import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Mapping, Optional, Protocol, cast

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class _HasHeaders(Protocol):
    @property
    def headers(self) -> Mapping[str, str]: ...


def parse_retry_after(response: _HasHeaders) -> Optional[int]:
    """Parse the Retry-After header into seconds, or None.

    Accepts any response object with a ``headers`` mapping (requests, httpx,
    ...). Handles both delay-seconds and HTTP-date forms (RFC 9110).
    """
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return None
    try:
        return max(0, int(retry_after))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(retry_after)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, int((when - datetime.now(timezone.utc)).total_seconds()))


def create_retry_session(
    retries: int = 5,
    backoff_factor: float = 2.0,
    status_forcelist: tuple = (429, 500, 502, 503, 504),
    allowed_methods: Optional[list] = None,
    session: Optional[requests.Session] = None,
) -> requests.Session:
    """Create or configure a requests Session with automatic retries.

    By default only idempotent methods are retried (urllib3's default set,
    which excludes POST/PATCH) — retrying a POST after a 5xx can create
    duplicates on the server. Pass ``allowed_methods`` to override.
    """
    session = session or requests.Session()

    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=(
            Retry.DEFAULT_ALLOWED_METHODS if allowed_methods is None else allowed_methods
        ),
        # Return the final response instead of raising RetryError, so callers
        # see the real status code (429 with Retry-After vs. a persistent 5xx)
        # rather than every exhausted retry looking like a rate limit.
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class SecureRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that ensures the log file has 0o600 permissions."""

    def _open(self):
        flags = os.O_WRONLY | os.O_CREAT
        if "a" in self.mode:
            flags |= os.O_APPEND
        elif "w" in self.mode:
            flags |= os.O_TRUNC

        try:
            fd = os.open(self.baseFilename, flags, 0o600)
            return cast(Any, os.fdopen(fd, self.mode, encoding=self.encoding))
        except OSError:
            stream = cast(Any, super()._open())
            try:
                os.chmod(self.baseFilename, 0o600)
            except OSError:
                pass
            return stream


def setup_logging(
    log_file: Optional[str] = None,
    level=logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
    console: bool = True,
):
    """Configure root logging. Console by default, plus file if log_file is given."""
    logger = logging.getLogger()
    logger.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if console:
        if not any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, RotatingFileHandler)
            for h in logger.handlers
        ):
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.setLevel(level)
            logger.addHandler(console_handler)

    if log_file:
        if not any(
            isinstance(handler, RotatingFileHandler) for handler in logger.handlers
        ):
            file_handler = SecureRotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(level)
            logger.addHandler(file_handler)


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename: strip path separators and control chars."""
    if not filename:
        return "unknown_file"

    filename = filename.replace("/", "_").replace("\\", "_")
    filename = "".join(c for c in filename if c.isprintable())
    filename = filename.strip(" .")

    if not filename:
        return "unknown_file"

    return filename


def format_date(date_val: Any) -> str:
    """Normalize a date value to YYYY-MM-DD. Handles None, strings, datetime."""
    if date_val is None:
        return ""
    if hasattr(date_val, "strftime"):
        return date_val.strftime("%Y-%m-%d")
    s_val = str(date_val)
    if (
        len(s_val) >= 10
        and s_val[0:4].isdigit()
        and s_val[4] == "-"
        and s_val[5:7].isdigit()
        and s_val[7] == "-"
        and s_val[8:10].isdigit()
        and not s_val[10:11].isdigit()
    ):
        return s_val[:10]
    return s_val

