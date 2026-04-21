class SevDeskArchiverError(Exception):
    """Base exception for sevdesk-archiver errors."""

    pass


class RateLimitExceededError(SevDeskArchiverError):
    """Raised when API rate limits are exhausted."""

    def __init__(
        self,
        message="API rate limit exceeded. Please try again later.",
        service="unknown",
        retry_after=None,
    ):
        self.service = service
        self.retry_after = retry_after
        super().__init__(
            f"[{service}] {message} (Retry-After: {retry_after}s)"
            if retry_after
            else f"[{service}] {message}"
        )


class AuthenticationError(SevDeskArchiverError):
    """Raised when authentication fails."""

    pass


class DocumentNotFoundError(SevDeskArchiverError):
    """Raised when a document/PDF does not exist on the remote (HTTP 404).

    Not really an error for archiving — many SevDesk vouchers are booked
    manually without an attached PDF. Callers can catch this to classify
    "no PDF available" distinctly from transient failures.
    """

    def __init__(self, object_type: str, object_id: str, service: str = "SevDesk"):
        self.object_type = object_type
        self.object_id = object_id
        self.service = service
        super().__init__(
            f"[{service}] {object_type} {object_id} has no attached document"
        )
