"""Request-local input-limit recovery without replaying completed tools."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Any

from events import RunEvent
from run.context import select_context, build_summary_message
from run.conversation.context_compaction import compact_context


@dataclass(slots=True)
class ContextRecovery:
    result: Any = None
    events: list[Any] | None = None
    memory: Any = None


def recover_provider_context(v, *, retry_count, live_tail, preserve_live_tail, trigger):
    """Only publish a replacement when coverage and request size are valid."""
    policy = replace(v["context_policy"], token_limit=v["effective_tool_context_limit"])
    current_count = len(v["context_selection"].kept_rounds)
    options: dict[str, Any] = dict(
        window=v["window"], policy=policy,
        system_message=v["system_message"],
        current_user_message=v["current_user_message"],
        fixed_suffix_messages=live_tail,
        tools=v["active_tool_schemas"], force_token_compress=True,
        max_history_rounds=current_count,
    )
    candidate = select_context(
        **options, summary_message=build_summary_message(v["summary_cache"]),
    )
    if len(candidate.kept_rounds) >= current_count:
        return ContextRecovery()
    metadata = {
        "trigger": trigger, "run_id": v["run_id"],
        "rounds_before": len(candidate.all_rounds) + v["projected_current_rounds"],
        "rounds_removed": len(candidate.removed_rounds),
        "rounds_remaining": len(candidate.kept_rounds) + v["projected_current_rounds"],
        "memory_mode": "background" if v["queue_compression_memory"] else "synchronous",
        "preserved_live_tool_tail": preserve_live_tail,
        "configured_token_limit": v["context_policy"].token_limit,
        "provider_declared_input_limit": v["declared_provider_input_limit"],
        "effective_token_limit": policy.token_limit,
    }
    started = RunEvent(
        type="context_compression", content="正在按统一策略整理对话上下文",
        metadata={**metadata, "status": "started"},
    )
    events = [started]
    memory = v["compression_memory"]
    if memory is None and not v["queue_compression_memory"]:
        memory = v["_extract_memory_backlog"](
            root=v["base"], user=v["user"], source=v["source"],
            session_id=v["session_id"], directory=v["window_path"],
            window=v["archive_window"], config=v["config"],
            agent_runner=v["agent_runner"], cancel_event=v["cancel_event"],
        )
        usage = memory.get("usage")
        if isinstance(usage, dict) and usage.get("provider_request_count", 0):
            for total in (v["compression_usage"], v["usage_total"]):
                v["_record_provider_request"](total, v["_usage_from_dict"](usage))

    def record_summary(raw):
        for total in (v["summary_usage"], v["usage_total"]):
            v["_record_provider_request"](total, v["_usage_from_dict"](raw))

    result = compact_context(
        **options, runtime_path=v["runtime_path"], previous_cache=v["summary_cache"],
        agent_runner=v["agent_runner"], trigger=trigger,
        cancel_event=v["cancel_event"], response_hook=record_summary,
        event_callback=events.append, source=v["source"], session_id=v["session_id"],
        summarize=v["get_or_create_summary"],
    )
    if trigger == "api_request_size" and not result.diagnostics.get("failed"):
        # Compare the same content envelope on both sides. This is a local
        # progress check, not a claim about the gateway's undisclosed byte cap.
        def content_bytes(messages):
            return len(json.dumps(
                {"messages": messages, "tools": v["active_tool_schemas"]},
                ensure_ascii=False, separators=(",", ":"), default=str,
            ).encode("utf-8"))

        before_bytes = content_bytes(v["messages"])
        after_bytes = content_bytes(result.selection.messages)
        result.diagnostics.update(request_bytes_before=before_bytes, request_bytes_after=after_bytes)
        if after_bytes >= before_bytes:
            result.diagnostics.update(
                failed=True, failure_kind="request_size_not_reduced",
                error="request_size_not_reduced", stage="request_recovery",
            )
    if (
        result.diagnostics.get("failed")
        or result.selection.estimated_tokens_after > policy.token_limit
        or len(result.selection.kept_rounds) >= current_count
    ):
        events.append(RunEvent(
            type="context_compression", content="上下文压缩未能完成，保留原始历史",
            metadata={**metadata, "status": "failed", "diagnostics": result.diagnostics},
        ))
        return ContextRecovery(events=events, memory=memory)
    events.append(RunEvent(
        type="context_compression", content="对话上下文摘要已就绪，继续当前工具链",
        metadata={
            **metadata, "status": "ready",
            "rounds_removed": len(result.selection.removed_rounds),
            "rounds_remaining": len(result.selection.kept_rounds) + v["projected_current_rounds"],
            "target_reached": result.selection.estimated_tokens_after <= policy.input_budget,
            "memory_status": "queued_after_commit" if v["queue_compression_memory"]
            else str((memory or {}).get("status") or ""),
        },
    ))
    return ContextRecovery(result=result, events=events, memory=memory)
