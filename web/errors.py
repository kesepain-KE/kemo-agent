"""Web 业务层对 HTTP 适配器公开的稳定异常合同。"""

from __future__ import annotations


class WebServiceError(RuntimeError):
    code = "internal_error"
    status = 500
    headers: dict[str, str] | None = None


class InvalidRequestError(WebServiceError):
    code = "invalid_request"
    status = 400


class ProviderDiscoveryError(WebServiceError):
    code = "provider_discovery_failed"
    status = 502


class NotFoundError(WebServiceError):
    code = "not_found"
    status = 404


class ConflictError(WebServiceError):
    code = "conflict"
    status = 409


class TooManyChatsError(WebServiceError):
    code = "too_many_chats"
    status = 503

    def __init__(self, message: str, *, retry_after: float) -> None:
        super().__init__(message)
        self.headers = {"Retry-After": str(max(1, int(retry_after)))}

