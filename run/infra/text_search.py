"""Text matching shared by history and memory searches."""

from __future__ import annotations

import re
from typing import Pattern


def compile_query(
    query: str, match_mode: str, use_regex: bool, *, case_sensitive: bool = False,
) -> Pattern[str] | None:
    flags = 0 if case_sensitive else re.IGNORECASE
    if use_regex:
        try:
            return re.compile(query, flags)
        except re.error as exc:
            raise ValueError(f"query 不是有效正则表达式：{exc}") from None
    if match_mode == "word":
        return re.compile(rf"\b{re.escape(query)}\b", flags)
    return None


def match_span(
    content: str,
    query: str,
    match_mode: str,
    pattern: Pattern[str] | None,
    *,
    case_sensitive: bool = False,
) -> tuple[int, int] | None:
    if pattern is not None:
        matched = pattern.search(content)
        return matched.span() if matched else None
    if match_mode == "exact":
        stripped = content.strip()
        candidate = stripped if case_sensitive else stripped.casefold()
        needle = query if case_sensitive else query.casefold()
        if candidate != needle:
            return None
        start = len(content) - len(content.lstrip())
        return start, start + len(stripped)
    position = (content.find(query) if case_sensitive
                else content.casefold().find(query.casefold()))
    if position < 0:
        return None
    return position, min(len(content), position + len(query))
