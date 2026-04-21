"""SevDesk Archiver — build a local, self-serving archive of SevDesk documents."""

from . import archive
from .archive import verify_archive
from .exceptions import (
    AuthenticationError,
    DocumentNotFoundError,
    RateLimitExceededError,
    SevDeskArchiverError,
)
from .sevdesk import SevDeskClient

__version__ = "0.1.0"

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
