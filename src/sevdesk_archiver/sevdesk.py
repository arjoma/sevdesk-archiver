import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, NoReturn, Optional, Tuple

from requests.exceptions import HTTPError, RequestException, RetryError

from .exceptions import DocumentNotFoundError, RateLimitExceededError
from .utils import create_retry_session, parse_retry_after, sanitize_filename

REQUEST_TIMEOUT = 30
logger = logging.getLogger(__name__)


class SevDeskClient:
    """Minimal SevDesk API client focused on fetching documents and PDFs."""

    def __init__(self, api_token: str, base_url: str = "https://my.sevdesk.de/api/v1"):
        self.api_token = api_token
        self.base_url = base_url
        self.headers = {
            "Authorization": self.api_token,
            "Content-Type": "application/json",
        }
        self.session = create_retry_session()
        self.session.headers.update(self.headers)

    def _handle_request_exception(self, e: RequestException, context: str) -> NoReturn:
        if isinstance(e, RetryError):
            raise RateLimitExceededError(service="SevDesk") from e
        if (
            isinstance(e, HTTPError)
            and e.response is not None
            and e.response.status_code == 429
        ):
            wait_time = parse_retry_after(e.response)
            raise RateLimitExceededError(
                service="SevDesk", retry_after=wait_time
            ) from e
        raise Exception(f"{context} failed after retries: {e}") from e

    def _fetch_objects(
        self,
        endpoint: str,
        date_field: str,
        status: Optional[str] = None,
        limit: int = 100,
        after_date: Optional[str] = None,
        end_date: Optional[str] = None,
        embed: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        all_objects: List[Dict[str, Any]] = []
        offset = 0
        page_limit = 100

        while len(all_objects) < limit:
            current_limit = min(page_limit, limit - len(all_objects))
            params: Dict[str, Any] = {"limit": current_limit, "offset": offset}
            if embed:
                params["embed"] = embed
            if status:
                params["status"] = status

            if after_date:
                try:
                    dt = datetime.strptime(after_date, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    params["startDate"] = int(dt.timestamp()) - 86400
                except ValueError:
                    pass

            if end_date:
                try:
                    dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    params["endDate"] = int(dt.timestamp()) + 86400
                except ValueError:
                    pass

            logger.debug(f"Fetching {endpoint} with params: {params}")

            try:
                response = self.session.get(
                    f"{self.base_url}/{endpoint}",
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()
                objects = data.get("objects", [])

                if not objects:
                    break

                all_objects.extend(objects)
                offset += len(objects)

                if len(objects) < current_limit:
                    break

            except RequestException as e:
                self._handle_request_exception(e, f"SevDesk {endpoint} fetch")

        if after_date or end_date:
            filtered_objects = []
            for obj in all_objects:
                obj_date = obj.get(date_field)
                if not obj_date:
                    continue
                obj_date_str = obj_date[:10]
                is_valid = True
                if after_date and obj_date_str < after_date:
                    is_valid = False
                if end_date and obj_date_str > end_date:
                    is_valid = False
                if is_valid:
                    filtered_objects.append(obj)
            return filtered_objects

        return all_objects

    def get_invoices(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        after_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self._fetch_objects(
            endpoint="Invoice",
            date_field="invoiceDate",
            status=status,
            limit=limit,
            after_date=after_date,
            end_date=end_date,
            embed="contact",
        )

    def get_credit_notes(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        after_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self._fetch_objects(
            endpoint="CreditNote",
            date_field="creditNoteDate",
            status=status,
            limit=limit,
            after_date=after_date,
            end_date=end_date,
            embed="contact",
        )

    def get_vouchers(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        after_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self._fetch_objects(
            endpoint="Voucher",
            date_field="voucherDate",
            status=status,
            limit=limit,
            after_date=after_date,
            end_date=end_date,
            embed="supplier",
        )

    def download_document(
        self, object_id: str, object_type: str = "Invoice"
    ) -> Tuple[bytes, str]:
        """Download the PDF/image for a given object. Returns (bytes, filename)."""
        endpoint_type = "Invoice"
        if object_type == "CreditNote":
            endpoint_type = "CreditNote"
        elif object_type == "Voucher":
            endpoint_type = "Voucher"

        try:
            response = self.session.get(
                f"{self.base_url}/{endpoint_type}/{object_id}/getPdf",
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            if response.headers.get("Content-Type") == "application/pdf":
                filename = sanitize_filename(f"{endpoint_type.lower()}_{object_id}.pdf")
                return response.content, filename

            data = response.json()
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                raise DocumentNotFoundError(endpoint_type, object_id) from e
            self._handle_request_exception(e, "SevDesk download_document")
        except RequestException as e:
            self._handle_request_exception(e, "SevDesk download_document")

        if "content" in data and "filename" in data:
            import base64

            file_bytes = base64.b64decode(data["content"])
            return file_bytes, sanitize_filename(data["filename"])

        if "objects" in data and isinstance(data["objects"], dict):
            obj = data["objects"]
            if "content" in obj and "filename" in obj:
                import base64

                if obj.get("base64Encoded", False):
                    file_bytes = base64.b64decode(obj["content"])
                else:
                    file_bytes = (
                        obj["content"].encode("utf-8")
                        if isinstance(obj["content"], str)
                        else obj["content"]
                    )
                return file_bytes, sanitize_filename(obj["filename"])

        raise ValueError(f"Unexpected response format from getPdf: {data.keys()}")

    def download_pdf(
        self, object_id: str, object_type: str = "Invoice"
    ) -> Tuple[bytes, str]:
        """Alias for download_document (backwards compatibility)."""
        return self.download_document(object_id, object_type)

    def get_invoice_by_number(self, invoice_number: str) -> Optional[Dict[str, Any]]:
        params: Dict[str, Any] = {"invoiceNumber": invoice_number, "limit": 1}
        try:
            response = self.session.get(
                f"{self.base_url}/Invoice",
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            objects = data.get("objects", [])
            return objects[0] if objects else None
        except RequestException as e:
            self._handle_request_exception(e, "SevDesk get_invoice_by_number")
            return None
