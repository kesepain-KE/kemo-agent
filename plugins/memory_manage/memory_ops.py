from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from run.infra.text_search import compile_query
from run.infra.fulltext import match_positions
from plugins.memory_manage.search_matching import search_match, validate_search_options

from run.memory import (
    TEMPORARY_TIERS,
    TIERS,
    MemoryError,
    MemoryStore,
    contains_sensitive_credential,
    normalize_memory_filename,
    utc_now,
)
from plugins.memory_manage.important_view import (
    IMPORTANT_FILENAME,
    IMPORTANT_MEMORY_PLACEHOLDER,
    apply_important_memory_view,
    important_path as _important_path,
    write_important_memory,
)


MANAGED_TIERS = frozenset((*TIERS, "important"))
_SEARCH_TIER_RANK = {name: index for index, name in enumerate(TIERS)}


def _memory_ref(tier: str, filename: str) -> str:
    return f"{tier}:{filename}"


def _validate_tier(tier: str) -> str:
    if tier not in MANAGED_TIERS:
        raise ValueError(f"不支持的记忆层级：{tier}")
    return tier


def _read_tiers(tier: str) -> tuple[str, ...]:
    if tier == "all":
        return TIERS
    return (_validate_tier(tier),)


def _important_entry(
    root: Path,
    user: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    path = _important_path(root, user)
    if path.is_symlink():
        raise MemoryError("临时重要记忆文件不能是符号链接")
    try:
        content = path.read_text("utf-8").strip()
    except FileNotFoundError:
        return []
    if not content:
        return []
    store = MemoryStore(root, user, config)
    featured_sources = []
    for filename in sorted(store.load_important_view_sources(), key=str.casefold):
        location = store.locate(filename)
        if location is not None and location.tier in TEMPORARY_TIERS:
            featured_sources.append(
                {"tier": location.tier, "filename": location.filename}
            )
    return [
        {
            "filename": IMPORTANT_FILENAME,
            "tier": "important",
            "content": content,
            "weight": 0,
            "updated_at": path.stat().st_mtime,
            "expires_at": None,
            "featured_sources": featured_sources,
        }
    ]


def _tier_entries(
    root: Path, user: str, config: dict[str, Any], tier: str
) -> list[dict[str, Any]]:
    _validate_tier(tier)
    if tier == "important":
        return _important_entry(root, user, config)
    return MemoryStore(root, user, config).load_tier(tier)


def _bounded_integer(value: int, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是整数")
    return min(max(minimum, value), maximum)


def _summary(item: dict[str, Any], tier: str) -> dict[str, Any]:
    temporary = tier in TEMPORARY_TIERS
    filename = str(item["filename"])
    return {
        **({"domain": item["domain"]} if item.get("domain", "fact") != "fact" else {}),
        "memory_ref": _memory_ref(tier, filename),
        "filename": filename,
        "weight": int(item.get("weight", 0)) if temporary else None,
        "created_at": item.get("created_at"),
        "content_updated_at": item.get("content_updated_at"),
        "last_used_at": item.get("last_used_at"),
        "expires_at": item.get("expires_at") if temporary else None,
    }


def list_entries(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    limit: int = 50,
    offset: int = 0,
    compact: bool = False,
    include_content: bool = False,
    page_char_limit: int = 80_000,
) -> dict[str, Any]:
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=500)
    normalized_offset = _bounded_integer(
        offset,
        field="offset",
        minimum=0,
        maximum=1_000_000,
    )
    if not isinstance(compact, bool):
        raise ValueError("compact 必须是布尔值")
    if not isinstance(include_content, bool):
        raise ValueError("include_content 必须是布尔值")
    normalized_page_char_limit = _bounded_integer(
        page_char_limit,
        field="page_char_limit",
        minimum=1_000,
        maximum=90_000,
    )
    items = _read_entries(root, user, config, tier)
    entries = []
    for current_tier, item in items[normalized_offset : normalized_offset + normalized_limit]:
        filename = str(item["filename"])
        temporary = current_tier in TEMPORARY_TIERS
        entry = {
            "memory_ref": _memory_ref(current_tier, filename),
            "filename": filename,
            "weight": int(item.get("weight", 0)) if temporary else None,
        }
        if tier == "all":
            entry["tier"] = current_tier
        if not compact:
            if temporary:
                entry.update({
                    key: item.get(key)
                    for key in ("created_at", "content_updated_at", "last_used_at", "expires_at")
                })
            else:
                entry["expires_at"] = None
        if include_content:
            entry["content"] = str(item.get("content") or "")
        entries.append(entry)
    page_limited_by_chars = False
    if include_content:
        bounded_entries: list[dict[str, Any]] = []
        rendered_chars = 2
        for entry in entries:
            entry_chars = len(json.dumps(entry, ensure_ascii=False, default=str)) + int(
                bool(bounded_entries)
            )
            if (
                bounded_entries
                and rendered_chars + entry_chars > normalized_page_char_limit
            ):
                page_limited_by_chars = True
                break
            bounded_entries.append(entry)
            rendered_chars += entry_chars
        entries = bounded_entries
    page_end = normalized_offset + len(entries)
    has_more = page_end < len(items)
    return {
        "ok": True, "action": "list",
        "tier": tier,
        "timezone": "UTC",
        "entries": entries,
        "total": len(items),
        "offset": normalized_offset,
        "next_offset": page_end if has_more else None,
        "has_more": has_more,
        "truncated": has_more,
        "compact": compact,
        "include_content": include_content,
        "page_char_limit": normalized_page_char_limit,
        "page_limited_by_chars": page_limited_by_chars,
    }


def get_fragment(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    filename: str,
) -> dict[str, Any]:
    _validate_tier(tier)
    if tier == "important":
        if (
            not isinstance(filename, str)
            or filename.strip().casefold() != IMPORTANT_FILENAME.casefold()
        ):
            raise FileNotFoundError(f"记忆不存在：{tier}/{filename}")
        normalized = IMPORTANT_FILENAME
        entries = _important_entry(root, user, config)
        if not entries:
            raise FileNotFoundError(f"记忆不存在：{tier}/{IMPORTANT_FILENAME}")
        item = entries[0]
    else:
        store = MemoryStore(root, user, config)
        normalized = normalize_memory_filename(filename)
        item = store.get_entry(tier, normalized)
        if item is None:
            raise FileNotFoundError(f"记忆不存在：{tier}/{normalized}")
        normalized = str(item["filename"])
    return {
        "ok": True, "action": "get",
        "tier": tier,
        "memory_ref": _memory_ref(
            tier, normalized if tier != "important" else IMPORTANT_FILENAME
        ),
        "filename": normalized if tier != "important" else IMPORTANT_FILENAME,
        "content": str(item.get("content") or ""),
        "weight": int(item.get("weight", 0)) if tier in TEMPORARY_TIERS else None,
        "created_at": item.get("created_at"),
        "content_updated_at": item.get("content_updated_at"),
        "last_used_at": item.get("last_used_at"),
        "expires_at": item.get("expires_at") if tier in TEMPORARY_TIERS else None,
        "featured_sources": (
            item.get("featured_sources", []) if tier == "important" else None
        ),
        "timezone": "UTC",
    }


def _read_entries(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (current_tier, item)
        for current_tier in _read_tiers(tier)
        for item in _tier_entries(root, user, config, current_tier)
    ]


def _search_sort_key(item: dict[str, Any]) -> tuple[float, int, str]:
    return (
        -float(item.get("match_score") or 0),
        _SEARCH_TIER_RANK.get(str(item.get("tier") or ""), len(_SEARCH_TIER_RANK)),
        str(item.get("filename") or "").casefold(),
    )


def _snippet_for_match(
    content: str,
    detail: dict[str, Any],
    context_chars: int,
) -> str:
    index = min(max(0, int(detail.get("match_index") or 0)), len(content))
    match_end = min(len(content), index + max(1, int(detail.get("match_length") or 1)))
    body_budget = context_chars
    center = (index + match_end) // 2
    start = max(0, min(len(content) - body_budget, center - body_budget // 2))
    end = min(len(content), start + body_budget)
    marker_chars = int(start > 0) + int(end < len(content))
    body_budget = max(1, context_chars - marker_chars)
    start = max(0, min(len(content) - body_budget, center - body_budget // 2))
    end = min(len(content), start + body_budget)
    return (
        f"{'…' if start > 0 else ''}{content[start:end]}"
        f"{'…' if end < len(content) else ''}"
    )[:context_chars]


def _fulltext_results(root, user, tier, queries, limit, context_chars, include_content):
    from run.memory.fulltext import search_batch

    tiers = _read_tiers(tier)
    if tier == "important":
        raise ValueError("fulltext 仅检索四个记忆碎片层；important 请使用 keywords 或文本匹配")
    results = []
    for index, (total, rows) in enumerate(search_batch(root, user, tiers, queries, limit)):
        matches = []
        for item in rows:
            title = item["filename"]
            content = item["content"]
            title_spans = match_positions(title, item["title_positions"])
            content_spans = match_positions(content, item["content_positions"])
            match = {
                **_summary(item, item["tier"]), "tier": item["tier"],
                "rank": item["rank"], "match_score": -item["rank"],
                "matched_by": (["title"] if title_spans else []) + (["content"] if content_spans else []),
                "matched_terms": list(dict.fromkeys(
                    [title[start:end] for start, end in title_spans]
                    + [content[start:end] for start, end in content_spans]
                ))[:12],
            }
            if content_spans:
                start, end = content_spans[0]
                match["snippet"] = _snippet_for_match(content, {
                    "match_index": start, "match_length": end - start,
                }, context_chars)
            if include_content:
                match["content"] = content
            matches.append(match)
        results.append({
            "index": index, "title": queries[index][0], "content": queries[index][1],
            "matches": matches, "total_matches": total, "truncated": total > len(matches),
        })
    return results


def search_by_title(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    query: str,
    limit: int = 50,
    case_sensitive: bool = False,
    *,
    match_mode: str = "keywords",
    regex: bool = False,
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 不能为空；列出全部记忆请使用 list action")
    validate_search_options(match_mode, regex, case_sensitive)
    needle = query.strip()
    pattern = compile_query(needle, match_mode, regex, case_sensitive=case_sensitive)
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=500)
    all_matches: list[dict[str, Any]] = []
    if match_mode == "fulltext" and not regex:
        page = _fulltext_results(root, user, tier, [(needle, "")], normalized_limit, 240, False)[0]
        return {
            "ok": True, "action": "search_by_title", "tier": tier, "timezone": "UTC", "query": query,
            "engine": "sqlite-fts5/simple", "matches": page["matches"],
            "total_matches": page["total_matches"], "truncated": page["truncated"],
        }
    for current_tier, item in _read_entries(root, user, config, tier):
        detail = search_match(
            needle,
            Path(str(item["filename"])).stem,
            case_sensitive=case_sensitive, match_mode=match_mode, pattern=pattern,
        )
        if detail is None:
            continue
        match = {
            **_summary(item, current_tier),
            **{
                key: value
                for key, value in detail.items()
                if not key.startswith("match_")
            },
            "match_score": detail["match_score"],
            "match_coverage": detail["match_coverage"],
            "matched_by": ["title"],
            "matched_terms": detail["matched_terms"],
        }
        if tier == "all":
            match["tier"] = current_tier
        all_matches.append(match)
    all_matches.sort(key=_search_sort_key)
    return {
        "ok": True, "action": "search_by_title",
        "tier": tier,
        "timezone": "UTC",
        "query": query,
        "matches": all_matches[:normalized_limit],
        "total_matches": len(all_matches),
        "truncated": len(all_matches) > normalized_limit,
    }


def search_by_content(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    query: str,
    limit: int = 50,
    context_chars: int = 240,
    case_sensitive: bool = False,
    *,
    match_mode: str = "keywords",
    regex: bool = False,
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 不能为空；列出全部记忆请使用 list action")
    validate_search_options(match_mode, regex, case_sensitive)
    needle = query.strip()
    pattern = compile_query(needle, match_mode, regex, case_sensitive=case_sensitive)
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=500)
    normalized_context = _bounded_integer(
        context_chars, field="context_chars", minimum=60, maximum=2000
    )
    matches: list[dict[str, Any]] = []
    if match_mode == "fulltext" and not regex:
        page = _fulltext_results(root, user, tier, [("", needle)], normalized_limit, normalized_context, False)[0]
        return {
            "ok": True, "action": "search_by_content", "tier": tier, "timezone": "UTC", "query": query,
            "engine": "sqlite-fts5/simple", "matches": page["matches"],
            "total_matches": page["total_matches"], "truncated": page["truncated"],
        }
    for current_tier, item in _read_entries(root, user, config, tier):
        content = str(item.get("content") or "")
        detail = search_match(
            needle, content, case_sensitive=case_sensitive, match_mode=match_mode, pattern=pattern,
        )
        if detail is None:
            continue
        match = {
            **_summary(item, current_tier),
            "snippet": _snippet_for_match(content, detail, normalized_context),
            "match_score": detail["match_score"],
            "match_coverage": detail["match_coverage"],
            "exact_match": detail["exact_match"],
            "matched_by": ["content"],
            "matched_terms": detail["matched_terms"],
        }
        if tier == "all":
            match["tier"] = current_tier
        matches.append(match)
    matches.sort(key=_search_sort_key)
    return {
        "ok": True, "action": "search_by_content",
        "tier": tier,
        "timezone": "UTC",
        "query": query,
        "matches": matches[:normalized_limit],
        "total_matches": len(matches),
        "truncated": len(matches) > normalized_limit,
    }


def search_many(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    queries: list[dict[str, Any]],
    *,
    limit: int = 10,
    context_chars: int = 240,
    case_sensitive: bool = False,
    include_content: bool = False,
    match_mode: str = "keywords",
    regex: bool = False,
) -> dict[str, Any]:
    """Search several candidates against one in-memory snapshot of all tiers."""

    if not isinstance(include_content, bool):
        raise ValueError("include_content 必须是布尔值")
    validate_search_options(match_mode, regex, case_sensitive)
    if not isinstance(queries, list) or not queries:
        raise ValueError("search_many 需要非空 queries 数组")
    if len(queries) > 20:
        raise ValueError("search_many 单次最多接收 20 个查询")
    normalized_limit = _bounded_integer(
        limit,
        field="limit",
        minimum=1,
        maximum=50,
    )
    normalized_context = _bounded_integer(
        context_chars, field="context_chars", minimum=60, maximum=2000
    )
    prepared_queries = []
    for index, raw_query in enumerate(queries):
        if not isinstance(raw_query, dict):
            raise ValueError(f"queries[{index}] 必须是对象")
        title = str(raw_query.get("title") or "").strip()
        content = str(raw_query.get("content") or "").strip()
        if not title and not content:
            raise ValueError(f"queries[{index}] 至少需要 title 或 content")
        prepared_queries.append((
            title, content,
            compile_query(title, match_mode, regex, case_sensitive=case_sensitive) if title else None,
            compile_query(content, match_mode, regex, case_sensitive=case_sensitive) if content else None,
        ))
    if match_mode == "fulltext" and not regex:
        return {
            "ok": True, "action": "search_many", "tier": tier, "timezone": "UTC",
            "include_content": include_content, "engine": "sqlite-fts5/simple",
            "results": _fulltext_results(root, user, tier, [(t, c) for t, c, _, _ in prepared_queries],
                                         normalized_limit, normalized_context, include_content),
        }
    entries = _read_entries(root, user, config, tier)
    results: list[dict[str, Any]] = []
    for index, (title, content, title_pattern, content_pattern) in enumerate(prepared_queries):
        matches: list[dict[str, Any]] = []
        for current_tier, item in entries:
            title_detail = (
                search_match(
                    title,
                    Path(str(item["filename"])).stem,
                    case_sensitive=case_sensitive, match_mode=match_mode, pattern=title_pattern,
                )
                if title
                else None
            )
            item_content = str(item.get("content") or "")
            content_detail = (
                search_match(
                    content, item_content, case_sensitive=case_sensitive,
                    match_mode=match_mode, pattern=content_pattern,
                )
                if content
                else None
            )
            if title_detail is None and content_detail is None:
                continue
            matched_by = []
            matched_terms: list[str] = []
            field_scores: dict[str, float] = {}
            if title_detail is not None:
                matched_by.append("title")
                matched_terms.extend(title_detail["matched_terms"])
                field_scores["title"] = float(title_detail["match_score"])
            if content_detail is not None:
                matched_by.append("content")
                matched_terms.extend(content_detail["matched_terms"])
                field_scores["content"] = float(content_detail["match_score"])
            combined_score = (
                field_scores.get("title", 0) * 1.15
                + field_scores.get("content", 0)
                + (15 if len(matched_by) == 2 else 0)
            )
            match = {
                **_summary(item, current_tier),
                "tier": current_tier,
                "matched_by": matched_by,
                "matched_terms": list(dict.fromkeys(matched_terms))[:12],
                "match_score": round(combined_score, 3),
                "match_coverage": max(
                    float((title_detail or {}).get("match_coverage") or 0),
                    float((content_detail or {}).get("match_coverage") or 0),
                ),
                "exact_match": bool(
                    (title_detail or {}).get("exact_match")
                    or (content_detail or {}).get("exact_match")
                ),
                "field_scores": field_scores,
            }
            if content_detail is not None:
                match["snippet"] = _snippet_for_match(
                    item_content,
                    content_detail,
                    normalized_context,
                )
            if include_content:
                match["content"] = item_content
            matches.append(match)
        matches.sort(key=_search_sort_key)
        results.append(
            {
                "index": index,
                "title": title,
                "content": content,
                "matches": matches[:normalized_limit],
                "total_matches": len(matches),
                "truncated": len(matches) > normalized_limit,
            }
        )
    return {
        "ok": True, "action": "search_many",
        "tier": tier,
        "timezone": "UTC",
        "include_content": include_content,
        "results": results,
    }


def delete_fragment(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    filename: str,
) -> dict[str, Any]:
    _validate_tier(tier)
    if tier == "important":
        raise MemoryError("临时重要记忆文件不可删除")
    store = MemoryStore(root, user, config)
    normalized = normalize_memory_filename(filename)
    location = store.locate_in_tier(tier, normalized)
    if location is None:
        return {
            "ok": True, "action": "delete",
            "tier": tier,
            "filename": normalized,
            "deleted": False,
        }
    deleted = store.delete_fragment(tier, location.filename)
    return {
        "ok": True, "action": "delete",
        "tier": tier,
        "memory_ref": _memory_ref(tier, location.filename),
        "filename": location.filename,
        "deleted": deleted,
        "row_removed": deleted,
        "index_removed": False,
        "file_removed": False,
        "repaired_orphan": False,
    }


def add_fragment(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    filename: str,
    content: str,
    *,
    memory_type: str | None = None,
) -> dict[str, Any]:
    _validate_tier(tier)
    body = content.strip()
    if not body:
        raise ValueError("记忆内容不能为空")
    if contains_sensitive_credential(body):
        raise ValueError("记忆内容包含疑似敏感凭据")
    if tier == "important":
        path = _important_path(root, user)
        if path.exists():
            raise FileExistsError("临时重要记忆文件已存在，请使用 edit")
        write_important_memory(root, user, body)
        return {
            "ok": True, "action": "add",
            "tier": tier,
            "memory_ref": _memory_ref(tier, IMPORTANT_FILENAME),
            "filename": IMPORTANT_FILENAME,
        }
    store = MemoryStore(root, user, config)
    normalized = normalize_memory_filename(filename)
    item = store.create_fragment(tier, normalized, body)
    normalized = str(item["filename"])
    return {
        "ok": True, "action": "add",
        "tier": tier,
        "memory_ref": _memory_ref(tier, normalized),
        "filename": normalized,
        "memory_type": memory_type,
    }


def edit_fragment(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    filename: str,
    content: str,
    *,
    new_filename: str | None = None,
    memory_type: str | None = None,
) -> dict[str, Any]:
    _validate_tier(tier)
    body = content.strip()
    if not body:
        raise ValueError("记忆内容不能为空")
    if contains_sensitive_credential(body):
        raise ValueError("记忆内容包含疑似敏感凭据")
    if tier == "important":
        path = _important_path(root, user)
        if not path.is_file():
            raise FileNotFoundError("临时重要记忆文件不存在")
        write_important_memory(root, user, body)
        return {
            "ok": True, "action": "edit",
            "tier": tier,
            "memory_ref": _memory_ref(tier, IMPORTANT_FILENAME),
            "filename": IMPORTANT_FILENAME,
        }
    store = MemoryStore(root, user, config)
    normalized = normalize_memory_filename(filename)
    location = store.locate_in_tier(tier, normalized)
    if location is None:
        raise FileNotFoundError(f"记忆不存在：{tier}/{normalized}")
    source_name = location.filename
    target_name = store.edit_fragment(
        tier,
        source_name,
        body,
        new_filename=new_filename,
        now=utc_now(),
    )
    return {
        "ok": True, "action": "edit",
        "tier": tier,
        "timezone": "UTC",
        "memory_ref": _memory_ref(tier, target_name),
        "filename": source_name,
        "new_filename": target_name,
        "memory_type": memory_type,
    }
