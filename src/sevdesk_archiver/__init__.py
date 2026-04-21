"""SevDesk Archiver — build a local, self-serving archive of SevDesk documents."""

from importlib.metadata import PackageNotFoundError, version

from . import archive
from .archive import verify_archive
from .exceptions import (
    AuthenticationError,
    DocumentNotFoundError,
    RateLimitExceededError,
    SevDeskArchiverError,
)
from .sevdesk import SevDeskClient

try:
    __version__ = version("sevdesk-archiver")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "SevDeskClient",
    "archive",
    "verify_archive",
    "SevDeskArchiverError",
    "AuthenticationError",
    "DocumentNotFoundError",
    "RateLimitExceededError",
    "__version__",
]
