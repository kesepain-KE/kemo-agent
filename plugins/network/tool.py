"""HTTP 网络请求工具 — GET/POST + 网页正文读取。kemo-agent 原生插件。"""

from html.parser import HTMLParser
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Any

_MAX_RESPONSE_BYTES = 0
_VERIFY_SSL = os.environ.get("HTTP_VERIFY_SSL", "1") != "0"
_READ_STRATEGIES = {"auto", "direct", "reader"}
_READER_SERVICES = {"auto", "jina", "markdown_new", "defuddle"}
_DEFAULT_WEB_READ_CHARS = 20000
_MAX_WEB_READ_CHARS = 100000

_READER_URLS = {
    "jina": "https://r.jina.ai/{url}",
    "markdown_new": "https://markdown.new/{url}",
    "defuddle": "https://defuddle.md/{url}",
}

_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_ENTITY = re.compile(r"&[a-zA-Z0-9#]+;")
_WHITESPACE = re.compile(r"\s{2,}")
_HTTP_TIMEOUT = 15


def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


# ── HTTP ────────────────────────────────────────────────────────────

def _open(url: str, data: bytes | None = None, headers: dict[str, str] | None = None,
          timeout: int = _HTTP_TIMEOUT) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=data or None, headers=headers or {}, method="POST" if data else "GET")
    ctx = None
    if not _VERIFY_SSL:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, dict(exc.headers), body
    except urllib.error.URLError as exc:
        raise ConnectionError(f"连接失败: {exc.reason}") from exc


def _run_get(url: str, headers: str = "", **_kw: Any) -> dict[str, Any]:
    parsed_headers = json.loads(headers) if headers else {}
    status, resp_headers, raw = _open(url, headers=parsed_headers)
    content_type = resp_headers.get("Content-Type", "")
    body = raw.decode("utf-8", errors="replace")
    if "application/json" in content_type:
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
    return _result(True, status=status, url=url, body=body, content_type=content_type)


def _run_post(url: str, body: str = "", headers: str = "", **_kw: Any) -> dict[str, Any]:
    parsed_headers = json.loads(headers) if headers else {}
    status, resp_headers, raw = _open(url, data=body.encode("utf-8"), headers=parsed_headers)
    content_type = resp_headers.get("Content-Type", "")
    resp_body = raw.decode("utf-8", errors="replace")
    if "application/json" in content_type:
        try:
            resp_body = json.loads(resp_body)
        except json.JSONDecodeError:
            pass
    return _result(True, status=status, url=url, body=resp_body, content_type=content_type)


# ── HTML 解析 ──────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, _attrs: list) -> None:
        if tag in {"script", "style", "head", "nav", "footer", "header", "aside"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head", "nav", "footer", "header", "aside"}:
            self._skip = False
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self.text.append(stripped)


def _extract_text(html: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        pass
    raw = " ".join(extractor.text)
    raw = _HTML_TAG.sub("", raw)
    raw = _HTML_ENTITY.sub(" ", raw)
    return _WHITESPACE.sub(" ", raw).strip()


def _read_direct(url: str, max_chars: int) -> str:
    _, _, raw = _open(url)
    html = raw.decode("utf-8", errors="replace")
    text = _extract_text(html)
    if not text or len(text) < 40:
        return ""
    return text[:max_chars]


def _read_reader(url: str, service: str, max_chars: int) -> str:
    reader_url = _READER_URLS.get(service, _READER_URLS["jina"]).format(url=url)
    _, _, raw = _open(reader_url)
    text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith("```markdown"):
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if i > 0 and line.strip().startswith("```"):
                text = "\n".join(lines[1:i]).strip()
                break
    return text[:max_chars]


def _run_read(url: str, strategy: str = "auto", reader_service: str = "auto",
              max_chars: int = 0, **_kw: Any) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"仅支持 http/https: {url}")
    max_chars = min(max(1000, max_chars or _DEFAULT_WEB_READ_CHARS), _MAX_WEB_READ_CHARS)

    if strategy not in _READ_STRATEGIES:
        strategy = "auto"
    if reader_service not in _READER_SERVICES:
        reader_service = "auto"

    text = ""
    source = ""

    if strategy in ("auto", "direct"):
        try:
            text = _read_direct(url, max_chars)
            source = "direct"
        except Exception:
            pass

    if not text and strategy in ("auto", "reader"):
        svc = reader_service if reader_service != "auto" else "jina"
        try:
            text = _read_reader(url, svc, max_chars)
            source = f"reader:{svc}"
        except Exception:
            pass

    if not text:
        return _result(False, url=url, error="无法获取网页正文")

    return _result(True, url=url, text=text, chars=len(text), source=source)


# ── 分发 ────────────────────────────────────────────────────────────

_ACTIONS = {
    "get": _run_get,
    "post": _run_post,
    "read": _run_read,
}


def run(action: str, url: str, *, context: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    handler = _ACTIONS.get(action)
    if handler is None:
        raise ValueError(f"未知 action: {action}，可选: {', '.join(sorted(_ACTIONS))}")
    return handler(url=url, **kwargs)
