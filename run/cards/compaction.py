"""Oldest-contiguous-prefix compaction with a single atomic commit point."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable

from run.cards.errors import CardCompactionError
from run.cards.models import CardSnapshot, CompactionBatch, validate_summary
from run.cards.store import CardStore


# A summary must stay usable. The retired fixed 256-token reserve forced a
# 360:1 compression on long sessions, which no model can satisfy, so the
# reserve is now a proportional slice of the history target with both bounds.
SUMMARY_RESERVE_RATIO = .15
SUMMARY_RESERVE_MIN = 1024
SUMMARY_RESERVE_MAX = 8192


def default_summary_reserve(target: int) -> int:
    """Usable summary budget for a history target, never below the floor."""
    if target < 1:
        return 1
    return min(max(int(target * SUMMARY_RESERVE_RATIO), SUMMARY_RESERVE_MIN),
               SUMMARY_RESERVE_MAX)


def select_batch(
    snapshot: CardSnapshot, ratio: float = .3, *, summary_reserve: int | None = None,
    max_history_tokens: int | None = None,
) -> CompactionBatch:
    if not 0 < ratio < 1:
        raise ValueError("token_compression_ratio must be strictly between zero and one")
    if not snapshot.cards or snapshot.history_tokens < 1:
        raise CardCompactionError("No effective history is available for compression")
    target = math.floor(snapshot.history_tokens * ratio)
    hard_limit = snapshot.history_tokens - 1
    if max_history_tokens is not None:
        if type(max_history_tokens) is not int or max_history_tokens < 1:
            raise ValueError("max_history_tokens must be a positive integer")
        target = min(target, max_history_tokens)
        hard_limit = min(hard_limit, max_history_tokens)
    if summary_reserve is None:
        summary_reserve = default_summary_reserve(target)
    if type(summary_reserve) is not int or summary_reserve < 1:
        raise ValueError("summary_reserve must be a positive integer")
    # Never demand more summary room than the whole target can afford.
    reserve = min(summary_reserve, max(1, target))
    consumed = 0
    for index, card in enumerate(snapshot.cards):
        consumed += card.view_tokens
        if snapshot.history_tokens - consumed + reserve <= target:
            # Keep the latest user/guidance literal when there is room. A long
            # running integration may still be compacted; no whole-round lock.
            latest = next((i for i in range(len(snapshot.cards) - 1, -1, -1)
                           if snapshot.cards[i].view_kind == "user_text"), 0)
            if 0 < latest <= index:
                suffix = sum(c.view_tokens for c in snapshot.cards[latest:])
                if suffix + reserve <= hard_limit:
                    return CompactionBatch(snapshot, snapshot.cards[:latest],
                                           max(target, suffix + reserve), hard_limit)
            return CompactionBatch(snapshot, snapshot.cards[:index + 1], target, hard_limit)
    raise CardCompactionError(
        "History is too small to admit a valid summary: "
        f"history={snapshot.history_tokens} target={target} reserve={reserve}")


@dataclass(frozen=True, slots=True)
class CompactionResult:
    snapshot: CardSnapshot
    before_tokens: int
    after_tokens: int
    absorbed_count: int
    summary_card_id: str
    usage: dict[str, Any]
    latency_ms: int = 0


def compact(
    store: CardStore, source: str, session_id: str, *,
    summarize: Callable[[CompactionBatch], Any], ratio: float = .3,
    snapshot: CardSnapshot | None = None,
    max_history_tokens: int | None = None,
    validate_snapshot: Callable[[CardSnapshot], None] | None = None,
) -> CompactionResult:
    """No mutation until a valid candidate exists; failures leave the old VIEW."""
    snapshot = snapshot or store.snapshot(source, session_id)
    batch = select_batch(snapshot, ratio, max_history_tokens=max_history_tokens)
    minimum = store.meter.tokens("summary", {"checkpoint": "x"})
    if batch.summary_limit < minimum:
        raise CardCompactionError(
            f"摘要预算不足以容纳最小结构：{batch.summary_limit}<{minimum}")
    candidate = summarize(batch)
    value = candidate.data if hasattr(candidate, "data") else candidate
    try:
        validate_summary(value)
        # Full-history target, including the untouched suffix, is checked in store.
        after = store.replace_prefix(batch, value, validate_snapshot=validate_snapshot)
    except ValueError as error:
        failure = CardCompactionError(str(error))
        failure.compression_usage = dict(getattr(candidate, "usage", {}) or {})
        failure.compression_latency_ms = int(getattr(candidate, "latency_ms", 0))
        raise failure from error
    except Exception as error:
        error.compression_usage = dict(getattr(candidate, "usage", {}) or {})
        error.compression_latency_ms = int(getattr(candidate, "latency_ms", 0))
        raise
    return CompactionResult(
        after, snapshot.history_tokens, after.history_tokens, len(batch.cards),
        batch.cards[0].card_id, dict(getattr(candidate, "usage", {}) or {}),
        int(getattr(candidate, "latency_ms", 0)),
    )
