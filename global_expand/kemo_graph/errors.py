"""Response-safe errors for the Kemo Graph sidecar extension."""

from __future__ import annotations


class GraphExpandError(RuntimeError):
    """A validation or local operation error safe to return to the caller."""


class GraphAPIError(GraphExpandError):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = int(status)
        self.code = str(code or "HTTP_ERROR")[:128]
        safe_message = str(message or "kemo-graph 请求失败")[:500]
        super().__init__(f"kemo-graph HTTP {self.status} {self.code}: {safe_message}")
