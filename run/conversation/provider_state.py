"""State transfer boundary for the provider exchange stage."""

from __future__ import annotations

from typing import Any

_FINAL_STATE_KEYS = (
    "prompt_bundle", "system_message", "all_text", "all_reasoning", "final_metadata", "completed",
    "task_plan_boundary", "reasoning_selection", "reasoning_extra", "stream",
    "guidance_channel", "retry_state", "pending_guidance_ack",
    "remote_guidance_assets", "protocol_parent_request_id", "messages",
    "usage_total", "tool_records", "pending_tool_calls", "provider_responses",
    "durable_provider_responses", "consumed_guidance",
    "consumed_guidance_details", "guidance_messages_for_retry",
    "tool_argument_retry_count", "last_provider_input_tokens",
    "last_sent_local_tokens", "context_selection", "context_stats",
    "summary_cache", "summary_diagnostics", "compression_memory",
    "compression_usage", "window", "runtime_path",
)

def finalize_provider_loop(state: Any, local_values: dict[str, Any]) -> None:
    state.values.update({key: local_values[key] for key in _FINAL_STATE_KEYS})
    # Only a normally completed exchange releases the outer orchestration loop.
    state.stop_main = False


def initialize_provider_exchange(values: dict[str, Any]) -> dict[str, Any]:
    resolve_reasoning_selection = values["resolve_reasoning_selection"]
    request = values["request"]
    runtime_provider = values["runtime_provider"]
    provider = values["provider"]
    cancel_event = values["cancel_event"]
    copy = values["copy"]
    summary_usage = values["summary_usage"]
    compression_usage = values["compression_usage"]
    usage_total = values["usage_total"]
    reasoning_selection = resolve_reasoning_selection(
        values["config"], runtime_provider, provider, cancel_event=cancel_event
    )
    reasoning_extra = (
        {"reasoning_effort": reasoning_selection.effort}
        if reasoning_selection.enabled and reasoning_selection.effort
        else {"reasoning_enabled": False}
    )
    usage_total.clear()
    retry_usage_base = request.get("_retry_usage_base")
    if isinstance(retry_usage_base, dict):
        usage_total.update(copy.deepcopy(retry_usage_base))
        if summary_usage.get("total_tokens", 0) or summary_usage.get("provider_request_count", 0):
            values["_merge_usage"](usage_total, values["_usage_from_dict"](summary_usage))
    else:
        usage_total.update(copy.deepcopy(summary_usage))
    if compression_usage.get("provider_request_count", 0):
        values["_record_provider_request"](usage_total, values["_usage_from_dict"](compression_usage))
    recovery_map = values["recovery_map"]
    return {
        "reasoning_selection": reasoning_selection,
        "reasoning_extra": reasoning_extra,
        "stream": bool(request.get("stream", runtime_provider.get("stream", False))),
        "guidance_channel": request.get("_guidance_queue"),
        "retry_state": request.get("_retry_state"),
        "pending_guidance_ack": [],
        "remote_guidance_assets": {},
        "protocol_parent_request_id": None,
        "usage_event": (values["RunEvent"](type="usage", usage=dict(summary_usage), metadata={"phase": "context_summary"}) if summary_usage.get("total_tokens", 0) else None),
        "seen_calls": ({signature: copy.deepcopy(item["result"]) for signature, item in recovery_map.items() if item.get("replay_policy") == "reuse"} if recovery_map else {}),
        "blocked_recovery": {signature: item for signature, item in recovery_map.items() if item.get("replay_policy") == "blocked"},
    }
