from dataclasses import dataclass


@dataclass
class ApiError(Exception):
    status: int
    code: str
    message: str
    retry_after: int | None = None


class CacheUnavailable(Exception):
    pass


class AuthUnavailable(Exception):
    pass


class EmailConflict(Exception):
    pass


class DataUnavailable(Exception):
    pass


class ReportConflict(Exception):
    pass


class UsernameConflict(Exception):
    pass


class StorageUnavailable(Exception):
    pass
