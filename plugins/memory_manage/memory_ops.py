from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

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
SEARCH_ALL_TIERS = (*TIERS,)
_SEARCH_PART_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
_SEARCH_COMPACT_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff]+")
_SEARCH_TIER_RANK = {name: index for index, name in enumerate(SEARCH_ALL_TIERS)}


def _memory_ref(tier: str, filename: str) -> str:
    return f"{tier}:{filename}"


def _validate_tier(tier: str) -> str:
    if tier not in MANAGED_TIERS:
        raise ValueError(f"不支持的记忆层级：{tier}")
    return tier


def _search_tiers(tier: str) -> tuple[str, ...]:
    if tier == "all":
        return SEARCH_ALL_TIERS
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
    _validate_tier(tier)
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
    if tier == "important":
        items = _important_entry(root, user, config)
        names = [IMPORTANT_FILENAME] if items else []
        by_name = {IMPORTANT_FILENAME: items[0]} if items else {}
        entries = []
        for filename in names[normalized_offset : normalized_offset + normalized_limit]:
            entry = {
                "memory_ref": _memory_ref(tier, filename),
                "filename": filename,
                "weight": None,
            }
            if not compact:
                entry["expires_at"] = None
            if include_content:
                entry["content"] = str(by_name[filename].get("content") or "")
            entries.append(entry)
    elif tier == "permanent":
        items = MemoryStore(root, user, config).load_tier("permanent")
        names = [str(item["filename"]) for item in items]
        by_name = {str(item["filename"]): item for item in items}
        entries = []
        for filename in names[normalized_offset : normalized_offset + normalized_limit]:
            entry = {
                "memory_ref": _memory_ref(tier, filename),
                "filename": filename,
                "weight": None,
            }
            if not compact:
                entry["expires_at"] = None
            if include_content:
                entry["content"] = str(by_name[filename].get("content") or "")
            entries.append(entry)
    else:
        items = MemoryStore(root, user, config).load_tier(tier)
        names = [str(item["filename"]) for item in items]
        by_name = {str(item["filename"]): item for item in items}
        selected_names = names[normalized_offset : normalized_offset + normalized_limit]
        entries = []
        for filename in selected_names:
            entry = {
                "memory_ref": _memory_ref(tier, filename),
                "filename": filename,
                "weight": int(by_name[filename].get("weight", 0)),
            }
            if not compact:
                entry.update(
                    {
                        "created_at": by_name[filename].get("created_at"),
                        "content_updated_at": by_name[filename].get(
                            "content_updated_at"
                        ),
                        "last_used_at": by_name[filename].get("last_used_at"),
                        "expires_at": by_name[filename].get("expires_at"),
                    }
                )
            if include_content:
                entry["content"] = str(by_name[filename].get("content") or "")
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
    has_more = page_end < len(names)
    return {
        "action": "list",
        "tier": tier,
        "timezone": "UTC",
        "entries": entries,
        "total": len(names),
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
        "action": "get",
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


def _normalize_search_text(value: Any, *, case_sensitive: bool) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return text if case_sensitive else text.casefold()


def _compact_search_text(value: str) -> str:
    return _SEARCH_COMPACT_RE.sub("", value)


def _search_segments(query: str, *, case_sensitive: bool) -> list[dict[str, Any]]:
    normalized = _normalize_search_text(query, case_sensitive=case_sensitive)
    segments: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for part in _SEARCH_PART_RE.findall(normalized):
        is_cjk = all("\u4e00" <= char <= "\u9fff" for char in part)
        if not is_cjk and len(part) < 2 and not part.isdigit():
            continue
        key = ("cjk" if is_cjk else "word", part)
        if key in seen:
            continue
        seen.add(key)
        grams: tuple[str, ...] = ()
        if is_cjk and len(part) >= 2:
            grams = tuple(
                dict.fromkeys(part[index : index + 2] for index in range(len(part) - 1))
            )
        segments.append(
            {
                "text": part,
                "kind": key[0],
                "grams": grams,
            }
        )
    return segments


def _search_match(
    query: str,
    haystack: str,
    *,
    case_sensitive: bool,
) -> dict[str, Any] | None:
    normalized_query = _normalize_search_text(query, case_sensitive=case_sensitive)
    normalized_haystack = _normalize_search_text(
        haystack, case_sensitive=case_sensitive
    )
    compact_query = _compact_search_text(normalized_query)
    compact_haystack = _compact_search_text(normalized_haystack)
    segments = _search_segments(query, case_sensitive=case_sensitive)
    if not compact_query or not segments:
        return None

    phrase_index = normalized_haystack.find(normalized_query)
    compact_exact = compact_query in compact_haystack
    if phrase_index >= 0 or compact_exact:
        if phrase_index < 0:
            phrase_index = next(
                (
                    normalized_haystack.find(segment["text"])
                    for segment in segments
                    if normalized_haystack.find(segment["text"]) >= 0
                ),
                0,
            )
        return {
            "match_score": round(100.0 + min(len(compact_query), 100) / 100, 3),
            "match_coverage": 1.0,
            "exact_match": True,
            "matched_terms": [segment["text"] for segment in segments][:8],
            "match_index": phrase_index,
            "match_length": max(1, len(normalized_query)),
        }

    haystack_parts = set(_SEARCH_PART_RE.findall(normalized_haystack))
    strengths: list[float] = []
    matched_terms: list[str] = []
    matched_indexes: list[tuple[int, int]] = []
    exact_segments = 0
    positive_segments = 0
    for segment in segments:
        text = str(segment["text"])
        kind = str(segment["kind"])
        index = normalized_haystack.find(text)
        if (kind == "word" and text in haystack_parts) or (
            kind == "cjk" and index >= 0
        ):
            strengths.append(1.0)
            positive_segments += 1
            exact_segments += 1
            matched_terms.append(text)
            matched_indexes.append((max(0, index), len(text)))
            continue
        grams = tuple(str(item) for item in segment.get("grams") or ())
        gram_hits = [gram for gram in grams if gram in normalized_haystack]
        strength = len(gram_hits) / len(grams) if grams else 0.0
        if strength >= (1 / 3):
            positive_segments += 1
            matched_terms.extend(gram_hits)
            for gram in gram_hits:
                matched_indexes.append((normalized_haystack.find(gram), len(gram)))
        else:
            strength = 0.0
        strengths.append(strength)

    coverage = sum(strengths) / len(segments)
    if len(segments) == 1:
        segment = segments[0]
        if segment["kind"] == "cjk" and len(str(segment["text"])) > 2:
            accepted = strengths[0] >= 0.6 and len(set(matched_terms)) >= 2
        else:
            accepted = strengths[0] == 1.0
    else:
        accepted = positive_segments >= 2 and coverage >= 0.5
    if not accepted:
        return None

    unique_terms = list(dict.fromkeys(matched_terms))
    valid_indexes = [item for item in matched_indexes if item[0] >= 0]
    match_index, match_length = min(valid_indexes, default=(0, 1))
    score = coverage * 60 + positive_segments * 8 + exact_segments * 6
    return {
        "match_score": round(score, 3),
        "match_coverage": round(coverage, 3),
        "exact_match": False,
        "matched_terms": unique_terms[:8],
        "match_index": match_index,
        "match_length": max(1, match_length),
    }


def _search_entries(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (current_tier, item)
        for current_tier in _search_tiers(tier)
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


def search_by_title(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    query: str,
    limit: int = 50,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 不能为空；列出全部记忆请使用 list action")
    if not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive 必须是布尔值")
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=500)
    all_matches: list[dict[str, Any]] = []
    for current_tier, item in _search_entries(root, user, config, tier):
        detail = _search_match(
            query,
            Path(str(item["filename"])).stem,
            case_sensitive=case_sensitive,
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
        "action": "search_by_title",
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
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 不能为空；列出全部记忆请使用 list action")
    if not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive 必须是布尔值")
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=500)
    normalized_context = _bounded_integer(
        context_chars, field="context_chars", minimum=60, maximum=2000
    )
    matches: list[dict[str, Any]] = []
    for current_tier, item in _search_entries(root, user, config, tier):
        content = str(item.get("content") or "")
        detail = _search_match(query, content, case_sensitive=case_sensitive)
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
        "action": "search_by_content",
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
) -> dict[str, Any]:
    """Search several candidates against one in-memory snapshot of all tiers."""

    if not isinstance(include_content, bool):
        raise ValueError("include_content 必须是布尔值")
    if not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive 必须是布尔值")
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
    entries = _search_entries(root, user, config, tier)
    results: list[dict[str, Any]] = []
    for index, raw_query in enumerate(queries):
        if not isinstance(raw_query, dict):
            raise ValueError(f"queries[{index}] 必须是对象")
        title = str(raw_query.get("title") or "").strip()
        content = str(raw_query.get("content") or "").strip()
        if not title and not content:
            raise ValueError(f"queries[{index}] 至少需要 title 或 content")
        matches: list[dict[str, Any]] = []
        for current_tier, item in entries:
            title_detail = (
                _search_match(
                    title,
                    Path(str(item["filename"])).stem,
                    case_sensitive=case_sensitive,
                )
                if title
                else None
            )
            item_content = str(item.get("content") or "")
            content_detail = (
                _search_match(content, item_content, case_sensitive=case_sensitive)
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
        "action": "search_many",
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
            "action": "delete",
            "tier": tier,
            "filename": normalized,
            "deleted": False,
        }
    deleted = store.delete_fragment(tier, location.filename)
    return {
        "action": "delete",
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
            "action": "add",
            "tier": tier,
            "memory_ref": _memory_ref(tier, IMPORTANT_FILENAME),
            "filename": IMPORTANT_FILENAME,
        }
    store = MemoryStore(root, user, config)
    normalized = normalize_memory_filename(filename)
    item = store.create_fragment(tier, normalized, body)
    normalized = str(item["filename"])
    return {
        "action": "add",
        "tier": tier,
        "memory_ref": _memory_ref(tier, normalized),
        "filename": normalized,
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
            "action": "edit",
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
        "action": "edit",
        "tier": tier,
        "timezone": "UTC",
        "memory_ref": _memory_ref(tier, target_name),
        "filename": source_name,
        "new_filename": target_name,
    }
