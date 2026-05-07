class SlikError(Exception):
    pass


class CaptchaSolverError(SlikError):
    pass


class ScrapingError(SlikError):
    pass


class QuotaFullError(SlikError):
    def __init__(self, message: str, sessions: list | None = None):
        super().__init__(message)
        self.sessions = sessions or []


class InvalidCredentialsError(SlikError):
    pass


class RateLimitError(SlikError):
    pass


class NetworkError(SlikError):
    pass


class DatabaseError(SlikError):
    pass


class SchedulerError(SlikError):
    pass


class NotificationError(SlikError):
    pass
