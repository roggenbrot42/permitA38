class ServiceError(Exception):
    """Raised by services; the API layer maps it to an HTTP error."""

    def __init__(self, status: int, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers
