"""Bounded HTTP client for the external kemo-graph service."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from errors import GraphAPIError, GraphExpandError
from registry import GraphConfig


MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _error_payload(body: bytes) -> tuple[str, str]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return "HTTP_ERROR", ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "HTTP_ERROR", ""
    return str(error.get("code") or "HTTP_ERROR"), str(error.get("message") or "")


def api_request(
    config: GraphConfig,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int | None = None,
    method: str | None = None,
    query: dict[str, Any] | None = None,
) -> Any:
    endpoint = "/" + str(path or "").strip().lstrip("/")
    url = config.base_url.rstrip("/") + endpoint
    if query:
        encoded = urllib.parse.urlencode(
            {key: value for key, value in query.items() if value is not None}
        )
        if encoded:
            url = f"{url}?{encoded}"
    request_method = method or ("POST" if payload is not None else "GET")
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "kemo-agent-graph-sidecar/2.0",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=request_method,
    )
    handlers: list[Any] = [_RejectRedirects()]
    if url.startswith("https://"):
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with urllib.request.build_opener(*handlers).open(
            request,
            timeout=timeout or config.timeout_seconds,
        ) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read(8192)
        code, message = _error_payload(raw)
        raise GraphAPIError(exc.code, code, message or str(exc.reason)) from exc
    except urllib.error.URLError as exc:
        raise GraphExpandError(f"无法连接 kemo-graph：{type(exc.reason).__name__}") from exc
    except TimeoutError as exc:
        raise GraphExpandError("连接 kemo-graph 超时") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise GraphExpandError("kemo-graph 响应超过 16 MB 安全上限")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GraphExpandError("kemo-graph 没有返回有效 JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("ok"), bool):
        raise GraphExpandError("kemo-graph 响应不符合统一包络")
    if not value["ok"]:
        error = value.get("error") if isinstance(value.get("error"), dict) else {}
        raise GraphAPIError(
            200,
            str(error.get("code") or "UNKNOWN"),
            str(error.get("message") or ""),
        )
    return value.get("data")


def verify_service(config: GraphConfig) -> Any:
    return api_request(config, "/status")
