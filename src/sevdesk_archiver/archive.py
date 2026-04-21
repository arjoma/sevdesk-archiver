"""
Local archive for SevDesk documents.

Idempotent and self-correcting. Each archive run:
- Scans the target directory for existing JSON sidecars and indexes them by SevDesk id.
- Fetches documents in monthly chunks between after_date and end_date.
- For each document, writes/updates the JSON sidecar and downloads the PDF if missing.
- Writes a manifest.json at the end summarizing the archive for the HTML viewer.

Filename scheme: ``<prefix>-<yyyymmdd>-<number>-<receiver>.pdf`` (and matching .json).
The filename is frozen once written: if SevDesk metadata later changes (receiver renamed,
number edited), the JSON is refreshed but the filename is not renamed.
"""

import json
import logging
import os
import re
import shutil
import time
from calendar import monthrange
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Tuple

import requests

from .exceptions import DocumentNotFoundError, RateLimitExceededError
from .utils import format_date

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_INDEX_TEMPLATE_PATH = os.path.join(_TEMPLATES_DIR, "archive_index.html")
_SERVE_PY_TEMPLATE_PATH = os.path.join(_TEMPLATES_DIR, "serve.py")
_SERVE_SH_TEMPLATE_PATH = os.path.join(_TEMPLATES_DIR, "serve-archive.sh")
_LOGO_TEMPLATE_PATH = os.path.join(_TEMPLATES_DIR, "logo.png")

# Portable filenames in the archive itself (plain, no "archive_" prefix).
SERVE_PY_NAME = "serve.py"
SERVE_SH_NAME = "serve-archive.sh"
LOGO_FILENAME = "logo.png"

logger = logging.getLogger(__name__)

ARCHIVE_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
INDEX_FILENAME = "index.html"
FILES_SUBDIR = "files"

TYPE_PREFIX = {"Invoice": "inv", "CreditNote": "cn", "Voucher": "vou"}

INVOICE_TYPE_DE = {
    "RE": "Rechnung",
    "SR": "Stornorechnung",
    "MA": "Mahnung",
    "TR": "Teilrechnung",
    "ER": "Erinnerung",
    "WKR": "Wiederkehrende Rechnung",
    "KB": "Kassenbon",
}

STATUS_DE = {
    "100": "Entwurf",
    "200": "Versendet",
    "300": "Mahnung",
    "750": "Teilweise bezahlt",
    "1000": "Bezahlt",
}

VOUCHER_STATUS_DE = {
    "50": "Entwurf",
    "100": "Offen",
    "1000": "Bezahlt",
}

_RECEIVER_MAX_LEN = 50
_UNSAFE_FILENAME_CHARS = re.compile(r'[:*?"<>|]')
_WHITESPACE = re.compile(r"\s+")


def _date_field_for(doc_type: str) -> str:
    return {
        "Invoice": "invoiceDate",
        "CreditNote": "creditNoteDate",
        "Voucher": "voucherDate",
    }[doc_type]


def _number_for(doc: Dict[str, Any], doc_type: str) -> str:
    if doc_type == "Invoice":
        return str(doc.get("invoiceNumber") or f"INV-{doc.get('id', '')}")
    if doc_type == "CreditNote":
        return str(doc.get("creditNoteNumber") or f"CN-{doc.get('id', '')}")
    if doc_type == "Voucher":
        return str(
            doc.get("voucherNumber")
            or doc.get("description")
            or f"VOU-{doc.get('id', '')}"
        )
    return str(doc.get("id", ""))


def _receiver_for(doc: Dict[str, Any], doc_type: str) -> str:
    """Best human-readable name for the counter-party.

    SevDesk Contact objects have ``name`` for companies; individuals leave that
    null and use ``surename`` (given name — SevDesk's spelling) + ``familyname``.
    ``objectName`` is the API type literal ("Contact") and is never useful.
    """
    party_key = "supplier" if doc_type == "Voucher" else "contact"
    party = doc.get(party_key) or {}
    if not isinstance(party, dict):
        return ""
    name = str(party.get("name") or "").strip()
    if name:
        return name
    sur = str(party.get("surename") or "").strip()
    fam = str(party.get("familyname") or "").strip()
    combined = f"{sur} {fam}".strip()
    return combined


def _clean_for_filename(value: str, max_len: int = _RECEIVER_MAX_LEN) -> str:
    """Make value safe for a filename segment. Keeps umlauts. Empty in → empty out."""
    if not value or not value.strip():
        return ""
    value = _UNSAFE_FILENAME_CHARS.sub("", value)
    value = value.replace("/", "_").replace("\\", "_")
    value = "".join(c for c in value if c.isprintable())
    value = _WHITESPACE.sub("_", value)
    value = value.strip("._-")
    if len(value) > max_len:
        value = value[:max_len].rstrip("._-")
    return value


def generate_archive_filename(doc: Dict[str, Any], doc_type: str) -> str:
    """Build ``<prefix>-<yyyymmdd>-<number>-<receiver>.pdf``."""
    prefix = TYPE_PREFIX.get(doc_type, "doc")
    date_iso = format_date(doc.get(_date_field_for(doc_type)))
    date_compact = date_iso.replace("-", "") if date_iso else "00000000"
    number = _clean_for_filename(_number_for(doc, doc_type), max_len=60)
    receiver = _clean_for_filename(_receiver_for(doc, doc_type))
    parts = [prefix, date_compact, number]
    if receiver:
        parts.append(receiver)
    stem = "-".join(p for p in parts if p)
    return f"{stem}.pdf"


def _sidecar_path(pdf_path: str) -> str:
    return pdf_path[:-4] + ".json" if pdf_path.endswith(".pdf") else pdf_path + ".json"


def _files_dir(target_dir: str) -> str:
    return os.path.join(target_dir, FILES_SUBDIR)


def migrate_flat_to_subdir(target_dir: str) -> int:
    """Move pre-existing archive files from target_dir root into the ``files/`` subdir.

    Idempotent: only moves ``.pdf`` / ``.json`` files that live directly in the
    archive root (not inside ``files/``). Skips manifest / index / directories
    and refuses to overwrite a destination that already exists.
    """
    if not os.path.isdir(target_dir):
        return 0
    files_dir = _files_dir(target_dir)
    os.makedirs(files_dir, exist_ok=True)
    moved = 0
    for name in os.listdir(target_dir):
        if name in (MANIFEST_FILENAME, INDEX_FILENAME, FILES_SUBDIR):
            continue
        src = os.path.join(target_dir, name)
        if not os.path.isfile(src):
            continue
        if not (name.endswith(".pdf") or name.endswith(".json")):
            continue
        dst = os.path.join(files_dir, name)
        if os.path.exists(dst):
            continue
        os.rename(src, dst)
        moved += 1
    return moved


def scan_existing(target_dir: str) -> Dict[str, Dict[str, Any]]:
    """Walk the archive's ``files/`` subdir for *.json sidecars.

    Returns id -> entry map. Each entry:
    ``{'json': path, 'pdf': path|None, 'doc_type': str, 'meta': dict}``.
    Unreadable or malformed sidecars are logged and skipped.
    """
    index: Dict[str, Dict[str, Any]] = {}
    files_dir = _files_dir(target_dir)
    if not os.path.isdir(files_dir):
        return index
    for name in os.listdir(files_dir):
        if not name.endswith(".json"):
            continue
        json_path = os.path.join(files_dir, name)
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Skipping malformed sidecar %s: %s", json_path, e)
            continue
        sev_id = meta.get("sevdesk_id")
        if not sev_id:
            continue
        pdf_path = json_path[:-5] + ".pdf"
        index[str(sev_id)] = {
            "json": json_path,
            "pdf": pdf_path if os.path.exists(pdf_path) else None,
            "doc_type": meta.get("type", "Invoice"),
            "meta": meta,
        }
    return index


def _write_sidecar(
    json_path: str,
    doc: Dict[str, Any],
    doc_type: str,
    pdf_filename: str,
) -> Dict[str, Any]:
    payload = {
        "archive_version": ARCHIVE_VERSION,
        "sevdesk_id": str(doc.get("id", "")),
        "type": doc_type,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "pdf_filename": pdf_filename,
        "document": doc,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return payload


def _sidecar_equivalent(meta: Dict[str, Any], doc: Dict[str, Any]) -> bool:
    return meta.get("document") == doc


def _iter_month_ranges(after_date: str, end_date: str) -> Iterator[Tuple[str, str]]:
    start = datetime.strptime(after_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if start > end:
        return
    cur = start.replace(day=1)
    while cur <= end:
        last_day = monthrange(cur.year, cur.month)[1]
        month_end = cur.replace(day=last_day)
        ms = max(cur, start)
        me = min(month_end, end)
        yield ms.strftime("%Y-%m-%d"), me.strftime("%Y-%m-%d")
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)


def _fetch_month(
    client, doc_type: str, month_start: str, month_end: str, status: Optional[str]
) -> List[Dict[str, Any]]:
    if doc_type == "Invoice":
        return client.get_invoices(
            status=status, limit=10000, after_date=month_start, end_date=month_end
        )
    if doc_type == "CreditNote":
        return client.get_credit_notes(
            status=status, limit=10000, after_date=month_start, end_date=month_end
        )
    if doc_type == "Voucher":
        return client.get_vouchers(
            status=status, limit=10000, after_date=month_start, end_date=month_end
        )
    raise ValueError(f"Unknown document type: {doc_type}")


MAX_RETRIES_PER_CALL = 3
RATE_LIMIT_DEFAULT_WAIT = 30
NETWORK_BACKOFF_BASE = 5


def _with_retry(
    thunk: Callable[..., Any],
    label: str,
    max_retries: int = MAX_RETRIES_PER_CALL,
) -> Generator[Dict[str, Any], None, Any]:
    """Call ``thunk()`` with backoff on rate-limits and transient network errors.

    Yields status event dicts so the caller can surface them live. Returns the
    final result via StopIteration.value — callers should use
    ``result = yield from _with_retry(...)``.
    """
    for attempt in range(max_retries + 1):
        try:
            return thunk()
        except RateLimitExceededError as e:
            if attempt >= max_retries:
                raise
            wait = e.retry_after or RATE_LIMIT_DEFAULT_WAIT
            yield {
                "type": "warning",
                "message": (
                    f"  ! {label}: rate-limited, sleeping {wait}s "
                    f"(retry {attempt + 1}/{max_retries})"
                ),
            }
            time.sleep(wait)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt >= max_retries:
                raise
            wait = NETWORK_BACKOFF_BASE * (2**attempt)
            yield {
                "type": "warning",
                "message": (
                    f"  ! {label}: network error ({type(e).__name__}); "
                    f"sleeping {wait}s (retry {attempt + 1}/{max_retries})"
                ),
            }
            time.sleep(wait)


def _is_draft(doc: Dict[str, Any], doc_type: str) -> bool:
    """Type-aware draft check.

    - Invoice / CreditNote: draft if status < 200 (100 = Entwurf).
    - Voucher: draft if status < 100 (50 = Entwurf; 100 = Offen is real).
    """
    raw = doc.get("status")
    if raw in (None, ""):
        return False
    try:
        s_int = int(str(raw))
    except (TypeError, ValueError):
        return False
    if doc_type == "Voucher":
        return s_int < 100
    return s_int < 200


def _status_label(doc: Dict[str, Any], doc_type: str) -> str:
    raw = str(doc.get("status", ""))
    if doc_type == "Voucher":
        return VOUCHER_STATUS_DE.get(raw, raw)
    return STATUS_DE.get(raw, raw)


def _manifest_entry(
    doc: Dict[str, Any], doc_type: str, pdf_filename: str
) -> Dict[str, Any]:
    date_iso = format_date(doc.get(_date_field_for(doc_type)))
    year, month = None, None
    if date_iso and len(date_iso) >= 7:
        try:
            year = int(date_iso[0:4])
            month = int(date_iso[5:7])
        except ValueError:
            pass

    invoice_type = doc.get("invoiceType")
    entry = {
        "id": str(doc.get("id", "")),
        "type": doc_type,
        "pdf": f"{FILES_SUBDIR}/{pdf_filename}",
        "json": f"{FILES_SUBDIR}/{pdf_filename[:-4]}.json",
        "date": date_iso,
        "year": year,
        "month": month,
        "number": _number_for(doc, doc_type),
        "receiver": _receiver_for(doc, doc_type),
        "gross": doc.get("sumGross"),
        "net": doc.get("sumNet"),
        "tax": doc.get("sumTax"),
        "currency": doc.get("currency"),
        "status": str(doc.get("status", "")),
        "status_label": _status_label(doc, doc_type),
        "invoice_type": invoice_type,
        "invoice_type_label": (
            INVOICE_TYPE_DE.get(str(invoice_type), str(invoice_type))
            if invoice_type
            else None
        ),
        "pay_date": format_date(doc.get("payDate")) or None,
        "delivery_date": format_date(doc.get("deliveryDate")) or None,
        "delivery_date_until": format_date(doc.get("deliveryDateUntil")) or None,
        "no_pdf": bool(doc.get("_no_pdf")),
    }
    return entry


def write_manifest(
    target_dir: str,
    entries: List[Dict[str, Any]],
) -> str:
    """Write manifest.json with all archive entries. Returns path."""
    manifest_path = os.path.join(target_dir, MANIFEST_FILENAME)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "archive_version": ARCHIVE_VERSION,
        "count": len(entries),
        "entries": entries,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return manifest_path


def verify_archive(target_dir: str) -> Dict[str, Any]:
    """Cross-check manifest.json against the actual ``files/`` directory.

    Returns a dict with:
      - ``manifest_count``: number of entries in manifest
      - ``files_on_disk``: set of bare filenames in ``files/``
      - ``missing_pdf``: manifest entries whose PDF is gone
        (skipped for entries flagged ``no_pdf``)
      - ``missing_json``: manifest entries whose JSON sidecar is gone
      - ``orphan_pdf``: PDF files on disk with no matching manifest entry
      - ``orphan_json``: JSON sidecars on disk with no matching manifest entry
      - ``no_pdf_count``: how many entries are flagged no_pdf (informational)
      - ``manifest_path``: full path for reporting
      - ``errors``: list of high-level errors (e.g. manifest missing)
    """
    out: Dict[str, Any] = {
        "manifest_path": os.path.join(target_dir, MANIFEST_FILENAME),
        "manifest_count": 0,
        "files_on_disk": set(),
        "missing_pdf": [],
        "missing_json": [],
        "orphan_pdf": [],
        "orphan_json": [],
        "no_pdf_count": 0,
        "errors": [],
    }

    if not os.path.exists(out["manifest_path"]):
        out["errors"].append("manifest.json not found")
        return out

    try:
        with open(out["manifest_path"], "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        out["errors"].append(f"manifest.json unreadable: {e}")
        return out

    files_dir = _files_dir(target_dir)
    on_disk = set(os.listdir(files_dir)) if os.path.isdir(files_dir) else set()
    out["files_on_disk"] = on_disk

    referenced_pdfs: set = set()
    referenced_jsons: set = set()
    entries = manifest.get("entries", [])
    out["manifest_count"] = len(entries)

    for entry in entries:
        pdf_rel = entry.get("pdf") or ""
        json_rel = entry.get("json") or ""
        pdf_base = os.path.basename(pdf_rel)
        json_base = os.path.basename(json_rel)
        if pdf_base:
            referenced_pdfs.add(pdf_base)
        if json_base:
            referenced_jsons.add(json_base)

        if entry.get("no_pdf"):
            out["no_pdf_count"] += 1
        elif pdf_base and pdf_base not in on_disk:
            out["missing_pdf"].append(pdf_rel)

        if json_base and json_base not in on_disk:
            out["missing_json"].append(json_rel)

    for name in on_disk:
        if name.endswith(".pdf") and name not in referenced_pdfs:
            out["orphan_pdf"].append(name)
        elif name.endswith(".json") and name not in referenced_jsons:
            out["orphan_json"].append(name)

    return out


def install_index_html(target_dir: str) -> Optional[str]:
    """Copy the shipped index.html template into target_dir (overwrite on update)."""
    if not os.path.exists(_INDEX_TEMPLATE_PATH):
        return None
    dest = os.path.join(target_dir, INDEX_FILENAME)
    shutil.copyfile(_INDEX_TEMPLATE_PATH, dest)
    return dest


def install_logo(target_dir: str) -> Optional[str]:
    """Copy the shipped logo.png into target_dir (overwrite on update)."""
    if not os.path.exists(_LOGO_TEMPLATE_PATH):
        return None
    dest = os.path.join(target_dir, LOGO_FILENAME)
    shutil.copyfile(_LOGO_TEMPLATE_PATH, dest)
    return dest


def install_serve_scripts(target_dir: str) -> List[str]:
    """Copy the standalone serve.py + serve-archive.sh into target_dir.

    These let the user open the archive without having sevdesk-archiver
    installed: ``./serve-archive.sh`` (or ``python3 serve.py``) from inside
    the archive folder is enough. Stdlib only, no pip install required.
    """
    written: List[str] = []
    for src, name in (
        (_SERVE_PY_TEMPLATE_PATH, SERVE_PY_NAME),
        (_SERVE_SH_TEMPLATE_PATH, SERVE_SH_NAME),
    ):
        if not os.path.exists(src):
            continue
        dest = os.path.join(target_dir, name)
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o755)
        written.append(dest)
    return written


def _default_after() -> str:
    """Default start date: 1st of previous month."""
    today = datetime.now()
    first_of_this = today.replace(day=1)
    last_of_prev = first_of_this.replace(day=1)
    if first_of_this.month == 1:
        first_of_prev = last_of_prev.replace(year=first_of_this.year - 1, month=12)
    else:
        first_of_prev = last_of_prev.replace(month=first_of_this.month - 1)
    return first_of_prev.strftime("%Y-%m-%d")


def archive(
    client,
    target_dir: str,
    after_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    include_credit_notes: bool = True,
    include_vouchers: bool = False,
    dry_run: bool = False,
) -> Iterator[Dict[str, Any]]:
    """Archive all matching documents into target_dir.

    Yields event dicts ``{"type": ..., "message": ...}``.
    Self-correcting: writes whichever of (PDF, JSON) is missing; skips when both
    are present and the JSON already matches the current document metadata.
    """
    os.makedirs(target_dir, exist_ok=True)
    files_dir = _files_dir(target_dir)
    os.makedirs(files_dir, exist_ok=True)

    effective_after = after_date or _default_after()
    effective_end = end_date or datetime.now().strftime("%Y-%m-%d")

    yield {
        "type": "info",
        "message": f"Archive target: {target_dir}",
    }
    yield {
        "type": "info",
        "message": f"Date range: {effective_after} .. {effective_end}",
    }

    moved = migrate_flat_to_subdir(target_dir)
    if moved:
        yield {
            "type": "info",
            "message": f"Migrated {moved} file(s) from archive root into {FILES_SUBDIR}/",
        }

    index = scan_existing(target_dir)
    yield {
        "type": "info",
        "message": f"Indexed {len(index)} existing sidecar(s).",
    }

    doc_types = ["Invoice"]
    if include_credit_notes:
        doc_types.append("CreditNote")
    if include_vouchers:
        doc_types.append("Voucher")

    totals = {
        "downloaded": 0,
        "refreshed": 0,
        "skipped": 0,
        "no_pdf": 0,
        "errors": 0,
        "seen": 0,
    }
    manifest_entries: List[Dict[str, Any]] = []
    seen_ids: set = set()

    for month_start, month_end in _iter_month_ranges(effective_after, effective_end):
        yield {
            "type": "info",
            "message": f"=== Month {month_start[:7]} ===",
        }
        for doc_type in doc_types:
            try:
                docs = yield from _with_retry(
                    lambda dt=doc_type,
                    ms=month_start,
                    me=month_end,
                    st=status: _fetch_month(client, dt, ms, me, st),
                    label=f"fetch {doc_type} {month_start[:7]}",
                )
            except Exception as e:
                yield {
                    "type": "error",
                    "message": f"Fetch failed for {doc_type} {month_start[:7]}: {e}",
                }
                totals["errors"] += 1
                continue

            before = len(docs)
            docs = [d for d in docs if not _is_draft(d, doc_type)]
            drafts_skipped = before - len(docs)

            if not docs and drafts_skipped == 0:
                continue

            if drafts_skipped:
                yield {
                    "type": "info",
                    "message": f"  {doc_type}: skipped {drafts_skipped} draft(s)",
                }

            if not docs:
                continue

            yield {
                "type": "info",
                "message": f"  {doc_type}: {len(docs)} document(s)",
            }

            for doc in docs:
                totals["seen"] += 1
                sev_id = str(doc.get("id", ""))
                if not sev_id:
                    continue
                if sev_id in seen_ids:
                    continue
                seen_ids.add(sev_id)

                existing = index.get(sev_id)
                pdf_filename = (
                    os.path.basename(existing["pdf"])
                    if existing and existing.get("pdf")
                    else None
                ) or (
                    os.path.basename(existing["json"]).replace(".json", ".pdf")
                    if existing
                    else generate_archive_filename(doc, doc_type)
                )
                pdf_path = os.path.join(files_dir, pdf_filename)
                json_path = _sidecar_path(pdf_path)

                need_pdf = not os.path.exists(pdf_path)
                need_json = not os.path.exists(json_path) or (
                    existing is not None
                    and not _sidecar_equivalent(existing.get("meta", {}), doc)
                )

                if not need_pdf and not need_json:
                    totals["skipped"] += 1
                    manifest_entries.append(
                        _manifest_entry(doc, doc_type, pdf_filename)
                    )
                    continue

                if dry_run:
                    action = []
                    if need_pdf:
                        action.append("PDF")
                    if need_json:
                        action.append("JSON")
                    yield {
                        "type": "dry_run",
                        "message": f"  [dry-run] would write {'+'.join(action)}: {pdf_filename}",
                    }
                    manifest_entries.append(
                        _manifest_entry(doc, doc_type, pdf_filename)
                    )
                    continue

                if need_pdf:
                    try:
                        content, _src_name = yield from _with_retry(
                            lambda sid=sev_id, dt=doc_type: client.download_document(
                                sid, object_type=dt
                            ),
                            label=f"download {sev_id}",
                        )
                        with open(pdf_path, "wb") as f:
                            f.write(content)
                        totals["downloaded"] += 1
                        yield {
                            "type": "success",
                            "message": f"  + {pdf_filename} ({len(content)} bytes)",
                        }
                    except DocumentNotFoundError:
                        totals["no_pdf"] += 1
                        doc["_no_pdf"] = True
                        yield {
                            "type": "info",
                            "message": f"  ⊘ no PDF on SevDesk for {doc_type} {sev_id}",
                        }
                    except Exception as e:
                        totals["errors"] += 1
                        yield {
                            "type": "error",
                            "message": f"  ! PDF download failed for {sev_id}: {e}",
                        }
                if need_json:
                    try:
                        _write_sidecar(json_path, doc, doc_type, pdf_filename)
                        if not need_pdf:
                            totals["refreshed"] += 1
                            yield {
                                "type": "info",
                                "message": f"  ~ refreshed metadata: {os.path.basename(json_path)}",
                            }
                    except OSError as e:
                        totals["errors"] += 1
                        yield {
                            "type": "error",
                            "message": f"  ! sidecar write failed for {sev_id}: {e}",
                        }

                manifest_entries.append(_manifest_entry(doc, doc_type, pdf_filename))

    orphans = [sid for sid in index if sid not in seen_ids]
    if orphans:
        yield {
            "type": "info",
            "message": (
                f"{len(orphans)} existing entr(y/ies) outside current range — "
                "keeping and including in manifest."
            ),
        }
        for sid in orphans:
            entry = index[sid]
            meta = entry.get("meta", {})
            doc = meta.get("document") or {}
            doc_type = entry.get("doc_type", "Invoice")
            pdf_filename = meta.get("pdf_filename") or os.path.basename(
                entry["json"].replace(".json", ".pdf")
            )
            manifest_entries.append(_manifest_entry(doc, doc_type, pdf_filename))

    if not dry_run:
        try:
            path = write_manifest(target_dir, manifest_entries)
            yield {
                "type": "info",
                "message": f"Wrote manifest: {os.path.basename(path)} ({len(manifest_entries)} entr(y/ies))",
            }
        except OSError as e:
            yield {"type": "error", "message": f"Manifest write failed: {e}"}

        try:
            idx = install_index_html(target_dir)
            if idx:
                yield {
                    "type": "info",
                    "message": f"Installed viewer: {os.path.basename(idx)}",
                }
        except OSError as e:
            yield {"type": "error", "message": f"Index write failed: {e}"}

        try:
            logo = install_logo(target_dir)
            if logo:
                yield {
                    "type": "info",
                    "message": f"Installed logo: {os.path.basename(logo)}",
                }
        except OSError as e:
            yield {"type": "error", "message": f"Logo write failed: {e}"}

        try:
            scripts = install_serve_scripts(target_dir)
            if scripts:
                yield {
                    "type": "info",
                    "message": "Installed serve scripts: "
                    + ", ".join(os.path.basename(p) for p in scripts),
                }
        except OSError as e:
            yield {"type": "error", "message": f"Serve-script install failed: {e}"}

    yield {
        "type": "info",
        "message": (
            f"Done. Seen={totals['seen']} Downloaded={totals['downloaded']} "
            f"Refreshed={totals['refreshed']} Skipped={totals['skipped']} "
            f"NoPdf={totals['no_pdf']} Errors={totals['errors']}"
        ),
    }
