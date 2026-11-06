"""Memory keyword scoring and shared history-style text matching."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Pattern

from run.infra.text_search import match_span


_SEARCH_PART_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
_SEARCH_COMPACT_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff]+")


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


def validate_search_options(match_mode: str, regex: bool, case_sensitive: bool) -> None:
    if not isinstance(match_mode, str) or match_mode not in {"keywords", "substring", "word", "exact", "fulltext"}:
        raise ValueError("match_mode 必须是 keywords/substring/word/exact/fulltext")
    if not isinstance(regex, bool):
        raise ValueError("regex 必须是布尔值")
    if not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive 必须是布尔值")
    if match_mode == "fulltext" and not regex and case_sensitive:
        raise ValueError("fulltext 不区分大小写；大小写敏感搜索请用其他 match_mode")


def search_match(
    query: str,
    haystack: str,
    *,
    case_sensitive: bool,
    match_mode: str = "keywords",
    pattern: Pattern[str] | None = None,
) -> dict[str, Any] | None:
    if match_mode != "keywords" or pattern is not None:
        span = match_span(haystack, query, match_mode, pattern, case_sensitive=case_sensitive)
        if span is None:
            return None
        start, end = span
        return {
            "match_score": 100.0,
            "match_coverage": 1.0,
            "exact_match": True,
            "matched_terms": [haystack[start:end]],
            "match_index": start,
            "match_length": end - start,
        }
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
