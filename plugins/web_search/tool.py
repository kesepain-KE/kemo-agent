"""Tavily 网络搜索 — search/extract/crawl/map/research。kemo-agent 原生插件。

需要: pip install tavily-python
需要环境变量: TAVILY_API_KEY
"""

from __future__ import annotations

import os
from typing import Any

try:
    from tavily import TavilyClient
    HAS_TAVILY = True
except ImportError:
    HAS_TAVILY = False


def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


def _get_client() -> TavilyClient:
    if not HAS_TAVILY:
        raise RuntimeError("tavily-python 未安装。请执行: pip install tavily-python")
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY 环境变量未设置")
    return TavilyClient(api_key=api_key)


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n…(截断)", True


# ── 操作 ─────────────────────────────────────────────────────────

def _search(client: TavilyClient, **kwargs: Any) -> dict[str, Any]:
    query = kwargs.get("query", "")
    if not query:
        raise ValueError("search 需要 query 参数")
    params: dict[str, Any] = {"query": query}
    for key in ("search_depth", "topic", "time_range", "max_results", "include_answer",
                 "include_domains", "exclude_domains", "include_raw_content",
                 "include_images", "include_image_descriptions", "country", "days"):
        if kwargs.get(key) is not None and kwargs[key] != "" and kwargs[key] != 0:
            params[key] = kwargs[key]
    result = client.search(**params)
    return _result(True, query=query, answer=result.get("answer", ""),
                   results=result.get("results", [])[:kwargs.get("max_results", 5)],
                   response_time=result.get("response_time", 0))


def _extract(client: TavilyClient, **kwargs: Any) -> dict[str, Any]:
    urls = kwargs.get("urls", "")
    if not urls:
        raise ValueError("extract 需要 urls 参数")
    url_list = [u.strip() for u in urls.replace("\n", ",").split(",") if u.strip()]
    if not url_list:
        raise ValueError("extract 需要有效的 URL")
    params: dict[str, Any] = {"urls": url_list}
    for key in ("query", "extract_depth", "format", "include_images", "include_favicon"):
        if kwargs.get(key) is not None and kwargs[key] != "":
            params[key] = kwargs[key]
    result = client.extract(**params)
    results = result.get("results", [])
    return _result(True, urls=url_list,
                   results=[{"url": r.get("url", ""), "raw_content": r.get("raw_content", "")[:3000]}
                            for r in results],
                   success=sum(1 for r in results if not r.get("error")),
                   failed=sum(1 for r in results if r.get("error")))


def _crawl(client: TavilyClient, **kwargs: Any) -> dict[str, Any]:
    url = kwargs.get("urls", "") or kwargs.get("query", "")
    if not url:
        raise ValueError("crawl 需要 urls 参数（起始 URL）")
    url = url.strip().split(",")[0].strip()
    params: dict[str, Any] = {"url": url}
    for key in ("max_depth", "limit", "instructions", "select_paths", "exclude_paths",
                 "extract_depth", "format", "include_images", "include_favicon"):
        if kwargs.get(key) is not None and kwargs[key] != "" and kwargs[key] != 0:
            params[key] = kwargs[key]
    result = client.crawl(**params)
    results = result.get("results", [])
    return _result(True, url=url,
                   results=[{"url": r.get("url", ""), "raw_content": r.get("raw_content", "")[:2000]}
                            for r in results],
                   success=sum(1 for r in results if not r.get("error")),
                   failed=sum(1 for r in results if r.get("error")),
                   total=len(results))


def _map(client: TavilyClient, **kwargs: Any) -> dict[str, Any]:
    url = kwargs.get("urls", "") or kwargs.get("query", "")
    if not url:
        raise ValueError("map 需要 urls 参数")
    url = url.strip().split(",")[0].strip()
    params: dict[str, Any] = {"url": url}
    for key in ("max_depth", "limit", "instructions", "select_paths", "exclude_paths"):
        if kwargs.get(key) is not None and kwargs[key] != "" and kwargs[key] != 0:
            params[key] = kwargs[key]
    result = client.map(**params)
    return _result(True, url=url, urls=result.get("urls", []),
                   total=len(result.get("urls", [])))


def _research(client: TavilyClient, **kwargs: Any) -> dict[str, Any]:
    topic = kwargs.get("input", "") or kwargs.get("query", "")
    if not topic:
        raise ValueError("research 需要 input 参数")
    params: dict[str, Any] = {"input": topic}
    for key in ("model", "citation_format"):
        if kwargs.get(key) is not None and kwargs[key] != "":
            params[key] = kwargs[key]
    result = client.research(**params)
    return _result(True, topic=topic,
                   report=result.get("report", ""),
                   citations=result.get("citations", []))


# ── 分发 ─────────────────────────────────────────────────────────

_ACTIONS = {
    "search": _search,
    "extract": _extract,
    "crawl": _crawl,
    "map": _map,
    "research": _research,
}


def run(action: str, *, context: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    handler = _ACTIONS.get(action)
    if handler is None:
        raise ValueError(f"未知 action: {action}，可选: {', '.join(sorted(_ACTIONS))}")
    client = _get_client()
    return handler(client, **kwargs)
