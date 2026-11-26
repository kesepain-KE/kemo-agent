"""Mailbox drains, runtime limits and terminal metadata for orchestration."""
from __future__ import annotations

import copy
import hashlib
import queue
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast
from contextlib import closing

from events import RunEvent

from events import error_event
from provider.protocol.diagnostics import sanitize_provider_diagnostic
from provider.protocol.models import MessageItem, ReasoningItem, RefusalContent, TextContent, ToolCallItem
from run.conversation.provider_projection import final_answer_messages
from run.infra import EngineError

if TYPE_CHECKING:
    from run.agents import TaskCoordinator
    from run.cards import IntegrationStore
    from run.extensions import MediaInputHost
    from run.retry import AttemptToolDedup
    from run.tools import (
        ConsecutiveIdenticalToolCallTracker, ConsecutiveToolFailureTracker, ShellInbox, ToolDefinition, ToolRuntime,
    )


def record_tool_result(
    event: RunEvent, *, integration: IntegrationStore, iteration: int,
    async_execution_ids: set[str], live_signatures: dict[str, str],
    identical: ConsecutiveIdenticalToolCallTracker, failures: ConsecutiveToolFailureTracker,
    seen: AttemptToolDedup, tool_records: list[dict[str, Any]],
) -> Any:
    """Record the receipt and update duplicate protection for terminal results."""
    from run.cards import json_text
    from run.tools import tool_call_signature

    raw = event.result
    status = str(event.metadata.get("status") or "")
    synthetic_statuses = {
        "blocked", "duplicate_blocked", "duplicate_reused",
        "identical_call_blocked", "invalid_arguments",
        "retry_reuse_blocked", "temporarily_unavailable", "not_executed",
    }
    payload = copy.deepcopy(raw)
    event.result = payload
    arguments = event.arguments or {}
    signature = tool_call_signature(event.tool_name, arguments)
    execution_live = bool(event.metadata.get("execution_live"))
    error = raw.get("error") if isinstance(raw, dict) else None
    error = error if isinstance(error, dict) else {}
    capacity_limited = error.get("category") == "execution_capacity"
    result_too_large = error.get("category") == "result_too_large"
    if not execution_live:
        async_execution_ids.discard(event.metadata["execution_id"])
        # Synthetic duplicate receipts have their own IDs; only the original
        # execution's terminal receipt may release its fence.
        if live_signatures.get(signature) == event.metadata.get("execution_id"):
            live_signatures.pop(signature, None)
        if status not in synthetic_statuses and not capacity_limited:
            identical.record(event.tool_name, arguments)
            failures.record(event.tool_name, succeeded=payload.get("ok") is True or result_too_large)
            seen.remember(
                event.tool_name, arguments, payload, iteration,
                retain_failure_for_retry=(status == "failed"),
            )
        event.metadata["consecutive_identical_calls"] = (
            identical.count if signature == identical.signature else 0
        )
    provider_call_id = "callid_" + hashlib.sha256(json_text([
        integration.key, integration.id, event.tool_call_id,
    ]).encode("utf-8")).hexdigest()
    tool_records.append({"id": event.tool_call_id,
                         "provider_call_id": provider_call_id,
                         "name": event.tool_name, "arguments": event.arguments,
                         "result": payload, **event.metadata})
    return payload


def wait_for_dependencies(
    coordinator: TaskCoordinator, *, cancel_event: threading.Event, channel: Any,
    integration: IntegrationStore, tool_runtime: ToolRuntime, shell: ShellInbox,
    pending_handoffs: set[str], allow_notifications: bool, plan_call_id: str | None,
) -> bool:
    plan_waiting = plan_call_id is not None

    def wake():
        size = getattr(channel, "qsize", None)
        return (tool_runtime.poll_decisions()
                or tool_runtime.changed.is_set()
                or bool(callable(size) and size())
                or bool(allow_notifications and not plan_waiting and shell.ready()))

    def waiting_tools():
        if not pending_handoffs:
            return integration.live_executions()
        # A commit can clear execution_live before its receipt
        # can be verified and published. Wait for that event too.
        tools = {tool["id"]: tool for tool in integration.live_executions()}
        for key in pending_handoffs - tools.keys():
            tools[key] = {
                "id": key, "status": (
                    "running" if tool_runtime.shell_execution(key) is not None else "result_commit_pending"
                ),
            }
        return list(tools.values())

    return coordinator.wait(
        cancel_event, wake=wake, pending=waiting_tools,
        allow_notifications=allow_notifications, heartbeat_only=plan_waiting, plan_call_id=plan_call_id,
    )


def runtime_dynamic_tail(tail, integration, coordinator, *, dispatched, child_notice, shell_notice):
    """Attach live dependencies and notices to the request-only state."""
    if dispatched:
        pending = [*integration.live_executions(), *coordinator.pending_dependencies()]
        tail["async_tasks"] = {
            "pending_count": len(pending),
            "completion_blocked": bool(pending),
            "pending_tasks": pending[:80],
            "pending_tasks_truncated": len(pending) > 80,
        }
    if child_notice:
        tail["subagent_events"] = {"content": child_notice["content"], "external_data": True}
    if shell_notice:
        tail["shell_events"] = {"content": shell_notice["content"], "external_data": True}
    return tail


def positive(config: dict, name: str, default: int) -> int:
    value = config.get(name, default)
    if type(value) is not int or value < 1:
        raise EngineError(f"{name} 必须是正整数")
    return value


def drain(channel, *, closing=False) -> list:
    method = getattr(channel, "drain_or_close" if closing else "drain", None)
    if callable(method):
        return cast(list, method())
    values = []
    while channel is not None:
        try:
            values.append(channel.get_nowait())
        except queue.Empty:
            break
    return values


def close_mailbox(channel) -> list:
    method = getattr(channel, "close_and_drain", None)
    if callable(method):
        return cast(list, method())
    close = getattr(channel, "close", None)
    if callable(close):
        close()
    return drain(channel)


def native_tools(registry, excluded=()):
    return registry.protocol_tools(exclude=set(excluded))


def response_payloads(
    response, *, model: str, allow_final: bool = True,
) -> tuple[dict | None, dict | None]:
    """Separate commentary from a final candidate without rewriting RAW later."""
    output = [item for item in response.output if not isinstance(item, ToolCallItem)]
    final_ids = (
        {item.id for item in final_answer_messages(response)}
        if allow_final else set()
    )

    def payload(items):
        if not items:
            return None
        messages = [item for item in items if isinstance(item, MessageItem)]
        states = [item for item in items if isinstance(item, ReasoningItem)]
        phases = [getattr(item.phase, "value", item.phase) for item in messages]
        phase = phases[0] if phases and phases[0] is not None and all(
            value == phases[0] for value in phases
        ) else None
        return {
            "text": "\n".join(
                "".join(block.text for block in item.content
                        if isinstance(block, (TextContent, RefusalContent)))
                or item.refusal or "" for item in messages),
            "content": [block.model_dump(mode="json", exclude_none=True)
                        for item in messages for block in item.content],
            "reasoning": "\n".join(item.content or item.summary or "" for item in states),
            "response_id": response.id, "provider_model": model,
            "provider_output": [item.model_dump(mode="json", exclude_none=True) for item in items],
            "provider_state": [item.model_dump(mode="json", exclude_none=True) for item in states],
            **({"phase": phase} if phase is not None else {}),
        }

    if not final_ids:
        return payload(output), None
    return (
        payload([item for item in output if isinstance(item, MessageItem) and item.id not in final_ids]),
        payload([item for item in output if not isinstance(item, MessageItem) or item.id in final_ids]),
    )


def build_failure_metadata(
    error, *, usage, context, guidance, argument_corrections, media_inputs,
    request_diagnostics, receipt, live_tools, cancelled, elapsed_ms: int,
) -> dict:
    """Keep provider evidence and unfinished work in the durable run receipt."""
    unfinished = [*(receipt.get("unfinished_tasks") or []), *live_tools]
    safe_error = sanitize_provider_diagnostic(error_event(error, phase="provider").error)
    if isinstance(safe_error, dict) and safe_error.get("phase") == "provider":
        safe_error["message"] = "Provider 请求失败；保留错误类别和状态码供诊断"
    return {
        "usage": copy.deepcopy(usage), "context": context,
        "guidance_details": guidance,
        "tool_argument_retries": argument_corrections,
        "error": safe_error,
        "failed_request_id": getattr(error, "failed_request_id", None),
        "partial_response": getattr(error, "partial_response", False),
        "media_inputs": copy.deepcopy(media_inputs),
        "provider_request_diagnostics": copy.deepcopy(request_diagnostics),
        **receipt,
        "elapsed_ms": elapsed_ms,
        "status": "partial" if receipt or live_tools else ("cancelled" if cancelled else "failed"),
        "unfinished_tasks": unfinished[:80],
        "unfinished_task_count": int(receipt.get("unfinished_task_count") or 0) + len(live_tools),
        "unfinished_tasks_truncated": bool(receipt.get("unfinished_tasks_truncated"))
            or len(unfinished) > 80,
        "retryable": False,
        **({"provider_error": error_event(error, phase="provider").error}
           if getattr(error, "cancelled", False) is True else {}),
    }


def call_is_explicitly_async(call) -> bool:
    """Only model-selected detached work may continue without its first result."""
    arguments = call.arguments if isinstance(call.arguments, dict) else {}
    if call.name == "shell":
        return (
            arguments.get("action") == "run"
            and arguments.get("background") is True
            and arguments.get("completion_mode") == "detached"
        )
    if call.name == "subagent_dispatch":
        action = arguments.get("action")
        # Legacy synchronous dispatches still need their real tool receipt.
        # The built-in submission receipt does not wait for the child itself.
        return action == "split" or (
            action in {"call", "resume"} and arguments.get("wait") is not True
            and not (action == "call" and arguments.get("agent") == "task_plan")
        )
    if call.name == "user_decision":
        return arguments.get("action") == "ask" and arguments.get("mode") == "async"
    return False


def prepare_request_with_events(prepare, *, run_id: str, trigger: str, on_started):
    """Run the synchronous request barrier while surfacing its compaction start."""
    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def report_started(details):
        events.put(("started", details))

    def execute():
        try:
            events.put(("result", prepare(report_started)))
        except BaseException as error:
            events.put(("error", error))

    worker = threading.Thread(target=execute, daemon=True, name="card-request-barrier")
    worker.start()
    while True:
        kind, value = events.get()
        if kind == "started":
            on_started(value)
            yield RunEvent(type="context_compression", content="正在整理对话上下文", metadata={
                "status": "started", "trigger": trigger,
                "run_id": run_id, **value,
            })
            continue
        worker.join()
        if kind == "error":
            raise value
        return value


def duplicate_execution_should_block(
    integration,
    execution_id: str,
) -> bool:
    return any(
        item.get("execution_id") == execution_id
        for item in integration.live_executions()
    )


def settle_tool_without_execution(
    call: ToolCallItem, *, signature: str, integration: IntegrationStore, tool_runtime: ToolRuntime,
    batch_signatures: set[str], live_signatures: dict[str, str], failures: ConsecutiveToolFailureTracker,
    seen: AttemptToolDedup, identical: ConsecutiveIdenticalToolCallTracker, retry_recovery: bool,
) -> bool:
    if signature in batch_signatures:
        tool_runtime.record(call, {"ok": False, "execution": {"executed": False},
            "error": {"code": "duplicate_blocked",
                      "message": "同一工具批次已提交相同调用；本次未再次启动，也未复用前次结果"}},
            status="duplicate_blocked")
        return True
    batch_signatures.add(signature)
    if failures.is_unavailable(call.name):
        tool_runtime.record(call, {
            "ok": False,
            "error": {
                "code": "temporarily_unavailable",
                "exception_type": "ToolTemporarilyUnavailable",
                "consecutive_failures": failures.count,
                "temporarily_unavailable": True,
                "message": f"工具 {call.name} 本次 Run 内已连续失败，暂时不可用",
            },
        }, status="temporarily_unavailable")
        return True
    if signature in live_signatures:
        active_execution_id = live_signatures[signature]
        if duplicate_execution_should_block(integration, active_execution_id):
            tool_runtime.record(call, {"ok": False, "execution": {"executed": False},
                "error": {"code": "duplicate_blocked",
                          "message": "相同调用仍在执行；本次未再次启动工具",
                          "execution_id": active_execution_id}},
                status="duplicate_blocked")
            return True
        live_signatures.pop(signature, None)
    dedup = seen.check(call.name, call.arguments, identical, retry_recovery=retry_recovery)
    if dedup is not None:
        tool_runtime.record(call, dedup[0], status=dedup[1])
        return True
    return False


def configure_tool_call(
    call: ToolCallItem, definition: ToolDefinition, context: dict[str, Any], *,
    request: dict[str, Any], integration: IntegrationStore, coordinator: TaskCoordinator,
    media: MediaInputHost, prompt: str, tool_records: list[dict[str, Any]], run_id: str,
) -> Callable[[Any], Any] | None:
    """Attach the ownership and payload handling needed by this tool."""
    if call.name == "external_message":
        context["transport_registry"] = request.get("_transport_registry")
    if call.name == "user_decision":
        integration.checkpoint_decisions(request)
    if call.name == "shell":
        coordinator.ensure_started()
    if call.name == "multimodal":
        context.update(user_text=prompt, tool_records=copy.deepcopy(tool_records))
    if call.name == "subagent_dispatch":
        callback = request.get("_subagent_event_callback")
        if callable(callback):
            context["event_callback"] = lambda event: callback(replace(
                event, tool_call_id=call.call_id,
                metadata={**event.metadata, "parent_run_id": run_id}))
        if call.arguments.get("action") == "call":
            context["task_ownership_factory"] = lambda: coordinator.ownership(call.call_id)
        elif call.arguments.get("action") in {"resume", "guide", "split"}:
            context["task_ownership"] = coordinator.ownership(call.call_id)
    if call.name == "attach_media":
        return lambda value: media.accept(
            definition, call.arguments, value, call_id=call.call_id, tool_records=tool_records)
    return None


def observed_exchange(stream, *, store, source, session_id, integration_id, integration,
                      observed_text, observed_reasoning, usage_total,
                      request_diagnostics, native_request, bundle, iteration):
    from run.infra import record_provider_request, usage_from_dict
    from run.history import session_statistics
    from run.conversation.request_diagnostics import provider_request_diagnostic
    with closing(stream):
        provider_retry_recovery = False
        while True:
            try:
                event = next(stream)
            except StopIteration as stopped:
                accepted = stopped.value
                break
            if event.type == "tool_call_start":
                # Proposed calls become execution events only after
                # validation and durable ToolRuntime registration.
                continue
            if event.type == "retrying":
                provider_retry_recovery = True
                if observed_text or observed_reasoning:
                    store.append(source, session_id, integration_id, "agent_text", {
                        "text": "".join(observed_text), "reasoning": "".join(observed_reasoning),
                        "incomplete": True, "retry_snapshot": True,
                        "request_id": event.metadata["failed_request_id"],
                    }, level="interim")
                observed_text.clear()
                observed_reasoning.clear()
                failed_usage = event.internal.get("failed_usage")
                if failed_usage is not None:
                    record_provider_request(usage_total, usage_from_dict(failed_usage))
                request_diagnostics.append(provider_request_diagnostic(
                    event.internal.get("provider_request", native_request), bundle, iteration=iteration,
                    attempt=int(event.metadata.get("retry_attempt") or 0) + 1,
                ))
            if event.type == "text_delta":
                observed_text.append(event.content)
            elif event.type == "reasoning_delta":
                observed_reasoning.append(event.content)
            elif event.type == "usage" and event.usage is not None:
                # Rebuild from completed requests plus this request's latest
                # snapshot, so repeated usage events never add the request twice.
                live_usage = copy.deepcopy(usage_total)
                record_provider_request(live_usage, usage_from_dict(event.usage))
                integration.update_usage(live_usage)
                event.metadata["session_cache_hit_rate"] = session_statistics(
                    store.root, store.user, source, session_id,
                )["usage"].get("cache_hit_rate")
            yield event
    return accepted, provider_retry_recovery


def request_context_snapshot(barrier, *, capacity, compression_ratio, request_output_limit):
    occupancy = barrier.occupancy
    history = barrier.snapshot.history
    summary_tokens = sum(
        card.view_tokens for card in history.cards if card.view_kind == "summary"
    )
    total_tokens = occupancy.total
    return {
        "available": True,
        "source": "cardstream",
        "measurement": "local_estimate",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "system_prompt_tokens": occupancy.fixed_tokens,
        "tool_schema_tokens": occupancy.schema_tokens,
        "conversation_tokens": occupancy.history_tokens - summary_tokens,
        "summary_tokens": summary_tokens,
        "other_tokens": occupancy.dynamic_tokens,
        "total_tokens": total_tokens,
        "capacity_tokens": capacity["configured_token_limit"],
        "input_limit_tokens": occupancy.input_limit,
        "input_limit_source": capacity["input_limit_source"],
        "context_window_tokens": capacity["context_window_tokens"],
        "context_window_source": capacity["context_window_source"],
        "output_reserve_tokens": capacity["output_reserve"],
        "request_output_limit_tokens": request_output_limit,
        "capability_unknown": capacity["capability_unknown"],
        "compression_threshold": int(occupancy.input_limit * .8),
        "compression_ratio": compression_ratio,
        "compression_target_tokens": int(occupancy.history_tokens * compression_ratio),
        "percent": round(total_tokens * 100 / capacity["configured_token_limit"], 4),
        "cards_active": len(history.cards),
        "last_seq": history.last_seq,
    }


def account_compression_failure(error, usage_total, context_stats):
    from run.infra import merge_usage_dict
    usage = getattr(error, "compression_usage", None)
    if usage:
        merge_usage_dict(usage_total, usage)
        context_stats["compression_input_tokens"] += int(usage.get("input_tokens") or 0)
        context_stats["compression_output_tokens"] += int(usage.get("output_tokens") or 0)
    context_stats["compression_latency"] += int(getattr(error, "compression_latency_ms", 0))


def prepare_runtime_request(*, store, source, session_id, provider, config, runtime,
                            request_system_prompt, dynamic_tail, native_tools, tool_wire,
                            virtual_media, input_limit, ratio, retry, cancel_event, settings,
                            capabilities, force_compact, skip_preventative, run_id, trigger,
                            compression_started, usage_total, context_stats, overflow_error,
                            extract_memory=True):
    from run.cards import CardCapacityError, ProviderSummarizer, prepare_request
    from run.infra import merge_usage_dict
    summarizer = ProviderSummarizer(
        provider, config, model=runtime["model"], system_prompt=request_system_prompt,
        tools=native_tools, input_limit=input_limit, retry=retry, cancel_event=cancel_event,
        timeout=float(settings.get("compression_timeout_seconds", 180)), meter=store.meter,
        capabilities=capabilities, configured_token_limit=positive(settings, "token_limit", 1000000),
        calibrate=store.calibrated_tokens,
    )
    def prepare_barrier(on_compaction_start):
        return prepare_request(
            store, source, session_id, system_prompt=request_system_prompt, dynamic_tail=dynamic_tail,
            tools=tool_wire, input_limit=input_limit, summarize=summarizer, ratio=ratio,
            force_compact=force_compact,
            skip_preventative=skip_preventative,
            virtual_items=virtual_media, transport=str(runtime.get("type") or ""), model=runtime["model"],
            on_compaction_start=on_compaction_start,
        )
    def mark_compression_started(details):
        compression_started.update(details, trigger=trigger)
    try:
        barrier = yield from prepare_request_with_events(
            prepare_barrier, run_id=run_id,
            trigger=trigger, on_started=mark_compression_started,
        )
    except CardCapacityError as error:
        if overflow_error is not None:
            account_compression_failure(error, usage_total, context_stats)
            raise overflow_error from error
        raise
    if barrier.compaction_error is not None:
        account_compression_failure(barrier.compaction_error, usage_total, context_stats)
        compression_started.clear()
        yield RunEvent(type="context_compression", content="整理暂未完成，原上下文仍可用，继续处理", metadata={
            "status": "failed", "trigger": trigger, "run_id": run_id,
            "continued": True, "raw_preserved": True})
    if barrier.compaction is not None:
        compaction = barrier.compaction
        compression_started.clear()
        context_stats.update(absorbed_cards=compaction.absorbed_count,
                             history_tokens_before=compaction.before_tokens,
                             history_tokens_after=compaction.after_tokens)
        context_stats["compression_latency"] += compaction.latency_ms
        context_stats["compression_input_tokens"] += int(compaction.usage.get("input_tokens") or 0)
        context_stats["compression_output_tokens"] += int(compaction.usage.get("output_tokens") or 0)
        merge_usage_dict(usage_total, compaction.usage)
        if extract_memory:
            from run.history import queue_memory_extraction
            queue_memory_extraction(store.root, store.user, source, session_id,
                                    target_seq=barrier.snapshot.history.last_seq,
                                    reason="cardstream_compaction")
        yield RunEvent(type="context_compression", metadata={
            "status": "ready", "trigger": trigger,
            "run_id": run_id,
            "history_tokens_before": compaction.before_tokens,
            "history_tokens_after": compaction.after_tokens,
            "absorbed_cards": compaction.absorbed_count,
            "summary_card_id": compaction.summary_card_id,
            "view_epoch": compaction.snapshot.view_epoch,
            "compression_latency": compaction.latency_ms,
            "latency_unit": "ms",
        })
    context_stats.update(snapshot_id=barrier.snapshot.snapshot_id,
                         occupancy=barrier.occupancy.total, wire_tokens=barrier.wire_tokens)
    return barrier
