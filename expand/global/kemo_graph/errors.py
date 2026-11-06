"""Response-safe errors for the Kemo Graph sidecar extension."""

from __future__ import annotations

import re
from typing import Any


_CATEGORY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,159}$")
_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_NON_RETRYABLE_CODES = frozenset(
    {
        "CONTENT_CONFLICT",
        "FILE_TOO_LARGE",
        "INVALID_PARAM",
        "INVALID_PATH",
        "NOT_FOUND",
        "NOT_INITIALIZED",
        "RESTART_FORBIDDEN",
        "STORE_ACCESS_DENIED",
        "STORE_INVALID",
        "STORE_NOT_INITIALIZED",
        "UNSUPPORTED_FORMAT",
        "UPDATE_BLOCKED",
        "UPDATE_FORBIDDEN",
    }
)


def _bounded_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed < minimum:
        return None
    return min(parsed, maximum)


def _http_category(status: int) -> str:
    if status in {401, 403}:
        return "auth_error"
    if status == 408:
        return "timeout"
    if status == 429:
        return "rate_limit"
    if status >= 500 or status == 425:
        return "upstream_error"
    if 400 <= status < 500:
        return "request_error"
    return "graph_api_error"


def error_metadata(exc: BaseException) -> dict[str, Any]:
    """Return bounded retry metadata without serializing arbitrary exception state."""

    result: dict[str, Any] = {}
    category = getattr(exc, "category", None)
    if isinstance(category, str) and _CATEGORY_RE.fullmatch(category.strip()):
        result["category"] = category.strip()
    status_code = _bounded_int(
        getattr(exc, "status_code", None),
        minimum=100,
        maximum=599,
    )
    if status_code is not None:
        result["status_code"] = status_code
    retryable = getattr(exc, "retryable", None)
    if isinstance(retryable, bool):
        result["retryable"] = retryable
    retry_after_ms = _bounded_int(
        getattr(exc, "retry_after_ms", None),
        minimum=0,
        maximum=120_000,
    )
    if retry_after_ms is not None:
        result["retry_after_ms"] = retry_after_ms
    return result


class GraphExpandError(RuntimeError):
    """A validation or local operation error safe to return to the caller."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "graph_expand_error",
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(str(message or "kemo-graph 拓展操作失败")[:1000])
        normalized_category = str(category or "").strip()
        self.category = (
            normalized_category
            if _CATEGORY_RE.fullmatch(normalized_category)
            else "graph_expand_error"
        )
        self.status_code = _bounded_int(
            status_code,
            minimum=100,
            maximum=599,
        )
        self.retryable_declared = True
        self.retryable = retryable is True
        self.retry_after_ms = _bounded_int(
            retry_after_ms,
            minimum=0,
            maximum=120_000,
        )


class GraphAPIError(GraphExpandError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        category: str | None = None,
        retryable: bool | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        self.status = _bounded_int(status, minimum=100, maximum=599) or 500
        self.code = str(code or "HTTP_ERROR")[:128]
        safe_message = str(message or "kemo-graph 请求失败")[:500]
        normalized_code = self.code.strip().upper()
        if normalized_code in _NON_RETRYABLE_CODES:
            resolved_retryable = False
        elif (
            400 <= self.status < 500
            and self.status not in {408, 425, 429}
        ):
            resolved_retryable = False
        elif isinstance(retryable, bool):
            resolved_retryable = retryable
        elif self.status in _RETRYABLE_HTTP_STATUSES:
            resolved_retryable = True
        else:
            resolved_retryable = False
        super().__init__(
            f"kemo-graph HTTP {self.status} {self.code}: {safe_message}",
            category=category or _http_category(self.status),
            status_code=self.status,
            retryable=resolved_retryable,
            retry_after_ms=retry_after_ms,
        )
