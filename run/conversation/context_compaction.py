"""Converge summary coverage and whole-round selection before changing state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from run.context import select_context
from run.context import (
    SUMMARY_CHUNK_TOKEN_BUDGET, SUMMARY_MAX_OUTPUT_TOKENS,
    build_summary_message, get_or_create_summary,
)


@dataclass(slots=True)
class CompactionResult:
    selection: Any
    summary_cache: dict[str, Any] | None
    diagnostics: dict[str, Any]


def compact_context(
    *,
    window, policy, runtime_path, system_message, current_user_message,
    tools, previous_cache, agent_runner, trigger, cancel_event=None,
    fixed_suffix_messages=None, force_compress=False,
    force_token_compress=False, max_history_rounds=None,
    response_hook=None, event_callback=None, source="", session_id="",
    summarize=get_or_create_summary,
) -> CompactionResult:
    """Generate summaries for every removed round; never grow the selection.

    All passes remain provisional. A failed pass returns the original summary
    and an untrimmed selection, so callers cannot commit unsummarized removals.
    """
    options = dict(
        window=window, policy=policy, system_message=system_message,
        current_user_message=current_user_message, tools=tools,
        fixed_suffix_messages=fixed_suffix_messages,
    )
    summary_cache = previous_cache
    diagnostics = dict(cache_hit=False, generated=False, failed=False, covered_rounds=[])
    selection = select_context(
        **options, summary_message=build_summary_message(summary_cache),
        force_compress=force_compress, force_token_compress=force_token_compress,
        max_history_rounds=max_history_rounds,
    )
    token_triggered = selection.token_limit_triggered
    round_triggered = selection.round_limit_triggered
    round_stage = selection.round_stage_applied
    token_stage = selection.token_stage_applied
    offset = max(0, int(((window.get("data") or {}).get("context") or {}).get("round_offset", 0)))
    for _ in range(len(selection.all_rounds) + 1):
        removed = [group.number for group in selection.removed_rounds]
        if not removed:
            return CompactionResult(selection, summary_cache, diagnostics)
        summary_cache, diagnostics = summarize(
            runtime_path=runtime_path, groups=selection.removed_rounds,
            agent_runner=agent_runner, agent_name="context_manage", trigger=trigger,
            cancel_event=cancel_event,
            # The summary model has its own prompt/capacity. A small main
            # window must not leave its summarizer less room than its schema.
            chunk_token_budget=SUMMARY_CHUNK_TOKEN_BUDGET,
            max_tokens=SUMMARY_MAX_OUTPUT_TOKENS,
            response_hook=response_hook, event_callback=event_callback,
            source=source, session_id=session_id, skip_memory_extraction=True,
            previous_cache=summary_cache, round_offset=offset, persist=False,
        )
        if diagnostics.get("failed") or summary_cache is None:
            diagnostics = {**diagnostics, "failed": True}
            break
        covered = set(summary_cache.get("covered_rounds") or [])
        if not {offset + number for number in removed}.issubset(covered):
            diagnostics = {**diagnostics, "failed": True, "error": "summary_coverage_incomplete"}
            break
        next_selection = select_context(
            **options, summary_message=build_summary_message(summary_cache),
            token_phase=token_stage,
            max_history_rounds=len(selection.kept_rounds),
        )
        next_selection = replace(
            next_selection,
            token_limit_triggered=token_triggered or next_selection.token_limit_triggered,
            round_limit_triggered=round_triggered or next_selection.round_limit_triggered,
            round_stage_applied=round_stage or next_selection.round_stage_applied,
            token_stage_applied=token_stage or next_selection.token_stage_applied,
        )
        if [group.number for group in next_selection.removed_rounds] == removed:
            return CompactionResult(next_selection, summary_cache, diagnostics)
        token_triggered = token_triggered or next_selection.token_limit_triggered
        round_stage = next_selection.round_stage_applied
        token_stage = next_selection.token_stage_applied
        selection = next_selection
    else:
        diagnostics = {**diagnostics, "failed": True, "error": "summary_did_not_converge"}
    original = select_context(
        **options, summary_message=build_summary_message(previous_cache),
        allow_compress=False,
    )
    return CompactionResult(original, previous_cache, diagnostics)
