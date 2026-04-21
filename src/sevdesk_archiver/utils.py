import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any, Optional, cast

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def parse_retry_after(response: requests.Response) -> Optional[int]:
    """Parse the Retry-After header. Returns seconds (int) or None."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return int(retry_after)
        except ValueError:
            pass
    return None


def create_retry_session(
    retries: int = 5,
    backoff_factor: float = 2.0,
    status_forcelist: tuple = (429, 500, 502, 503, 504),
    allowed_methods: Optional[list] = None,
    session: Optional[requests.Session] = None,
) -> requests.Session:
    """Create or configure a requests Session with automatic retries."""
    session = session or requests.Session()

    if allowed_methods is None:
        allowed_methods = ["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"]

    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=allowed_methods,
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
    ):
        return s_val[:10]
    return s_val


def update_env_var(key: str, value: str, path: str = ".env") -> None:
    """Update a single key in a .env file, adding it if absent. 0o600 perms."""
    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    updated = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}\n")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.writelines(new_lines)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
