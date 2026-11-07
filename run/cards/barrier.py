"""The only automatic compression trigger: immediately before a main request."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
import math
from typing import Any, Callable

from run.cards.assembly import DynamicTail, RequestSnapshot
from run.cards.compaction import CompactionResult, compact, select_batch
from run.cards.errors import CardCapacityError, CardCompactionError
from run.cards.meter import (
    RequestOccupancy, estimate_items_tokens, estimate_request_tokens, estimate_tokens,
)
from run.cards.models import json_text
from run.cards.store import CardStore


@dataclass(frozen=True, slots=True)
class PreparedCardRequest:
    snapshot: RequestSnapshot
    occupancy: RequestOccupancy
    wire_tokens: int
    compaction: CompactionResult | None = None
    compaction_error: Exception | None = None


def prepare_request(
    store: CardStore, source: str, session_id: str, *,
    system_prompt: str, dynamic_tail: Callable[[], dict[str, Any]],
    tools: list[dict[str, Any]], input_limit: int, summarize=None,
    ratio: float = .3, trigger_ratio: float = .8, force_compact: bool = False,
    skip_preventative: bool = False,
    virtual_items=(), transport: str = "", model: str = "",
    on_compaction_start: Callable[[dict[str, Any]], None] | None = None,
) -> PreparedCardRequest:
    """Synchronous appends have already committed RAW and initial VIEW together.

    The SQL snapshot is transactional; the dynamic payload is copied as JSON.
    Neither callbacks nor new arrivals can mutate the returned request snapshot.
    Compaction has its own Provider request and never recursively enters here.
    """
    history = store.snapshot(source, session_id)
    if history.meter_key != store.meter.key:
        history = store.remeter(source, session_id)
    tail = DynamicTail(json.dumps(dynamic_tail(), ensure_ascii=False, separators=(",", ":"),
                                  allow_nan=False))
    schema = json_text(tools)
    virtual_json = json_text([item.model_dump(mode="json", exclude_none=True) for item in virtual_items])
    fixed_tokens = estimate_tokens(system_prompt) if system_prompt else 0
    dynamic_tokens = estimate_tokens(tail.items()[0].model_dump(mode="json")) if tail.items() else 0
    if virtual_json != "[]":
        dynamic_tokens += estimate_items_tokens(virtual_items)
    schema_tokens = estimate_tokens(tools) if tools else 0

    def measure(snapshot):
        request = RequestSnapshot(snapshot, system_prompt, tail, schema, virtual_json, transport, model)
        occupancy = RequestOccupancy(fixed_tokens, snapshot.history_tokens, dynamic_tokens,
                                     schema_tokens, input_limit)
        items = request.items()
        wire_tokens = store.calibrated_tokens(
            estimate_request_tokens(system_prompt, items, tools), items=items)
        return request, occupancy, wire_tokens

    def require_fit(snapshot):
        _, measured, wire = measure(snapshot)
        measured.require_capacity()
        if wire > input_limit:
            raise CardCapacityError(
                f"完整请求超过输入容量：{wire}>{input_limit}；候选摘要未提交，原始卡片保持不变")

    request, occupancy, wire_tokens = measure(history)
    occupancy.require_fixed_capacity()
    _, _, fixed_wire = measure(replace(history, cards=(), history_tokens=0))
    if fixed_wire >= input_limit:
        raise CardCapacityError(
            f"校准后的固定上下文、动态尾部和工具 Schema 已占满输入容量：{fixed_wire}>={input_limit}")
    result = None
    failure = None
    fits = occupancy.total <= input_limit and wire_tokens <= input_limit
    if history.cards and (force_compact or not fits or not skip_preventative and (
            occupancy.should_compact(trigger_ratio) or wire_tokens > input_limit * trigger_ratio)):
        scale = store.calibrated_tokens(1_000_000, items=request.items()) / 1_000_000
        available = min(input_limit - occupancy.fixed_occupancy,
                        math.floor((input_limit - fixed_wire) / scale))
        target = min(math.floor(history.history_tokens * ratio), available)
        empty_summary = {"checkpoint": "x"}
        minimum = store.meter.tokens("summary", empty_summary)
        # A high fixed prefix can cross the trigger even with an irreducibly
        # small history. Do not break an otherwise valid request with a summary
        # that cannot possibly fit its own JSON structure.
        if force_compact or not fits or target >= minimum:
            while available >= minimum:
                batch = select_batch(history, ratio, max_history_tokens=available)
                placeholder = replace(
                    batch.cards[0], view_kind="summary", view_payload_json=json_text(empty_summary),
                    view_tokens=minimum, view_revision=batch.cards[0].view_revision + 1,
                )
                _, _, minimum_wire = measure(replace(
                    history, cards=(placeholder, *history.cards[len(batch.cards):]),
                    history_tokens=minimum + batch.remaining_tokens,
                ))
                # Reserve the actual summary message envelope and the projected
                # untouched suffix, including tool pairs detached by absorption.
                # Leave one local token for separate estimate rounding.
                capped_target = batch.remaining_tokens + minimum + math.floor(
                    (input_limit - minimum_wire) / scale) - 1
                if capped_target >= available:
                    break
                available = min(available, capped_target)
            if available < minimum:
                if force_compact or not fits:
                    raise CardCapacityError("剩余输入容量不足以容纳结构化摘要；原 VIEW 保持不变")
            else:
                if summarize is None:
                    raise CardCapacityError("需要压缩历史，但未配置 CardStream 摘要器")
                if on_compaction_start is not None:
                    on_compaction_start({
                        "history_tokens_before": history.history_tokens,
                        "absorbed_cards": len(select_batch(
                            history, ratio, max_history_tokens=available).cards),
                    })
                try:
                    result = compact(store, source, session_id, summarize=summarize,
                                     ratio=ratio, snapshot=history, max_history_tokens=available,
                                     validate_snapshot=require_fit)
                except Exception as error:
                    from run.context import CompressionBudgetExhausted
                    from provider.retry import transient
                    if (force_compact or not fits or getattr(error, "still_running", False)
                            or not (isinstance(error, (CardCompactionError, CardCapacityError,
                                                      CompressionBudgetExhausted)) or transient(error))):
                        raise
                    # A failed prophylactic attempt must not kill a request
                    # that fits. Re-measure: asynchronous arrivals may not fit.
                    history = store.snapshot(source, session_id)
                    require_fit(history)
                    request, occupancy, wire_tokens = measure(history)
                    failure = error
                # The already-frozen dynamic tail is not re-read during compaction.
                if result is not None:
                    request, occupancy, wire_tokens = measure(result.snapshot)
    occupancy.require_capacity()
    if wire_tokens > input_limit:
        raise CardCapacityError(
            f"适配后请求超出输入容量：{wire_tokens}>{input_limit}，未截断原始卡片或工具参数")
    return PreparedCardRequest(request, occupancy, wire_tokens, result, failure)
