"""HTTP response validation used only by the Chat transport."""
from __future__ import annotations

from typing import Any
from provider.schema import ProviderError

def _response_content_type(response: Any) -> str:
    headers = getattr(response, "headers", None)
    if headers is not None:
        get_content_type = getattr(headers, "get_content_type", None)
        if callable(get_content_type):
            try:
                return str(get_content_type() or "").strip().casefold()
            except (AttributeError, TypeError, ValueError):
                pass
        get_header = getattr(headers, "get", None)
        if callable(get_header):
            try:
                value = get_header("Content-Type")
                if value:
                    return str(value).split(";", 1)[0].strip().casefold()
            except (AttributeError, TypeError, ValueError):
                pass
    getheader = getattr(response, "getheader", None)
    if callable(getheader):
        try:
            value = getheader("Content-Type")
            if value:
                return str(value).split(";", 1)[0].strip().casefold()
        except (AttributeError, TypeError, ValueError):
            pass
    return ""


def _validate_chat_content_type(response: Any) -> str:
    content_type = _response_content_type(response)
    if content_type in {"text/html", "application/xhtml+xml"}:
        raise ProviderError(
            "Chat 接口返回 HTML 网页而非模型响应，请检查 base_url 与 /v1 路径是否正确",
            category="invalid_response",
            retryable=False,
        )
    return content_type


