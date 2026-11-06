from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from run.history.archive_query_common import raw_search_rows, raw_text_context
from run.history.fulltext import search_rows as fulltext_rows
from run.infra.fulltext import match_positions
from run.infra.text_search import compile_query as _compile_query, match_span as _match_span


_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ROLES = frozenset({"any", "user", "assistant"})
_MATCH_MODES = frozenset({"substring", "word", "exact", "fulltext"})


def _normalize_date(value: str, *, field: str) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None
    if not _DATE_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} 必须使用 YYYY-MM-DD 格式")
    try:
        date.fromisoformat(normalized)
    except ValueError:
        raise ValueError(f"{field} 不是有效日期：{normalized}") from None
    return normalized


def _snippet(content: str, match_span: tuple[int, int], max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    match_start, match_end = match_span
    center = max(0, min(len(content), (match_start + match_end) // 2))
    if max_chars == 1:
        return content[min(center, len(content) - 1)]
    if max_chars == 2:
        character = content[min(center, len(content) - 1)]
        return f"…{character}" if center > 0 else f"{character}…"

    def bounds(budget: int) -> tuple[int, int]:
        if budget >= len(content):
            return 0, len(content)
        start = max(0, min(len(content) - budget, center - budget // 2))
        return start, start + budget

    body_budget = max_chars
    start, end = bounds(body_budget)
    for _ in range(3):
        marker_chars = int(start > 0) + int(end < len(content))
        next_budget = max(0, max_chars - marker_chars)
        if next_budget == body_budget:
            break
        body_budget = next_budget
        if body_budget == 0:
            return "…" * min(max_chars, 2)
        start, end = bounds(body_budget)

    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"[:max_chars]


def _bounded_integer(value: int, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是整数")
    return min(max(minimum, value), maximum)


def _bounded_filter(value: str, *, field: str, maximum: int = 200) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{field} 最多 {maximum} 个字符")
    return normalized


def _serialized_chars(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _search(
    query: str,
    limit: int = 10,
    since: str = "",
    until: str = "",
    role: str = "any",
    match_mode: str = "substring",
    regex: bool = False,
    max_snippet: int = 500,
    context_messages: int = 0,
    max_context_chars: int = 1000,
    offset: int = 0,
    page_char_limit: int = 80_000,
    source: str = "",
    session_id: str = "",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(query, str):
        raise ValueError("query 必须是字符串")
    if not isinstance(since, str) or not isinstance(until, str):
        raise ValueError("since 和 until 必须是字符串")
    if not isinstance(role, str) or not isinstance(match_mode, str):
        raise ValueError("role 和 match_mode 必须是字符串")
    if not isinstance(regex, bool):
        raise ValueError("regex 必须是布尔值")

    needle = query.strip()
    normalized_role = role.strip().casefold()
    normalized_mode = match_mode.strip().casefold()
    if normalized_role not in _ROLES:
        raise ValueError(f"role 必须是 any/user/assistant，而不是 {role}")
    if normalized_mode not in _MATCH_MODES:
        raise ValueError(f"match_mode 必须是 substring/word/exact/fulltext，而不是 {match_mode}")
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=100)
    normalized_snippet = _bounded_integer(
        max_snippet, field="max_snippet", minimum=1, maximum=5000
    )
    normalized_context = _bounded_integer(
        context_messages, field="context_messages", minimum=0, maximum=20
    )
    normalized_context_chars = _bounded_integer(
        max_context_chars, field="max_context_chars", minimum=50, maximum=5000
    )
    normalized_offset = _bounded_integer(
        offset, field="offset", minimum=0, maximum=1_000_000
    )
    normalized_page_chars = _bounded_integer(
        page_char_limit, field="page_char_limit", minimum=1000, maximum=90_000
    )
    normalized_source = _bounded_filter(source, field="source")
    normalized_session_id = _bounded_filter(session_id, field="session_id")
    since_value = _normalize_date(since, field="since")
    until_value = _normalize_date(until, field="until")
    if since_value and until_value and since_value > until_value:
        raise ValueError("since 不能晚于 until")

    result: dict[str, Any] = {
        "ok": True,
        "query": query,
        "matches": [],
        "total_matches": 0,
        "truncated": False,
        "offset": normalized_offset,
        "next_offset": None,
        "has_more": False,
        "page_char_limit": normalized_page_chars,
        "page_limited_by_chars": False,
        "time_range": {"since": since_value, "until": until_value},
        "filters": {
            "source": normalized_source or None,
            "session_id": normalized_session_id or None,
        },
    }
    if not needle:
        return result

    pattern = _compile_query(needle, normalized_mode, regex)
    try:
        root = Path(context["root"])
        user = str(context["user"])
    except (KeyError, TypeError):
        raise ValueError("context 必须包含 root 和 user") from None
    matches: list[dict[str, Any]] = []
    total_matches = 0
    rendered_chars = 0
    page_limited_by_chars = False
    collection_closed = False
    indexed = normalized_mode == "fulltext" and not regex
    if indexed:
        total_matches, rows = fulltext_rows(
            root, user, needle, source=normalized_source, session_id=normalized_session_id,
            since=since_value or "", until=until_value or "", role=normalized_role,
            limit=normalized_limit, offset=normalized_offset,
        )
        result["engine"] = "sqlite-fts5/simple"
    else:
        rows = raw_search_rows(
            root, user, source=normalized_source, session_id=normalized_session_id,
            since=since_value or "", until=until_value or "",
        )
    for message in rows:
        message_role, content = message["role"], message["content"]
        if normalized_role != "any" and message_role != normalized_role:
            continue
        if indexed:
            if collection_closed:
                break
            span = match_positions(content, message["positions"])[0]
        else:
            span = _match_span(content, needle, normalized_mode, pattern)
            if span is None:
                continue
            total_matches += 1
            if total_matches <= normalized_offset or collection_closed or len(matches) >= normalized_limit:
                continue
        entry = {key: message[key] for key in (
            "source", "session_id", "card_id", "integration_id", "seq", "raw_kind", "created_at", "role")}
        entry["snippet"] = _snippet(content, span, normalized_snippet)
        if indexed:
            entry["rank"] = message["rank"]
        if normalized_context:
            entry["context"] = raw_text_context(
                root, user, source=message["source"], session_id=message["session_id"],
                seq=message["seq"], count=normalized_context, max_chars=normalized_context_chars,
            )
        entry_chars = _serialized_chars(entry)
        if rendered_chars + entry_chars + _serialized_chars(result) + 128 > normalized_page_chars:
            page_limited_by_chars = collection_closed = True
            continue
        matches.append(entry)
        rendered_chars += entry_chars

    result["matches"] = matches
    result["total_matches"] = total_matches
    returned_end = normalized_offset + len(matches)
    result["has_more"] = total_matches > returned_end
    result["truncated"] = result["has_more"]
    result["next_offset"] = returned_end if result["has_more"] else None
    result["page_limited_by_chars"] = page_limited_by_chars
    return result


def run(
    query: str = "",
    limit: int = 10,
    since: str = "",
    until: str = "",
    role: str = "any",
    match_mode: str = "substring",
    regex: bool = False,
    max_snippet: int = 500,
    context_messages: int = 0,
    max_context_chars: int = 1000,
    offset: int = 0,
    page_char_limit: int = 80_000,
    source: str = "",
    session_id: str = "",
    *,
    action: str = "search",
    date: str = "",
    archive_id: str = "",
    cursor: str = "",
    context: dict[str, Any],
) -> dict[str, Any]:
    """Keep the old positional search contract; new operations are keyword-only."""
    if not isinstance(action, str) or action not in {"search", "dates", "list", "read"}:
        raise ValueError("action 必须是 search/dates/list/read")
    if not isinstance(query, str):
        raise ValueError("query 必须是字符串")
    if not isinstance(since, str) or not isinstance(until, str):
        raise ValueError("since 和 until 必须是字符串")
    if not isinstance(source, str) or not isinstance(session_id, str):
        raise ValueError("source 和 session_id 必须是字符串")
    for field, value, maximum in (("date", date, 10), ("archive_id", archive_id, 4096),
                                   ("cursor", cursor, 8192)):
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{field} 必须是长度不超过 {maximum} 的字符串")
    if action == "search":
        if date or archive_id or cursor:
            raise ValueError("search 不接受 date/archive_id/cursor，请用 since/until/offset")
        return _search(
            query, limit, since, until, role, match_mode, regex, max_snippet,
            context_messages, max_context_chars, offset, page_char_limit, source, session_id,
            context=context,
        )
    if query or match_mode != "substring" or regex or context_messages or max_snippet != 500 or max_context_chars != 1000:
        raise ValueError("query/match_mode/regex/片段与上下文参数仅适用于 search")
    if not isinstance(role, str) or role not in _ROLES:
        raise ValueError("role 必须是 any/user/assistant")
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=100)
    normalized_chars = _bounded_integer(page_char_limit, field="page_char_limit", minimum=1000, maximum=90_000)
    normalized_offset = _bounded_integer(offset, field="offset", minimum=0, maximum=1_000_000)
    try:
        root, user = Path(context["root"]), str(context["user"])
    except (KeyError, TypeError):
        raise ValueError("context 必须包含 root 和 user") from None
    if action == "read":
        if not archive_id:
            raise ValueError("read 必须提供 list 返回的 archive_id")
        if date or since or until or source or session_id or offset:
            raise ValueError("read 使用 archive_id/cursor 定位，不接受目录过滤或 offset")
        from run.history import read_archive
        return {"ok": True, **read_archive(
            root, user, archive_id=archive_id, cursor=cursor, role=role,
            limit=normalized_limit, page_char_limit=normalized_chars,
        )}
    if archive_id or cursor or role != "any":
        raise ValueError("dates/list 不接受 archive_id/cursor/role；请使用 read 或 search")
    if date and action != "list":
        raise ValueError("date 仅用于 list；日期目录可使用 since/until")
    if not isinstance(since, str) or not isinstance(until, str):
        raise ValueError("since 和 until 必须是字符串")
    date_value = _normalize_date(date, field="date") or ""
    since_value = _normalize_date(since, field="since") or ""
    until_value = _normalize_date(until, field="until") or ""
    if since_value and until_value and since_value > until_value:
        raise ValueError("since 不能晚于 until")
    from run.history import query_archives
    return {"ok": True, **query_archives(
        root, user, action=action, date=date_value, since=since_value, until=until_value,
        source=_bounded_filter(source, field="source"),
        session_id=_bounded_filter(session_id, field="session_id"),
        limit=normalized_limit, offset=normalized_offset, page_char_limit=normalized_chars,
    )}
