"""Bounded HTTP client for the external kemo-graph service."""

from __future__ import annotations

import json
import mimetypes
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from errors import GraphAPIError, GraphExpandError
from registry import GraphConfig


MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_RETRY_AFTER_MS = 120_000


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


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


def _error_details(value: Any) -> dict[str, Any]:
    error = value.get("error") if isinstance(value, dict) else None
    if not isinstance(error, dict):
        return {"code": "HTTP_ERROR", "message": ""}
    details = error.get("details")
    sources = [error, details] if isinstance(details, dict) else [error]
    result: dict[str, Any] = {
        "code": str(
            error.get("code")
            or (details.get("code") if isinstance(details, dict) else None)
            or "HTTP_ERROR"
        )[:128],
        "message": str(
            error.get("message")
            or (details.get("message") if isinstance(details, dict) else None)
            or ""
        )[:500],
    }
    for source in sources:
        category = source.get("category") or source.get("type")
        if isinstance(category, str) and category.strip():
            result["category"] = category.strip()[:160]
            break
    for source in sources:
        if "status_code" not in source:
            continue
        status_code = _bounded_int(
            source.get("status_code"),
            minimum=100,
            maximum=599,
        )
        if status_code is not None:
            result["status_code"] = status_code
        break
    for source in sources:
        if "retryable" in source:
            # A malformed declaration must never become an opt-in to replay.
            result["retryable"] = source.get("retryable") is True
            break
    for source in sources:
        if "retry_after_ms" not in source:
            continue
        retry_after_ms = _bounded_int(
            source.get("retry_after_ms"),
            minimum=0,
            maximum=MAX_RETRY_AFTER_MS,
        )
        if retry_after_ms is not None:
            result["retry_after_ms"] = retry_after_ms
        break
    return result


def _error_payload(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return {"code": "HTTP_ERROR", "message": ""}
    return _error_details(payload)


def _retry_after_ms(headers: Any) -> int | None:
    if headers is None or not hasattr(headers, "get"):
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdecimal():
        seconds = _bounded_int(
            text,
            minimum=0,
            maximum=MAX_RETRY_AFTER_MS // 1000,
        )
        return seconds * 1000 if seconds is not None else None
    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    delay_ms = max(
        0,
        round((retry_at - datetime.now(timezone.utc)).total_seconds() * 1000),
    )
    return min(delay_ms, MAX_RETRY_AFTER_MS)


def _request_url(
    config: GraphConfig,
    path: str,
    query: dict[str, Any] | None = None,
) -> str:
    endpoint = "/" + str(path or "").strip().lstrip("/")
    url = config.base_url.rstrip("/") + endpoint
    if query:
        encoded = urllib.parse.urlencode(
            {key: value for key, value in query.items() if value is not None}
        )
        if encoded:
            url = f"{url}?{encoded}"
    return url


def _decode_response(body: bytes) -> Any:
    if len(body) > MAX_RESPONSE_BYTES:
        raise GraphExpandError("kemo-graph 响应超过 16 MB 安全上限")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GraphExpandError("kemo-graph 没有返回有效 JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("ok"), bool):
        raise GraphExpandError("kemo-graph 响应不符合统一包络")
    if not value["ok"]:
        error = _error_details(value)
        raise GraphAPIError(
            error.get("status_code") or 200,
            str(error.get("code") or "UNKNOWN"),
            str(error.get("message") or ""),
            category=error.get("category"),
            retryable=error.get("retryable"),
            retry_after_ms=error.get("retry_after_ms"),
        )
    return value.get("data")


def _perform_request(
    config: GraphConfig,
    request: urllib.request.Request,
    *,
    timeout: int | None = None,
) -> Any:
    handlers: list[Any] = [_RejectRedirects()]
    if request.full_url.startswith("https://"):
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with urllib.request.build_opener(*handlers).open(
            request,
            timeout=timeout or config.timeout_seconds,
        ) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read(8192)
        error = _error_payload(raw)
        retry_after_ms = _retry_after_ms(exc.headers)
        if retry_after_ms is None:
            retry_after_ms = error.get("retry_after_ms")
        raise GraphAPIError(
            exc.code,
            str(error.get("code") or "HTTP_ERROR"),
            str(error.get("message") or str(exc.reason)),
            category=error.get("category"),
            retryable=error.get("retryable"),
            retry_after_ms=retry_after_ms,
        ) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (ssl.CertificateError, ssl.SSLError)):
            raise GraphExpandError(
                "kemo-graph TLS 证书或握手校验失败",
                category="tls_error",
                retryable=False,
            ) from exc
        if isinstance(exc.reason, TimeoutError):
            raise GraphExpandError(
                "连接 kemo-graph 超时",
                category="connection_error",
                retryable=True,
            ) from exc
        raise GraphExpandError(
            f"无法连接 kemo-graph：{type(exc.reason).__name__}",
            category="connection_error",
            retryable=True,
        ) from exc
    except TimeoutError as exc:
        raise GraphExpandError(
            "连接 kemo-graph 超时",
            category="timeout",
            retryable=True,
        ) from exc
    except ConnectionError as exc:
        raise GraphExpandError(
            "连接 kemo-graph 中断",
            category="connection_error",
            retryable=True,
        ) from exc
    return _decode_response(body)


def api_request(
    config: GraphConfig,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int | None = None,
    method: str | None = None,
    query: dict[str, Any] | None = None,
) -> Any:
    url = _request_url(config, path, query)
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
    return _perform_request(config, request, timeout=timeout)


def api_upload_file(
    config: GraphConfig,
    path: str,
    file_path: str | Path,
    *,
    fields: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> Any:
    source = Path(file_path).expanduser()
    if not source.is_absolute():
        raise GraphExpandError("文件上传只接受绝对路径")
    if source.is_symlink():
        raise GraphExpandError("文件上传不接受符号链接")
    try:
        source = source.resolve(strict=True)
    except OSError as exc:
        raise GraphExpandError("待上传文件不存在或无法访问") from exc
    if not source.is_file():
        raise GraphExpandError("待上传路径不是普通文件")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise GraphExpandError("无法读取待上传文件状态") from exc
    if size > MAX_UPLOAD_BYTES:
        raise GraphExpandError("待上传文件超过 50 MB 上限")

    boundary = f"----kemo-agent-{uuid.uuid4().hex}"
    body = bytearray()

    def append(value: str | bytes) -> None:
        body.extend(value.encode("utf-8") if isinstance(value, str) else value)

    for name, value in sorted((fields or {}).items()):
        append(f"--{boundary}\r\n")
        append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        append(str(value))
        append("\r\n")

    suffix = source.suffix.casefold()
    ascii_name = source.name.encode("ascii", "ignore").decode("ascii").strip()
    if not ascii_name or Path(ascii_name).suffix.casefold() != suffix:
        ascii_name = f"upload{suffix}"
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
    encoded_name = urllib.parse.quote(source.name, safe="")
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    append(f"--{boundary}\r\n")
    append(
        'Content-Disposition: form-data; name="file"; '
        f'filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}\r\n'
    )
    append(f"Content-Type: {media_type}\r\n\r\n")
    total = 0
    try:
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise GraphExpandError("待上传文件超过 50 MB 上限")
                append(chunk)
    except OSError as exc:
        raise GraphExpandError("读取待上传文件失败") from exc
    append("\r\n")
    append(f"--{boundary}--\r\n")

    request = urllib.request.Request(
        _request_url(config, path, query),
        data=bytes(body),
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "kemo-agent-graph-sidecar/2.0",
        },
        method="POST",
    )
    return _perform_request(config, request, timeout=timeout)


def verify_service(config: GraphConfig) -> Any:
    return api_request(config, "/status")
