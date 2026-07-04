"""Tests for SevDeskClient against a mocked requests session.

Hermetic: no network. The client's retry session is replaced with a MagicMock
that returns canned responses, so pagination, date filtering, download-response
parsing, and error mapping are exercised exactly as the real API would drive
them.
"""

import base64
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import HTTPError, RetryError

from sevdesk_archiver.exceptions import DocumentNotFoundError, RateLimitExceededError
from sevdesk_archiver.sevdesk import SevDeskClient
from sevdesk_archiver.utils import parse_retry_after


def _response(
    json_data=None,
    status_code=200,
    content=b"",
    content_type=None,
    headers=None,
):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = dict(headers or {})
    if content_type:
        resp.headers["Content-Type"] = content_type
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    if status_code >= 400:
        resp.raise_for_status.side_effect = HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


def _client_with(session):
    client = SevDeskClient(api_token="test-token")
    client.session = session
    return client


def _invoice(_id, date="2026-02-10"):
    return {"id": str(_id), "invoiceDate": date, "invoiceNumber": f"RE-{_id}"}


class TestFetchPagination(unittest.TestCase):
    def test_single_page(self):
        session = MagicMock()
        session.get.return_value = _response({"objects": [_invoice(1), _invoice(2)]})
        client = _client_with(session)

        result = client.get_invoices(limit=10000)

        self.assertEqual([o["id"] for o in result], ["1", "2"])
        self.assertEqual(session.get.call_count, 1)

    def test_paginates_with_offset_until_short_page(self):
        page1 = [_invoice(i) for i in range(100)]
        page2 = [_invoice(i) for i in range(100, 137)]
        session = MagicMock()
        session.get.side_effect = [
            _response({"objects": page1}),
            _response({"objects": page2}),
        ]
        client = _client_with(session)

        result = client.get_invoices(limit=10000)

        self.assertEqual(len(result), 137)
        self.assertEqual(session.get.call_count, 2)
        offsets = [c.kwargs["params"]["offset"] for c in session.get.call_args_list]
        self.assertEqual(offsets, [0, 100])
        # page size is capped at 100 regardless of the overall limit
        limits = [c.kwargs["params"]["limit"] for c in session.get.call_args_list]
        self.assertEqual(limits, [100, 100])

    def test_stops_on_empty_page(self):
        session = MagicMock()
        session.get.side_effect = [
            _response({"objects": [_invoice(i) for i in range(100)]}),
            _response({"objects": []}),
        ]
        client = _client_with(session)

        result = client.get_invoices(limit=10000)

        self.assertEqual(len(result), 100)
        self.assertEqual(session.get.call_count, 2)

    def test_respects_overall_limit(self):
        session = MagicMock()
        session.get.return_value = _response(
            {"objects": [_invoice(i) for i in range(50)]}
        )
        client = _client_with(session)

        result = client.get_invoices(limit=50)

        self.assertEqual(len(result), 50)
        self.assertEqual(session.get.call_count, 1)

    def test_status_and_embed_params_are_sent(self):
        session = MagicMock()
        session.get.return_value = _response({"objects": []})
        client = _client_with(session)

        client.get_invoices(status="200")

        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["status"], "200")
        self.assertEqual(params["embed"], "contact")

    def test_vouchers_embed_supplier(self):
        session = MagicMock()
        session.get.return_value = _response({"objects": []})
        client = _client_with(session)

        client.get_vouchers()

        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["embed"], "supplier")


class TestFetchDateFiltering(unittest.TestCase):
    def test_server_window_is_widened_by_one_day(self):
        session = MagicMock()
        session.get.return_value = _response({"objects": []})
        client = _client_with(session)

        client.get_invoices(after_date="2026-02-01", end_date="2026-02-28")

        params = session.get.call_args.kwargs["params"]
        start = int(
            datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp()
        )
        end = int(datetime(2026, 2, 28, tzinfo=timezone.utc).timestamp())
        self.assertEqual(params["startDate"], start - 86400)
        self.assertEqual(params["endDate"], end + 86400)

    def test_client_side_filter_is_strict(self):
        docs = [
            _invoice(1, date="2026-01-31"),  # before window
            _invoice(2, date="2026-02-01"),  # first day (inclusive)
            _invoice(3, date="2026-02-15T12:00:00+01:00"),
            _invoice(4, date="2026-02-28"),  # last day (inclusive)
            _invoice(5, date="2026-03-01"),  # after window
        ]
        session = MagicMock()
        session.get.return_value = _response({"objects": docs})
        client = _client_with(session)

        result = client.get_invoices(after_date="2026-02-01", end_date="2026-02-28")

        self.assertEqual([o["id"] for o in result], ["2", "3", "4"])

    def test_documents_without_date_are_dropped_when_filtering(self):
        """Current (documented) behavior: the client-side date filter drops
        documents whose date field is missing or empty."""
        docs = [_invoice(1), {"id": "2", "invoiceNumber": "RE-2"}]
        session = MagicMock()
        session.get.return_value = _response({"objects": docs})
        client = _client_with(session)

        result = client.get_invoices(after_date="2026-02-01", end_date="2026-02-28")

        self.assertEqual([o["id"] for o in result], ["1"])

    def test_no_filter_without_date_range(self):
        docs = [_invoice(1), {"id": "2"}]
        session = MagicMock()
        session.get.return_value = _response({"objects": docs})
        client = _client_with(session)

        result = client.get_invoices()

        self.assertEqual(len(result), 2)


class TestDownloadDocument(unittest.TestCase):
    def test_direct_pdf_response(self):
        session = MagicMock()
        session.get.return_value = _response(
            content=b"%PDF-1.7 data", content_type="application/pdf"
        )
        client = _client_with(session)

        content, filename = client.download_document("42", object_type="Invoice")

        self.assertEqual(content, b"%PDF-1.7 data")
        self.assertEqual(filename, "invoice_42.pdf")
        url = session.get.call_args.args[0]
        self.assertIn("/Invoice/42/getPdf", url)

    def test_json_top_level_base64(self):
        payload = {
            "content": base64.b64encode(b"%PDF-x").decode(),
            "filename": "rechnung.pdf",
        }
        session = MagicMock()
        session.get.return_value = _response(
            json_data=payload, content_type="application/json"
        )
        client = _client_with(session)

        content, filename = client.download_document("7")

        self.assertEqual(content, b"%PDF-x")
        self.assertEqual(filename, "rechnung.pdf")

    def test_json_objects_base64_encoded(self):
        payload = {
            "objects": {
                "content": base64.b64encode(b"%PDF-y").decode(),
                "filename": "beleg.pdf",
                "base64Encoded": True,
            }
        }
        session = MagicMock()
        session.get.return_value = _response(
            json_data=payload, content_type="application/json"
        )
        client = _client_with(session)

        content, filename = client.download_document("8", object_type="Voucher")

        self.assertEqual(content, b"%PDF-y")
        self.assertEqual(filename, "beleg.pdf")
        url = session.get.call_args.args[0]
        self.assertIn("/Voucher/8/getPdf", url)

    def test_json_objects_plain_content(self):
        payload = {
            "objects": {
                "content": "plain text",
                "filename": "note.txt",
                "base64Encoded": False,
            }
        }
        session = MagicMock()
        session.get.return_value = _response(
            json_data=payload, content_type="application/json"
        )
        client = _client_with(session)

        content, filename = client.download_document("9")

        self.assertEqual(content, b"plain text")
        self.assertEqual(filename, "note.txt")

    def test_filename_is_sanitized(self):
        payload = {
            "content": base64.b64encode(b"x").decode(),
            "filename": "../../etc/passwd",
        }
        session = MagicMock()
        session.get.return_value = _response(
            json_data=payload, content_type="application/json"
        )
        client = _client_with(session)

        _, filename = client.download_document("1")

        self.assertNotIn("/", filename)
        self.assertNotIn("\\", filename)

    def test_404_raises_document_not_found(self):
        session = MagicMock()
        session.get.return_value = _response(status_code=404)
        client = _client_with(session)

        with self.assertRaises(DocumentNotFoundError) as ctx:
            client.download_document("42", object_type="Voucher")
        self.assertEqual(ctx.exception.object_id, "42")
        self.assertEqual(ctx.exception.object_type, "Voucher")

    def test_unexpected_json_shape_raises(self):
        session = MagicMock()
        session.get.return_value = _response(
            json_data={"weird": True}, content_type="application/json"
        )
        client = _client_with(session)

        with self.assertRaises(ValueError):
            client.download_document("1")


class TestErrorMapping(unittest.TestCase):
    def test_429_maps_to_rate_limit_with_retry_after(self):
        session = MagicMock()
        session.get.return_value = _response(
            status_code=429, headers={"Retry-After": "17"}
        )
        client = _client_with(session)

        with self.assertRaises(RateLimitExceededError) as ctx:
            client.get_invoices()
        self.assertEqual(ctx.exception.retry_after, 17)

    def test_retry_error_maps_to_rate_limit(self):
        """When the urllib3 retry adapter gives up, the client reports it as
        a rate-limit condition (no Retry-After known)."""
        session = MagicMock()
        session.get.side_effect = RetryError("too many retries")
        client = _client_with(session)

        with self.assertRaises(RateLimitExceededError) as ctx:
            client.get_invoices()
        self.assertIsNone(ctx.exception.retry_after)

    def test_download_429_maps_to_rate_limit(self):
        session = MagicMock()
        session.get.return_value = _response(
            status_code=429, headers={"Retry-After": "5"}
        )
        client = _client_with(session)

        with self.assertRaises(RateLimitExceededError) as ctx:
            client.download_document("1")
        self.assertEqual(ctx.exception.retry_after, 5)

    def test_other_http_error_raises_generic_exception(self):
        session = MagicMock()
        session.get.return_value = _response(status_code=500)
        client = _client_with(session)

        with self.assertRaises(Exception) as ctx:
            client.get_invoices()
        self.assertNotIsInstance(ctx.exception, RateLimitExceededError)
        self.assertIn("failed after retries", str(ctx.exception))

    def test_connection_error_propagates_for_caller_backoff(self):
        """Connection errors surface as the client's generic wrapper
        exception with the original chained as __cause__."""
        session = MagicMock()
        session.get.side_effect = ReqConnectionError("boom")
        client = _client_with(session)

        with self.assertRaises(Exception) as ctx:
            client.get_invoices()
        self.assertIsInstance(ctx.exception.__cause__, ReqConnectionError)


class TestParseRetryAfter(unittest.TestCase):
    def _resp_with(self, value):
        resp = MagicMock()
        resp.headers = {"Retry-After": value} if value is not None else {}
        return resp

    def test_integer_header(self):
        self.assertEqual(parse_retry_after(self._resp_with("30")), 30)

    def test_missing_header(self):
        self.assertIsNone(parse_retry_after(self._resp_with(None)))

    def test_non_numeric_header(self):
        self.assertIsNone(parse_retry_after(self._resp_with("Wed, 21 Oct")))

    def test_auth_header_is_set(self):
        client = SevDeskClient(api_token="tok-123")
        self.assertEqual(client.headers["Authorization"], "tok-123")


if __name__ == "__main__":
    unittest.main()
