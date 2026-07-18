"""无网络范围限制的 HTTP 请求与网页正文读取工具。"""

from __future__ import annotations

from html.parser import HTMLParser
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit


_MAX_RESPONSE_BYTES = 2_000_000
_VERIFY_SSL = os.environ.get("HTTP_VERIFY_SSL", "1").strip().casefold() not in {"0", "false", "no", "off"}
_READ_STRATEGIES = {"auto", "direct", "reader"}
_READER_SERVICES = {"auto", "jina", "markdown_new", "defuddle"}
_DEFAULT_WEB_READ_CHARS = 20_000
_MAX_WEB_READ_CHARS = 100_000
_DEFAULT_TIMEOUT = 15
_READER_URLS = {
    "jina": "https://r.jina.ai/{url}",
    "markdown_new": "https://markdown.new/{url}",
    "defuddle": "https://defuddle.md/{url}",
}
_WHITESPACE = re.compile(r"[ \t]{2,}")


def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


def _validate_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url 不能为空")
    value = url.strip()
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"仅支持有效的 http/https URL: {url}")
    return value


def _request_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    result = {"User-Agent": "kemo-agent/0.1"}
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("headers 的键和值必须是字符串")
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            raise ValueError("headers 不得包含换行符")
        result[name] = value
    return result


def _ssl_context() -> ssl.SSLContext | None:
    if _VERIFY_SSL:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _read_limited(response: Any) -> tuple[bytes, bool]:
    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    return raw[:_MAX_RESPONSE_BYTES], len(raw) > _MAX_RESPONSE_BYTES


def _open(
    url: str,
    *,
    method: str,
    data: bytes | None = None,
    headers: dict[str, Any] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[int, dict[str, str], bytes, bool]:
    value = _validate_url(url)
    request = urllib.request.Request(
        value,
        data=data,
        headers=_request_headers(headers),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            raw, truncated = _read_limited(response)
            return int(response.status), dict(response.headers), raw, truncated
    except urllib.error.HTTPError as exc:
        raw, truncated = _read_limited(exc)
        return int(exc.code), dict(exc.headers), raw, truncated
    except urllib.error.URLError as exc:
        raise ConnectionError(f"连接失败: {exc.reason}") from exc


def _header(headers: dict[str, str], name: str) -> str:
    expected = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == expected), "")


def _decode(raw: bytes, content_type: str) -> str:
    matched = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    candidates = [matched.group(1).strip('"\'')] if matched else []
    candidates.extend(("utf-8", "gbk"))
    for encoding in dict.fromkeys(candidates):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _body(raw: bytes, content_type: str) -> Any:
    text = _decode(raw, content_type)
    if "json" in content_type.casefold():
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def _run_get(
    url: str,
    headers: dict[str, Any] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    **_kw: Any,
) -> dict[str, Any]:
    status, response_headers, raw, truncated = _open(
        url, method="GET", headers=headers, timeout=timeout,
    )
    content_type = _header(response_headers, "content-type")
    return _result(
        200 <= status < 400,
        status=status,
        url=url,
        body=_body(raw, content_type),
        content_type=content_type,
        response_truncated=truncated,
    )


def _run_post(
    url: str,
    body: str = "",
    headers: dict[str, Any] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    **_kw: Any,
) -> dict[str, Any]:
    status, response_headers, raw, truncated = _open(
        url,
        method="POST",
        data=body.encode("utf-8"),
        headers=headers,
        timeout=timeout,
    )
    content_type = _header(response_headers, "content-type")
    return _result(
        200 <= status < 400,
        status=status,
        url=url,
        body=_body(raw, content_type),
        content_type=content_type,
        response_truncated=truncated,
    )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head", "nav", "footer", "header", "aside"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head", "nav", "footer", "header", "aside"}:
            self._skip_depth = max(0, self._skip_depth - 1)
        if self._skip_depth == 0 and tag in {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.text.append(data.strip())


def _extract_text(value: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(value)
    lines = []
    for line in " ".join(extractor.text).splitlines():
        normalized = _WHITESPACE.sub(" ", line).strip()
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def _read_direct(url: str, max_chars: int, timeout: float) -> tuple[str, bool]:
    status, headers, raw, response_truncated = _open(url, method="GET", timeout=timeout)
    if not 200 <= status < 400:
        raise ConnectionError(f"HTTP {status}")
    content_type = _header(headers, "content-type")
    decoded = _decode(raw, content_type)
    text = _extract_text(decoded) if "html" in content_type.casefold() or "<html" in decoded[:500].casefold() else decoded.strip()
    if len(text) < 40:
        return "", response_truncated
    return text[:max_chars], response_truncated or len(text) > max_chars


def _read_reader(url: str, service: str, max_chars: int, timeout: float) -> tuple[str, bool]:
    reader_url = _READER_URLS[service].format(url=url)
    status, headers, raw, response_truncated = _open(reader_url, method="GET", timeout=timeout)
    if not 200 <= status < 400:
        raise ConnectionError(f"reader HTTP {status}")
    text = _decode(raw, _header(headers, "content-type")).strip()
    return text[:max_chars], response_truncated or len(text) > max_chars


def _run_read(
    url: str,
    strategy: str = "auto",
    reader_service: str = "auto",
    max_chars: int = 0,
    timeout: float = _DEFAULT_TIMEOUT,
    **_kw: Any,
) -> dict[str, Any]:
    value = _validate_url(url)
    if strategy not in _READ_STRATEGIES:
        raise ValueError(f"未知 strategy: {strategy}")
    if reader_service not in _READER_SERVICES:
        raise ValueError(f"未知 reader_service: {reader_service}")
    limit = min(max(1000, max_chars or _DEFAULT_WEB_READ_CHARS), _MAX_WEB_READ_CHARS)
    errors: list[str] = []
    if strategy in {"auto", "direct"}:
        try:
            text, truncated = _read_direct(value, limit, timeout)
            if text:
                return _result(True, url=value, text=text, chars=len(text), source="direct", truncated=truncated)
        except Exception as exc:
            errors.append(f"direct: {exc}")
    if strategy in {"auto", "reader"}:
        service = "jina" if reader_service == "auto" else reader_service
        try:
            text, truncated = _read_reader(value, service, limit, timeout)
            if text:
                return _result(True, url=value, text=text, chars=len(text), source=f"reader:{service}", truncated=truncated)
        except Exception as exc:
            errors.append(f"reader:{service}: {exc}")
    return _result(False, url=value, error="；".join(errors) or "无法获取网页正文")


_ACTIONS = {"get": _run_get, "post": _run_post, "read": _run_read}


def run(action: str, url: str, *, context: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    handler = _ACTIONS.get(action)
    if handler is None:
        raise ValueError(f"未知 action: {action}，可选: {', '.join(sorted(_ACTIONS))}")
    requested_timeout = kwargs.pop("timeout", 0)
    timeout = float(requested_timeout or context.get("tool_timeout") or _DEFAULT_TIMEOUT)
    kwargs["timeout"] = max(1.0, min(timeout, 3600.0))
    return handler(url=_validate_url(url), **kwargs)
