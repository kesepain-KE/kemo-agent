"""Finish threshold-triggered compression before committing a completed round."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from events import RunEvent
from run.context import select_context, build_context_snapshot, build_summary_message, SUMMARY_STORE_REF
from run.conversation.context_compaction import compact_context


@dataclass(slots=True)
class FinalizedContext:
    window: dict[str, Any]
    summary_cache: dict[str, Any] | None
    summary_diagnostics: dict[str, Any]
    context_stats: dict[str, Any]
    compressed: bool


def finalize_completed_context(v):
    """Include this round's outputs in the threshold check and summary commit."""
    applied = bool(v["summary_cache"] is not None and v["context_selection"].removed_rounds)
    # Failed summaries must not silently fall back to unverified max-round trims.
    limit = (
        max(1, len(v["context_selection"].kept_rounds) + 1)
        if applied else max(1, int(v["window"]["data"]["rounds"]))
    )
    window = v["_trim_to_max_rounds"](v["window"], limit)
    offset = v["archive_round_number"] - int(window["data"]["rounds"])
    window["data"].setdefault("context", {})["round_offset"] = max(0, offset)
    cache = v["summary_cache"]
    diagnostics = v["summary_diagnostics"]
    stats = dict(v["context_stats"])
    policy = v["context_policy"]
    declared = stats.get("provider_declared_input_limit")
    if isinstance(declared, int) and declared > 0:
        policy = replace(policy, token_limit=min(policy.token_limit, declared))
    options = dict(
        window=window, policy=policy, system_message=v["system_message"],
        current_user_message=None, tools=v["tool_schemas"],
    )
    candidate = select_context(**options, summary_message=build_summary_message(cache))
    if candidate.removed_rounds:
        trigger = "token_limit" if candidate.token_limit_triggered else "round_limit"
        metadata = {
            "trigger": trigger, "phase": "round_end", "run_id": v["run_id"],
            "rounds_before": len(candidate.all_rounds),
            "rounds_removed": len(candidate.removed_rounds),
            "rounds_remaining": len(candidate.kept_rounds),
            "memory_mode": "background",
        }
        yield RunEvent(type="context_compression", content="正在压缩本轮结束后的上下文",
                       metadata={**metadata, "status": "started"})

        def record(raw):
            usage = v["_usage_from_dict"](raw)
            for total in (
                v["summary_usage"], v["usage_total"],
                v["window"]["data"]["token_usage"],
                v["archive_window"]["data"]["token_usage"],
                window["data"]["token_usage"],
            ):
                v["_record_provider_request"](total, usage)

        events = []
        result = compact_context(
            **options, runtime_path=v["runtime_path"], previous_cache=cache,
            agent_runner=v["agent_runner"], trigger=trigger,
            cancel_event=v["cancel_event"], response_hook=record,
            event_callback=events.append, source=v["source"], session_id=v["session_id"],
            summarize=v["get_or_create_summary"],
        )
        yield from events
        stats["end_compression"] = {**result.selection.stats(), "summary": result.diagnostics}
        failed = bool(result.diagnostics.get("failed"))
        if not failed:
            cache = result.summary_cache
            diagnostics = result.diagnostics
            window = v["_trim_to_max_rounds"](window, max(1, len(result.selection.kept_rounds)))
            applied = True
        yield RunEvent(
            type="context_compression",
            content="本轮结束后的上下文压缩失败，已保留完整历史" if failed else "本轮结束后的上下文压缩完成",
            metadata={
                **metadata, "status": "failed" if failed else "ready",
                "rounds_removed": len(result.selection.removed_rounds),
                "rounds_remaining": len(result.selection.kept_rounds),
                "target_reached": result.selection.estimated_tokens_after <= policy.input_budget,
            },
        )
        for target in (v["window"], v["archive_window"], window):
            metrics = target["data"].get("round_metrics") or []
            if metrics:
                metrics[-1]["usage"] = dict(v["usage_total"])
    # Snapshot the workspace that will actually be committed, with no additional
    # hypothetical removals that have not been covered by a summary.
    snapshot_selection = select_context(
        **{**options, "window": window}, summary_message=build_summary_message(cache),
        allow_compress=False,
    )
    window["data"]["context"] = {
        **snapshot_selection.stats(), "summary": diagnostics,
        "summary_usage": v["summary_usage"],
        "end_compression": stats.get("end_compression"),
        "round_offset": max(0, v["archive_round_number"] - int(window["data"]["rounds"])),
        "workspace_rounds": int(window["data"]["rounds"]),
        "summary_cache": SUMMARY_STORE_REF if cache is not None else None,
    }
    window["data"]["context_snapshot"] = build_context_snapshot(
        snapshot_selection, system_prompt=v["prompt_bundle"].text,
        summary_message=build_summary_message(cache), capacity_tokens=policy.token_limit,
    )
    return FinalizedContext(window, cache, diagnostics, stats, applied)
