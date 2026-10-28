"""Whole-round selection, with independent round and token compression floors."""

from __future__ import annotations

from typing import Any, Iterable

from run.context import (
    ContextPolicy, ContextSelection, build_round_groups,
    estimate_messages_tokens, estimate_tools_tokens,
)


def select_context(
    *,
    window: dict[str, Any],
    policy: ContextPolicy,
    system_message: dict[str, Any] | None,
    current_user_message: dict[str, Any] | None,
    fixed_suffix_messages: Iterable[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    summary_message: dict[str, Any] | None = None,
    force_compress: bool = False,
    force_token_compress: bool = False,
    token_phase: bool = False,
    max_history_rounds: int | None = None,
    allow_compress: bool = True,
) -> ContextSelection:
    """Every entry tries rounds first, then tokens only when still necessary.

    Token pressure may remove protected recent rounds, keeping at least one
    complete turn (the live turn counts). Fixed messages and live tool groups
    are never split. ``allow_compress=False`` measures the actual workspace.
    """
    rounds = build_round_groups(window, policy)
    prefix = [item for item in (system_message, summary_message) if item is not None]
    suffix = [current_user_message] if current_user_message is not None else []
    suffix.extend(item for item in (fixed_suffix_messages or []) if isinstance(item, dict))
    tool_tokens = estimate_tools_tokens(tools)

    def messages_for(groups):
        return [*prefix, *(message for group in groups for message in group.messages), *suffix]

    before = estimate_messages_tokens(messages_for(rounds)) + tool_tokens
    current_slots = int(current_user_message is not None)
    minimum = max(0, 1 - current_slots)
    kept = list(rounds)
    if allow_compress and max_history_rounds is not None:
        keep_count = min(len(kept), max(minimum, max_history_rounds))
        kept = kept[-keep_count:] if keep_count else []
    initial_count = len(kept)
    bounded_tokens = estimate_messages_tokens(messages_for(kept)) + tool_tokens
    round_trigger = allow_compress and initial_count + current_slots >= policy.max_rounds
    pressure = bounded_tokens >= policy.token_limit or force_token_compress
    requested = allow_compress and (round_trigger or force_compress or pressure or token_phase)
    if requested and not token_phase:
        keep_count = min(
            len(kept),
            max(minimum, policy.recent_full_rounds - current_slots,
                policy.rounds_after_compression - current_slots),
        )
        kept = kept[-keep_count:] if keep_count else []
    round_progress = len(kept) < initial_count
    after_rounds = estimate_messages_tokens(messages_for(kept)) + tool_tokens
    token_stage = requested and (
        token_phase or not round_progress or after_rounds >= policy.token_limit
    )
    token_trigger = allow_compress and (
        pressure or token_phase or (force_compress and not round_progress)
    )
    while token_stage and len(kept) > minimum:
        if estimate_messages_tokens(messages_for(kept)) + tool_tokens <= policy.input_budget:
            break
        kept.pop(0)
    # A server rejection / exact-usage signal can expose tokenizer differences.
    # If both local phases see a small request, still make bounded progress;
    # never jump directly to an empty historical window.
    if force_token_compress and requested and len(kept) == initial_count and len(kept) > minimum:
        kept.pop(0)
    selected = messages_for(kept)
    after = estimate_messages_tokens(selected) + tool_tokens
    kept_numbers = {group.number for group in kept}
    return ContextSelection(
        messages=selected, all_rounds=rounds, kept_rounds=kept,
        removed_rounds=[group for group in rounds if group.number not in kept_numbers],
        estimated_tokens_before=before, estimated_tokens_after=after,
        tool_schema_tokens=tool_tokens, input_budget=policy.input_budget,
        output_reserve=policy.output_reserve,
        round_limit_triggered=round_trigger or (allow_compress and force_compress),
        token_limit_triggered=token_trigger,
        fixed_content_over_budget=(
            estimate_messages_tokens([*prefix, *suffix]) + tool_tokens > policy.input_budget
        ),
        recent_content_over_budget=bool(
            token_trigger and len(kept) <= minimum and after > policy.input_budget
        ),
        token_limit=policy.token_limit,
        current_rounds=current_slots,
        round_stage_applied=round_progress,
        token_stage_applied=token_stage,
    )
