from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Pattern


_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WINDOW_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-|$)")
_ROLES = frozenset({"any", "user", "assistant"})
_MATCH_MODES = frozenset({"substring", "word", "exact"})


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


def _in_time_range(dirname: str, since: str | None, until: str | None) -> bool:
    """Return whether a committed history window is inside the date range."""

    if not _WINDOW_PATTERN.match(dirname):
        return False
    window_date = dirname[:10]
    if since and window_date < since:
        return False
    if until and window_date > until:
        return False
    return True


def _compile_query(query: str, match_mode: str, use_regex: bool) -> Pattern[str] | None:
    if use_regex:
        try:
            return re.compile(query, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"query 不是有效正则表达式：{exc}") from None
    if match_mode == "word":
        return re.compile(rf"\b{re.escape(query)}\b", re.IGNORECASE)
    return None


def _match_span(
    content: str,
    query: str,
    match_mode: str,
    pattern: Pattern[str] | None,
) -> tuple[int, int] | None:
    if pattern is not None:
        matched = pattern.search(content)
        return matched.span() if matched else None
    if match_mode == "exact":
        stripped = content.strip()
        if stripped.casefold() != query.casefold():
            return None
        start = len(content) - len(content.lstrip())
        return start, start + len(stripped)
    position = content.casefold().find(query.casefold())
    if position < 0:
        return None
    return position, min(len(content), position + len(query))


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


def _context_messages(
    text: dict[str, Any], match_index: int, count: int
) -> tuple[list[dict[str, str]], int]:
    raw_messages = text.get("messages")
    messages = raw_messages if isinstance(raw_messages, list) else []
    eligible: list[tuple[int, dict[str, str]]] = []
    match_position = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if index == match_index:
            match_position = len(eligible)
        eligible.append((index, {"role": str(message["role"]), "content": content}))
    start = max(0, match_position - count)
    end = min(len(eligible), match_position + count + 1)
    return [item for _, item in eligible[start:end]], match_position - start


def _bounded_integer(value: int, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是整数")
    return min(max(minimum, value), maximum)


def run(
    query: str,
    limit: int = 10,
    since: str = "",
    until: str = "",
    role: str = "any",
    match_mode: str = "substring",
    regex: bool = False,
    max_snippet: int = 500,
    context_messages: int = 0,
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
        raise ValueError(
            f"match_mode 必须是 substring/word/exact，而不是 {match_mode}"
        )
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=100)
    normalized_snippet = _bounded_integer(
        max_snippet, field="max_snippet", minimum=1, maximum=5000
    )
    normalized_context = _bounded_integer(
        context_messages, field="context_messages", minimum=0, maximum=20
    )
    since_value = _normalize_date(since, field="since")
    until_value = _normalize_date(until, field="until")
    if since_value and until_value and since_value > until_value:
        raise ValueError("since 不能晚于 until")

    result: dict[str, Any] = {
        "query": query,
        "matches": [],
        "total_matches": 0,
        "truncated": False,
        "time_range": {"since": since_value, "until": until_value},
    }
    if not needle:
        return result

    pattern = _compile_query(needle, normalized_mode, regex)
    try:
        history_dir = (
            Path(context["root"]) / "users" / str(context["user"]) / "history"
        )
    except (KeyError, TypeError):
        raise ValueError("context 必须包含 root 和 user") from None
    if not history_dir.is_dir():
        return result

    matches: list[dict[str, Any]] = []
    total_matches = 0
    for directory in sorted(history_dir.iterdir(), key=lambda item: item.name, reverse=True):
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or getattr(directory, "is_junction", lambda: False)()
            or not _in_time_range(directory.name, since_value, until_value)
        ):
            continue
        try:
            data = json.loads((directory / "data.json").read_text("utf-8"))
            text = json.loads((directory / "text.json").read_text("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("complete") is not True:
            continue
        if not isinstance(text, dict) or not isinstance(text.get("messages"), list):
            continue

        for index, message in enumerate(text["messages"]):
            if not isinstance(message, dict):
                continue
            message_role = message.get("role")
            if message_role not in {"user", "assistant"}:
                continue
            if normalized_role != "any" and message_role != normalized_role:
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            span = _match_span(content, needle, normalized_mode, pattern)
            if span is None:
                continue

            total_matches += 1
            if len(matches) >= normalized_limit:
                continue
            entry: dict[str, Any] = {
                "window": directory.name,
                "source": data.get("source"),
                "session_id": data.get("session_id"),
                "role": message_role,
                "snippet": _snippet(content, span, normalized_snippet),
                "match_index": index,
            }
            if normalized_context > 0:
                context_result, context_index = _context_messages(
                    text, index, normalized_context
                )
                entry["context"] = context_result
                entry["context_index"] = context_index
            matches.append(entry)

    result["matches"] = matches
    result["total_matches"] = total_matches
    result["truncated"] = total_matches > normalized_limit
    return result
