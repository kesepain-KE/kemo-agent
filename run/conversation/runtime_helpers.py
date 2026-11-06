"""Mailbox drains, runtime limits and terminal metadata for orchestration."""
from __future__ import annotations

import copy
import queue
import threading
from typing import Any
from contextlib import closing

from events import RunEvent

from events import error_event
from provider.protocol.diagnostics import sanitize_provider_diagnostic
from provider.protocol.models import MessageItem, ReasoningItem, ToolCallItem
from run.conversation.provider_projection import final_answer_messages
from run.infra import EngineError


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
        return method()
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
        return method()
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
                "".join(block.text for block in item.content if block.type in {"text", "refusal"})
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
    request_diagnostics, receipt, live_tools, cancelled,
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
        return (
            arguments.get("action") in {"call", "resume"}
            and arguments.get("wait") is False
        )
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
                            compression_started, usage_total, context_stats, overflow_error):
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
