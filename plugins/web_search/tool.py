"""Tavily 搜索、提取、爬取、站点地图与深度研究。"""

from __future__ import annotations

import json
import os
import time
from typing import Any

try:
    from tavily import TavilyClient
    HAS_TAVILY = True
except ImportError:  # pragma: no cover - dependency error is exercised through _get_client
    TavilyClient = Any  # type: ignore[assignment,misc]
    HAS_TAVILY = False


_SEARCH_DEPTHS = {"basic", "advanced", "fast", "ultra-fast"}
_TOPICS = {"general", "news", "finance"}
_TIME_RANGES = {"day", "week", "month", "year"}
_EXTRACT_DEPTHS = {"basic", "advanced"}
_FORMATS = {"markdown", "text"}
_MODELS = {"mini", "pro", "auto"}
_CITATION_FORMATS = {"numbered", "mla", "apa", "chicago"}
_CONTENT_LIMIT = 10_000
_REPORT_LIMIT = 100_000


def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


def _get_client() -> TavilyClient:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "网络搜索尚未配置：请在项目 .env 中设置 TAVILY_API_KEY，"
            "然后重启智能体。获取密钥：https://app.tavily.com/"
        )
    if not HAS_TAVILY:
        raise RuntimeError(
            "网络搜索依赖尚未安装：请执行 pip install tavily-python，"
            "然后重启智能体。"
        )
    return TavilyClient(api_key=api_key)


def _csv(value: Any) -> list[str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    items = [item.strip() for item in value.replace(";", ",").replace("\n", ",").split(",") if item.strip()]
    return items or None


def _boolish(value: Any, *, default: bool | str = False) -> bool | str:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"basic", "advanced", "markdown", "text"}:
            return normalized
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return default


def _content(value: Any, limit: int = _CONTENT_LIMIT) -> tuple[str, bool]:
    text = value if isinstance(value, str) else "" if value is None else str(value)
    return text[:limit], len(text) > limit


def _search(client: TavilyClient, timeout: float, **kwargs: Any) -> dict[str, Any]:
    query = str(kwargs.get("query") or "").strip()
    if not query:
        raise ValueError("search 需要 query 参数")
    params: dict[str, Any] = {
        "query": query,
        "max_results": max(1, min(int(kwargs.get("max_results") or 5), 20)),
        "include_answer": _boolish(kwargs.get("include_answer"), default="basic"),
        "include_raw_content": _boolish(kwargs.get("include_raw_content"), default=False),
        "include_images": bool(kwargs.get("include_images", False)),
        "timeout": timeout,
    }
    search_depth = str(kwargs.get("search_depth") or "basic").casefold()
    params["search_depth"] = search_depth if search_depth in _SEARCH_DEPTHS else "basic"
    topic = str(kwargs.get("topic") or "general").casefold()
    params["topic"] = topic if topic in _TOPICS else "general"
    for name, allowed in (("time_range", _TIME_RANGES),):
        value = str(kwargs.get(name) or "").casefold()
        if value in allowed:
            params[name] = value
    for name in ("start_date", "end_date", "country"):
        value = str(kwargs.get(name) or "").strip()
        if value:
            params[name] = value
    for name in ("days",):
        value = int(kwargs.get(name) or 0)
        if value > 0:
            params[name] = value
    chunks = int(kwargs.get("chunks_per_source") or 0)
    if chunks > 0 and params["search_depth"] == "advanced":
        params["chunks_per_source"] = max(1, min(chunks, 3))
    for name in ("include_domains", "exclude_domains"):
        value = _csv(kwargs.get(name))
        if value:
            params[name] = value
    for name in ("include_image_descriptions", "include_favicon", "auto_parameters"):
        if kwargs.get(name):
            params[name] = True
    response = client.search(**params)
    results = []
    for item in (response.get("results") or [])[: params["max_results"]]:
        content, truncated = _content(item.get("content"))
        entry = dict(item)
        entry["content"] = content
        entry["content_truncated"] = truncated
        if "raw_content" in entry:
            raw, raw_truncated = _content(entry.get("raw_content"))
            entry["raw_content"] = raw
            entry["raw_content_truncated"] = raw_truncated
        results.append(entry)
    return _result(
        True,
        query=query,
        answer=response.get("answer", ""),
        results=results,
        images=response.get("images", []),
        usage=response.get("usage"),
        response_time=response.get("response_time"),
    )


def _extract(client: TavilyClient, timeout: float, **kwargs: Any) -> dict[str, Any]:
    urls = _csv(kwargs.get("urls"))
    if not urls:
        raise ValueError("extract 需要 urls 参数")
    params: dict[str, Any] = {"urls": urls if len(urls) > 1 else urls[0], "timeout": timeout}
    depth = str(kwargs.get("extract_depth") or "basic").casefold()
    params["extract_depth"] = depth if depth in _EXTRACT_DEPTHS else "basic"
    output_format = str(kwargs.get("format") or "markdown").casefold()
    params["format"] = output_format if output_format in _FORMATS else "markdown"
    for name in ("query",):
        value = str(kwargs.get(name) or "").strip()
        if value:
            params[name] = value
    chunks = int(kwargs.get("chunks_per_source") or 0)
    if chunks > 0:
        params["chunks_per_source"] = chunks
    for name in ("include_images", "include_favicon"):
        if kwargs.get(name):
            params[name] = True
    response = client.extract(**params)
    extracted = []
    for item in response.get("results", []):
        content, truncated = _content(item.get("raw_content"))
        extracted.append({**item, "raw_content": content, "content_truncated": truncated})
    return _result(
        True,
        urls=urls,
        results=extracted,
        failed_results=response.get("failed_results", []),
        usage=response.get("usage"),
    )


def _site_params(kwargs: dict[str, Any], timeout: float, *, crawl: bool) -> dict[str, Any]:
    url = str(kwargs.get("urls") or kwargs.get("query") or "").strip().split(",", 1)[0].strip()
    if not url:
        raise ValueError(f"{'crawl' if crawl else 'map'} 需要 urls 参数")
    params: dict[str, Any] = {"url": url, "timeout": timeout}
    for name in ("max_depth", "max_breadth", "limit"):
        value = int(kwargs.get(name) or 0)
        if value > 0:
            params[name] = value
    instructions = str(kwargs.get("instructions") or "").strip()
    if instructions:
        params["instructions"] = instructions
    for name in ("select_paths", "exclude_paths", "select_domains", "exclude_domains"):
        value = _csv(kwargs.get(name))
        if value:
            params[name] = value
    if kwargs.get("allow_external"):
        params["allow_external"] = True
    if crawl:
        depth = str(kwargs.get("extract_depth") or "basic").casefold()
        params["extract_depth"] = depth if depth in _EXTRACT_DEPTHS else "basic"
        output_format = str(kwargs.get("format") or "markdown").casefold()
        params["format"] = output_format if output_format in _FORMATS else "markdown"
        chunks = int(kwargs.get("chunks_per_source") or 0)
        if chunks > 0:
            params["chunks_per_source"] = chunks
        if kwargs.get("include_images"):
            params["include_images"] = True
    if kwargs.get("include_favicon"):
        params["include_favicon"] = True
    return params


def _crawl(client: TavilyClient, timeout: float, **kwargs: Any) -> dict[str, Any]:
    params = _site_params(kwargs, timeout, crawl=True)
    response = client.crawl(**params)
    pages = []
    for item in response.get("results", []):
        content, truncated = _content(item.get("raw_content"))
        pages.append({**item, "raw_content": content, "content_truncated": truncated})
    return _result(
        True,
        url=params["url"],
        results=pages,
        failed_results=response.get("failed_results", []),
        usage=response.get("usage"),
    )


def _map(client: TavilyClient, timeout: float, **kwargs: Any) -> dict[str, Any]:
    params = _site_params(kwargs, timeout, crawl=False)
    response = client.map(**params)
    urls = response.get("results") or response.get("urls") or []
    return _result(True, url=params["url"], urls=urls, total=len(urls), usage=response.get("usage"))


def _research(client: TavilyClient, timeout: float, **kwargs: Any) -> dict[str, Any]:
    topic = str(kwargs.get("input") or kwargs.get("query") or "").strip()
    if not topic:
        raise ValueError("research 需要 input 参数")
    model = str(kwargs.get("model") or "auto").casefold()
    citation_format = str(kwargs.get("citation_format") or "numbered").casefold()
    params: dict[str, Any] = {
        "input": topic,
        "model": model if model in _MODELS else "auto",
        "citation_format": citation_format if citation_format in _CITATION_FORMATS else "numbered",
        "stream": bool(kwargs.get("stream", False)),
        "timeout": timeout,
    }
    schema_text = str(kwargs.get("output_schema") or "").strip()
    if schema_text:
        try:
            schema = json.loads(schema_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"output_schema 不是有效 JSON: {exc}") from exc
        if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
            raise ValueError("output_schema 必须是包含 properties 的 JSON Schema 对象")
        params["output_schema"] = schema
    task = client.research(**params)
    if params["stream"]:
        chunks: list[str] = []
        total = 0
        truncated = False
        for chunk in task:
            value = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else json.dumps(chunk, ensure_ascii=False) if isinstance(chunk, dict) else str(chunk)
            if total + len(value) > _REPORT_LIMIT:
                chunks.append(value[: max(0, _REPORT_LIMIT - total)])
                truncated = True
                break
            chunks.append(value)
            total += len(value)
        return _result(True, topic=topic, stream=True, report="".join(chunks), truncated=truncated)
    if not isinstance(task, dict):
        raise RuntimeError("Tavily research 返回了无效任务")
    result = task
    request_id = str(result.get("request_id") or "")
    status = str(result.get("status") or "")
    deadline = time.monotonic() + timeout
    while status not in {"completed", "failed", "cancelled"}:
        if not request_id:
            raise RuntimeError("Tavily research 未返回 request_id")
        if time.monotonic() >= deadline:
            return _result(False, topic=topic, request_id=request_id, status=status, error="研究任务超时")
        time.sleep(min(2.0, max(0.05, deadline - time.monotonic())))
        result = client.get_research(request_id)
        status = str(result.get("status") or status)
    if status != "completed":
        return _result(False, topic=topic, request_id=request_id, status=status, error=f"研究任务{status}")
    report, truncated = _content(result.get("content") or result.get("report"), _REPORT_LIMIT)
    return _result(
        True,
        topic=topic,
        request_id=request_id,
        status=status,
        report=report,
        truncated=truncated,
        sources=result.get("sources") or result.get("citations") or [],
        usage=result.get("usage"),
    )


_ACTIONS = {"search": _search, "extract": _extract, "crawl": _crawl, "map": _map, "research": _research}


def run(action: str, *, context: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    handler = _ACTIONS.get(action)
    if handler is None:
        raise ValueError(f"未知 action: {action}，可选: {', '.join(sorted(_ACTIONS))}")
    client = _get_client()
    default = 180 if action == "research" else 150 if action in {"crawl", "map"} else 60 if action == "search" else 30
    timeout = float(context.get("tool_timeout") or default)
    return handler(client, max(1.0, min(timeout, 3600.0)), **kwargs)
