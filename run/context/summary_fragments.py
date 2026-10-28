"""Lossless internal fragmentation; never rewrites the original conversation."""

from __future__ import annotations

import json
from typing import Any, Callable

from run.context.summary_budget import SummaryBudget, SummaryBudgetError, failure_kind
from run.retry import HOST_CONTROL_EXCEPTIONS


def summarize_bounded(
    rounds: list[dict[str, Any]], previous: dict[str, Any] | None, *,
    trigger: str, skip_memory: bool, budget: SummaryBudget,
    invoke: Callable[[dict[str, Any]], Any],
    normalize: Callable[[Any], dict[str, Any]],
    diagnostics: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Cover every source character before the caller can publish coverage."""
    rolling = previous
    extractions: list[dict[str, Any]] = []
    recoveries = 0

    def payload(parts):
        value = {"previous_summary": rolling, "rounds": parts, "trigger": trigger}
        if skip_memory:
            value["skip_memory_extraction"] = True
        return value

    def call(parts):
        nonlocal rolling, recoveries
        value = payload(parts)
        if not budget.fits(value):
            return False
        diagnostics["request_tokens"] = budget.tokens(value)
        diagnostics["input_budget"] = budget.input_limit
        diagnostics["output_budget"] = budget.output_limit
        if diagnostics.get("attempts", 0) >= 256:
            raise SummaryBudgetError("摘要分片请求数量达到安全上限，未丢弃历史")
        diagnostics["attempts"] = diagnostics.get("attempts", 0) + 1
        try:
            result = invoke(value)
        except BaseException as exc:
            if isinstance(exc, HOST_CONTROL_EXCEPTIONS):
                raise
            kind = failure_kind(exc)
            if kind not in {"input_limit", "request_too_large"} or recoveries >= 3:
                raise
            recoveries += 1
            diagnostics["size_recoveries"] = diagnostics.get("size_recoveries", 0) + 1
            budget.shrink(kind)
            return False
        rolling = normalize(result.data)
        diagnostics["requests"] = diagnostics.get("requests", 0) + 1
        extraction = result.metadata.get("memory_extraction")
        if isinstance(extraction, dict):
            extractions.append(dict(extraction))
        return True

    def process(parts):
        if call(parts):
            return
        if len(parts) > 1:
            middle = len(parts) // 2
            process(parts[:middle])
            process(parts[middle:])
            return
        original = parts[0]
        source = json.dumps(original, ensure_ascii=False, sort_keys=True, default=str)
        offset = 0
        index = 0
        while offset < len(source):
            def fragment(end):
                return [{
                    "round": original["round"],
                    "fragment": {
                        "encoding": "json_text", "index": index + 1,
                        "offset": offset, "total_chars": len(source), "final": end == len(source),
                    },
                    "source_json_part": source[offset:end],
                }]
            low, high, best = offset + 1, len(source), offset
            while low <= high:
                middle = (low + high) // 2
                if budget.fits(payload(fragment(middle))):
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            if best == offset:
                raise SummaryBudgetError("摘要固定内容或已有摘要超过预算，未丢弃历史")
            if call(fragment(best)):
                offset = best
                index += 1
                diagnostics["fragments"] = diagnostics.get("fragments", 0) + 1

    process(rounds)
    if rolling is None:
        raise SummaryBudgetError("摘要未产生有效结果")
    return rolling, extractions
