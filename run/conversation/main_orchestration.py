"""CardStream main runtime: event commits, request barrier and real completion.

No window loading, round selection, terminal history copying or legacy context
recovery participates in this path. A Provider response is an input to the
integration state machine, not its completion authority.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import logging
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from events import RunEvent, error_event
from provider.protocol.models import (
    MessageItem, ToolCallItem,
)
from provider.retry import RetryCancelled, RetryManager
from provider.token_usage import reported_token_fields
from run.agents import TaskCoordinator
from run.cards import (
    CardMeter, CardStore, IntegrationStore, estimate_request_tokens,
    json_text,
)
from run.config import build_prompt_bundle, load_config, read_dynamic_tail, refresh_memory_prompt_bundle
from run.extensions import MediaInputHost, context_input_budget, resolve_reasoning_selection
from run.infra import EngineError, new_usage_total, record_provider_request, usage_from_dict
from run.memory import MemoryStore, memory_extraction_mode
from run.retry import (
    AttemptToolDedup,
    HOST_CONTROL_EXCEPTIONS,
)
from run.tasks import detect_task_plan_creation_boundary, task_plan_creation_batch
from run.tools import (
    ConsecutiveIdenticalToolCallTracker, ConsecutiveToolFailureTracker,
    ShellInbox, ToolRegistry, ToolRuntime, apply_runtime_tool_policy,
    invalid_tool_name, response_invalid_tool_arguments_error,
    system_prompt_with_tool_argument_repair, tool_call_signature,
    validate_tool_call_batch,
)
from run.conversation.guidance import normalize_guidance
from run.conversation.guidance_runtime import guidance_failure_details, prepare_guidance
from run.conversation.media_routing import prepare_provider_request
from run.conversation.provider_exchange import exchange
from run.conversation.provider_events import is_context_length_exceeded
from run.conversation.request_input import content_display, request_content_blocks
from run.conversation.request_diagnostics import provider_request_diagnostic, build_conversation_request
from run.conversation.runtime_helpers import (
    close_mailbox as _close, drain as _drain, native_tools as _native_tools,
    positive as _positive, build_failure_metadata, response_payloads,
    call_is_explicitly_async as _call_is_explicitly_async,
    duplicate_execution_should_block as _duplicate_execution_should_block, observed_exchange,
    account_compression_failure, prepare_runtime_request, request_context_snapshot,
    runtime_dynamic_tail,
)

def run_conversation(request, *, root: Path, provider_factory, tool_registry_factory,
                     cancel_event: threading.Event):
    user, source, session_id = (request[key].strip() for key in ("user", "source", "session_id"))
    run_id = str(request.get("run_id") or "run_" + uuid4().hex)
    integration_id = "integration_" + uuid4().hex
    channel = request.get("_guidance_queue")
    integration = tool_runtime = coordinator = shell = media = None
    finished = False
    cleanup_attempted = False
    request_inflight = False
    compression_started: dict[str, Any] = {}
    consumed_guidance: list[dict] = []
    pending_guidance: list[tuple[int, Any]] = []
    preparing_guidance: list[Any] = []
    guidance_seqs: dict[str, int] = {}
    usage_total = new_usage_total()
    all_text, all_reasoning, tool_records = [], [], []
    observed_text, observed_reasoning = [], []
    request_diagnostics = []
    context_stats: dict[str, Any] = {
        "engine": "cardstream", "storage": "cards.sqlite3", "provider_latency": 0,
        "compression_latency": 0, "compression_input_tokens": 0, "compression_output_tokens": 0,
    }
    final_payload = None
    overflow_attempts = overflow_wire = 0
    overflow_error = None
    preventative_retry_at = 0.0
    started = time.monotonic()
    try:
        compress_only = bool(request.get("compress_only"))
        content_blocks = [] if compress_only else request_content_blocks(request)
        if not compress_only and not content_blocks and not request.get("uploaded_files"):
            raise EngineError("请求必须包含非空 prompt、content[] 或 uploaded_files")
        config = load_config(user, root)
        configured_provider = config.get("provider") or {}
        store = CardStore(root, user, meter=CardMeter.for_model(
            str(configured_provider.get("type") or ""), str(configured_provider.get("model") or "")))
        state = store.ensure_session(source, session_id)
        if state["meter_key"] != store.meter.key:
            store.remeter(source, session_id)
        candidate_integration = IntegrationStore(store, source, session_id, integration_id)
        candidate_integration.start()
        integration = candidate_integration
        prepared = prepare_provider_request(
            request, base=root, user=user, source=source, session_id=session_id,
            content_blocks=content_blocks,
            dependencies=SimpleNamespace(provider_factory=provider_factory), cancel_event=cancel_event,
        )
        config, runtime, provider = prepared.config, prepared.runtime_provider, prepared.provider
        actual_meter = CardMeter.for_model(str(runtime.get("type") or ""), str(runtime.get("model") or ""))
        if store.meter != actual_meter:
            # The tokenizer/meter key is intentionally shared across models;
            # only usage-derived safety calibration follows the active route.
            store.meter = actual_meter
        if store.snapshot(source, session_id).meter_key != actual_meter.key:
            store.remeter(source, session_id)
        settings, tool_settings = config.get("agents") or {}, config.get("tools") or {}
        argument_repairs = tool_settings.get("invalid_tool_arguments_retries", 2)
        if type(argument_repairs) is not int or not 0 <= argument_repairs <= 5:
            raise EngineError("tools.invalid_tool_arguments_retries 必须为 0..5 的整数")
        # An output reservation is always present, even if the upstream default
        # is unknown. Shared context limits subtract it exactly once.
        output_reserve = _positive(settings, "output_reserve", 8192)
        if runtime.get("max_tokens") is not None:
            output_reserve = min(output_reserve, _positive(runtime, "max_tokens", output_reserve))
        reasoning = resolve_reasoning_selection(config, runtime, provider, cancel_event=cancel_event)
        capacity = context_input_budget(
            _positive(settings, "token_limit", 1000000), reasoning.capabilities,
            output_reserve=output_reserve,
        )
        input_limit = capacity["effective_token_limit"]
        output_reserve = capacity["output_reserve"]
        context_stats.update(capacity)
        ratio = settings.get("token_compression_ratio", .3)
        if isinstance(ratio, bool) or not isinstance(ratio, (float, int)) or not 0 < ratio < 1:
            raise EngineError("agents.token_compression_ratio 必须在 0 和 1 之间")
        retry = RetryManager(max_retries=(config.get("provider_runtime") or {}).get("max_retries", 5))
        if request.get("_auto_retry") is False:
            retry = RetryManager(max_retries=0)
        registry = (apply_runtime_tool_policy(tool_registry_factory(root, user), config)
                    if tool_settings.get("enabled", True) else ToolRegistry({}))
        failures = ConsecutiveToolFailureTracker(
            _positive(config.get("history") or {}, "consecutive_tool_fail_limit", 5))
        identical = ConsecutiveIdenticalToolCallTracker(
            _positive(tool_settings, "consecutive_identical_call_limit", 3))
        seen = AttemptToolDedup()
        call_iterations, live_signatures = {}, {}
        memory = MemoryStore(root, user, config)
        bundle = build_prompt_bundle(root, user, config, plugin_manifests=registry.plugin_manifests,
                                     memory_store=memory, source=source, session_id=session_id)
        memory_revision = (memory.prompt_revision()
                           if bundle.diagnostics["memory_prompt_injection"] else None)
        tool_runtime = ToolRuntime(integration, cancel_event=cancel_event)
        request["_cardstream_integration_id"] = integration_id
        request["_cardstream_generation"] = integration.generation
        request["_cardstream_meter_key"] = store.meter.key
        coordinator = TaskCoordinator(root, user, source, session_id, run_id, request)
        shell = ShellInbox(root, user, source, session_id, root_task_id=coordinator.id)
        descriptors = prepared.uploaded_descriptors
        prompt = content_display(content_blocks)
        media = MediaInputHost(
            root=root, user=user, scope=("main", source, session_id, run_id), state={},
            config=config, runtime_provider=runtime, provider=provider, session_id=session_id,
            known=descriptors, user_text=prompt, cancel_event=cancel_event,
            input_limit=input_limit, visible_asset_ids=prepared.direct_asset_ids,
        )
        virtual_media = []
        if prepared.provider_media:
            virtual_media.append(MessageItem.text("user",
                "[本次模型请求已携带用户输入的原生媒体，请现在直接读取；"
                "无需等待最终回复或下一条用户消息。对应 RAW 中的资产引用。]"))
            virtual_media[-1] = virtual_media[-1].model_copy(update={
                "content": [*virtual_media[-1].content, *prepared.provider_media],
            })
        if not compress_only:
            store.append(source, session_id, integration_id, "user_text", {
                "text": prompt, "content": [block.model_dump(mode="json", exclude_none=True)
                                              for block in prepared.durable_user_content_blocks],
                "attachments": prepared.history_attachments,
                "metadata": copy.deepcopy(request.get("_user_metadata") or {}),
            }, level="initial", event_key=f"{integration_id}:initial",
                expected_generation=integration.generation)

        def persist_guidance(values):
            normalized = [item for raw in values if (item := normalize_guidance(raw)) is not None]
            for item in normalized:
                item.id = item.id or "guidance_" + uuid4().hex
                if item.id not in guidance_seqs:
                    safe_detail = guidance_failure_details([item], "received")[0]
                    card = store.append(source, session_id, integration_id, "user_text", {
                        "text": item.text or item.display_text,
                        "attachments": safe_detail["uploaded_files"], "guidance_id": item.id,
                    }, level="guidance", event_key=f"{integration_id}:guidance:{item.id}",
                        expected_generation=integration.generation)
                    guidance_seqs[item.id] = card.seq
            return normalized

        def append_guidance(values):
            nonlocal final_payload
            if not values:
                return
            if final_payload is not None:
                store.append(source, session_id, integration_id, "agent_text", final_payload, level="interim")
                final_payload = None
            # Retain ownership of all drained inputs until RAW commits succeed.
            normalized = persist_guidance(values)
            preparing_guidance.extend(normalized)
            prepared_guidance = prepare_guidance(
                normalized, root=root, user=user, session_id=session_id, config=config,
                runtime_provider=runtime, provider=provider, cancel_event=cancel_event,
                known_descriptors=descriptors,
            )
            descriptors.extend(prepared_guidance.uploaded_descriptors)
            media.visible_asset_ids.update(prepared_guidance.direct_asset_ids)
            for original, item, message in zip(
                normalized, prepared_guidance.inputs, prepared_guidance.messages, strict=True,
            ):
                pending_guidance.append((guidance_seqs[item.id], item))
                preparing_guidance.remove(original)
                blocks = message.get("content")
                if isinstance(blocks, list):
                    # The guidance text is already durably present in the
                    # CardStream user_text card included by prepare_request.
                    # Keep only native provider media in the transient tail;
                    # otherwise one guidance is sent twice in this request,
                    # then once again on every later historical request.
                    media_blocks = [
                        block for block in blocks
                        if isinstance(block, dict) and block.get("type") != "text"
                    ]
                    if media_blocks:
                        virtual_media.append(MessageItem(
                            id="msg_guidance_media_" + uuid4().hex,
                            role="user", content=[{
                                "type": "text", "text":
                                "[本次模型请求已携带运行中新增的原生媒体，请现在直接读取；"
                                f"无需等待最终回复或下一条用户消息。guidance_id={item.id}]",
                            }, *media_blocks],
                        ))

        def metadata(status, **extra):
            snapshot = store.snapshot(source, session_id)
            return {
                "user": user, "source": source, "session_id": session_id, "run_id": run_id,
                "integration_id": integration_id, "status": status,
                "committed": True, "cancelled": status == "cancelled",
                "text": final_payload["text"] if status == "completed" and final_payload else "".join(all_text),
                "reasoning": "".join(all_reasoning),
                "final_item_ids": [item["id"] for item in (final_payload or {}).get("provider_output", [])
                                   if item["type"] == "message"] if status == "completed" else [],
                "tool_calls": dispatched, "usage": copy.deepcopy(usage_total),
                "context": {**context_stats, **capacity, "last_seq": snapshot.last_seq,
                            "history_tokens": snapshot.history_tokens,
                            "view_epoch": snapshot.view_epoch, "cards_active": len(snapshot.cards),
                            "summary": {"exists": any(card.view_kind == "summary" for card in snapshot.cards)}},
                "guidance": [item.get("display_text", "") for item in consumed_guidance],
                "guidance_count": len(consumed_guidance),
                "guidance_details": copy.deepcopy(consumed_guidance),
                "tool_argument_retries": argument_corrections,
                "prompt": copy.deepcopy(bundle.diagnostics),
                "knowledge": {"documents": []},
                "memory": {"injected_files": list(bundle.memory_files), "weighted_files": [],
                           "extraction_mode": memory_extraction_mode(config)},
                "media_inputs": copy.deepcopy(media.audit),
                "provider_request_diagnostics": copy.deepcopy(request_diagnostics),
                "elapsed_ms": round((time.monotonic() - started) * 1000), **extra,
            }

        def close_limited(stop_reason, **extra):
            nonlocal finished
            consumed_guidance.extend(guidance_failure_details(
                [item for _, item in pending_guidance] + preparing_guidance
                + persist_guidance(_close(channel)),
                stop_reason,
            ))
            pending_guidance.clear()
            preparing_guidance.clear()
            receipt = coordinator.finish("limited", defer_terminal=True) or {}
            live_tools = integration.live_executions()
            unfinished = [*(receipt.get("unfinished_tasks") or []), *live_tools]
            status = "partial" if receipt or live_tools else "limited"
            outcome = metadata(status, **{
                **extra, **{key: value for key, value in receipt.items() if key != "status"},
                "stop_reason": receipt.get("stop_reason") or stop_reason,
                "requested_stop_reason": stop_reason,
                "unfinished_tasks": unfinished[:80],
                "unfinished_task_count": int(receipt.get("unfinished_task_count") or 0) + len(live_tools),
                "unfinished_tasks_truncated": bool(receipt.get("unfinished_tasks_truncated"))
                    or len(unfinished) > 80,
                "retryable": False,
            })
            integration.finish(status, final_payload=final_payload, metadata=outcome)
            finished = True
            coordinator.finish("limited")
            return outcome

        iteration, dispatched, invalid_batches, argument_corrections = 0, 0, 0, 0
        repair_pending, repair_tool_name = False, ""
        continuation = True
        while True:
            if cancel_event.is_set():
                raise RetryCancelled("用户停止了当前整合")
            result_events = tool_runtime.events()
            plan_boundary = None
            for event in result_events:
                if event.type == "tool_call_start":
                    yield event
                    if cancel_event.is_set():
                        raise RetryCancelled("用户停止了当前整合")
                    continue
                raw = event.result
                status = str(event.metadata.get("status") or "")
                synthetic_statuses = {
                    "blocked", "duplicate_blocked", "duplicate_reused",
                    "identical_call_blocked", "invalid_arguments",
                    "retry_reuse_blocked", "temporarily_unavailable", "not_executed",
                }
                payload = copy.deepcopy(raw)
                event.result = payload
                signature = tool_call_signature(event.tool_name, event.arguments or {})
                execution_live = bool(event.metadata.get("execution_live"))
                error = raw.get("error") if isinstance(raw, dict) else None
                error = error if isinstance(error, dict) else {}
                capacity_limited = error.get("category") == "execution_capacity"
                result_too_large = error.get("category") == "result_too_large"
                if not execution_live:
                    # Synthetic duplicate receipts have their own IDs; only the
                    # original execution's terminal receipt may release its fence.
                    if live_signatures.get(signature) == event.metadata.get("execution_id"):
                        live_signatures.pop(signature, None)
                    if status not in synthetic_statuses and not capacity_limited:
                        identical.record(event.tool_name, event.arguments or {})
                        failures.record(
                            event.tool_name,
                            succeeded=payload.get("ok") is True or result_too_large,
                        )
                        seen.remember(
                            event.tool_name, event.arguments or {}, payload,
                            call_iterations.get(event.tool_call_id, iteration),
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
                if event.tool_name == "subagent_dispatch" and isinstance(raw, dict):
                    business_result = raw.get("result")
                    if (isinstance(business_result, dict)
                            and business_result.get("status") == "completed"
                            and business_result.get("task_id")):
                        coordinator.acknowledge_sync(business_result["task_id"])
                yield event
                if cancel_event.is_set():
                    raise RetryCancelled("用户停止了当前整合")
                boundary = detect_task_plan_creation_boundary(
                    tool_name=event.tool_name, arguments=event.arguments or {}, result_payload=payload,
                )
                if boundary is not None:
                    plan_boundary = boundary
            if plan_boundary is not None:
                outcome = close_limited(
                    plan_boundary.stop_reason, task_plan_id=plan_boundary.plan_id, plan_id=plan_boundary.plan_id,
                    awaiting_user_approval=plan_boundary.awaiting_user_approval,
                )
                yield RunEvent(type="done", metadata=outcome)
                return
            new_guidance = _drain(channel)
            append_guidance(new_guidance)
            # Child and Shell notifications are runtime state in the virtual
            # tail, not invented user history or an extra persistence mechanism.
            live, unseen = integration.pending()
            # A result may commit after the event drain. Apply its boundary
            # before deciding whether the now-idle integration needs inference.
            if tool_runtime.has_events():
                continue
            child_notice, shell_notice = coordinator.notifications(), shell.take()
            dependencies_pending = bool(live or coordinator.pending() or shell.pending())
            live_tool_waiting = bool(live and not continuation)
            needs_request = continuation or bool(
                (new_guidance or child_notice or shell_notice) and not live_tool_waiting
            )
            needs_request = needs_request or (unseen and not live)
            needs_request = needs_request or (
                final_payload is None and not dependencies_pending
            )
            if not needs_request:
                if dependencies_pending:
                    integration.phase("waiting")
                    pending_jobs = shell.pending()
                    if pending_jobs:
                        yield RunEvent(type="background_job_progress", metadata={
                            "status": "waiting",
                            "jobs": [{"job_id": job["job_id"], "status": job["status"]}
                                     for job in pending_jobs],
                        })
                    def wake():
                        size = getattr(channel, "qsize", None)
                        return (tool_runtime.changed.is_set()
                                or bool(size() if callable(size) else False)
                                or bool(shell.ready()))
                    if not coordinator.wait(
                        cancel_event, wake=wake,
                        pending=integration.live_executions,
                    ):
                        if cancel_event.is_set():
                            raise RetryCancelled("用户停止了当前整合")
                        outcome = close_limited("completion_deadline")
                        yield RunEvent(type="done", usage=usage_total, metadata=outcome)
                        return
                    continue
                # Atomic mailbox closure prevents acknowledged guidance from
                # landing between the final drain and terminal commit.
                last_guidance = _drain(channel, closing=True)
                if last_guidance:
                    append_guidance(last_guidance)
                    continuation = True
                    continue
                coordinator.finish("completed", defer_terminal=True)
                result = metadata("completed")
                integration.finish("completed", final_payload=final_payload, metadata=result)
                finished = True
                coordinator.finish("completed")
                result = metadata("completed")
                store.update_record(source, session_id, {"run_state": "idle", "last_integration": integration_id})
                yield RunEvent(type="done", usage=usage_total, metadata=result)
                return

            if final_payload is not None:
                store.append(source, session_id, integration_id, "agent_text", final_payload, level="interim")
                final_payload = None
            iteration += 1
            native_tools = _native_tools(registry, failures.unavailable)
            request_tools = list(native_tools)
            tool_wire = [item.model_dump(mode="json", exclude_none=True) for item in request_tools]
            if memory_revision is not None:
                revision = memory.prompt_revision()
                if revision != memory_revision:
                    bundle = refresh_memory_prompt_bundle(bundle, root, user, config, memory_store=memory)
                    memory_revision = memory.prompt_revision()

            def dynamic_tail():
                return runtime_dynamic_tail(
                    read_dynamic_tail(root, user, config, source=source, session_id=session_id),
                    integration, coordinator, dispatched=dispatched,
                    child_notice=child_notice, shell_notice=shell_notice,
                )

            request_system_prompt = (
                system_prompt_with_tool_argument_repair(
                    bundle.text,
                    tool_name=repair_tool_name,
                    retry_number=argument_corrections,
                )
                if repair_pending else bundle.text
            )
            media.refresh_items(virtual_media)
            if media.audit:
                yield RunEvent(type="media_input_update", metadata={"media_inputs": copy.deepcopy(media.audit)})
            if cancel_event.is_set():
                raise RetryCancelled("用户停止了当前整合")
            compression_trigger = "provider_overflow" if overflow_wire else (
                "manual" if request.get("compress") else "occupancy")
            barrier = yield from prepare_runtime_request(
                store=store, source=source, session_id=session_id, provider=provider, config=config,
                runtime=runtime, request_system_prompt=request_system_prompt, dynamic_tail=dynamic_tail,
                native_tools=native_tools, tool_wire=tool_wire, virtual_media=virtual_media,
                input_limit=input_limit, ratio=ratio, retry=retry, cancel_event=cancel_event,
                settings=settings, capabilities=reasoning.capabilities,
                force_compact=bool(overflow_wire or request.get("compress") and iteration == 1),
                skip_preventative=time.monotonic() < preventative_retry_at, run_id=run_id,
                trigger=compression_trigger, compression_started=compression_started,
                usage_total=usage_total, context_stats=context_stats, overflow_error=overflow_error,
            )
            if barrier.compaction_error is not None or barrier.compaction is not None:
                preventative_retry_at = time.monotonic() + 60
            if overflow_error is not None:
                if barrier.wire_tokens >= overflow_wire:
                    raise overflow_error
                yield RunEvent(type="retrying", metadata={"phase": "context_recovery",
                    "retry_attempt": overflow_attempts, "max_retries": 3,
                    "failed_request_id": native_request.request_id, "committed": False})
            if compress_only:
                result = metadata("completed", compressed=barrier.compaction is not None)
                integration.continuation(False)
                integration.finish("completed", metadata=result)
                finished = True
                yield RunEvent(type="done", usage=usage_total, metadata=result)
                return
            items = barrier.snapshot.items()
            media.validate_items(items, request_system_prompt, native_tools)
            native_request = build_conversation_request(
                runtime=runtime, system_prompt=request_system_prompt, items=items, tools=request_tools,
                output_reserve=output_reserve, stream=bool(request.get("stream", runtime.get("stream", False))),
                reasoning_enabled=reasoning.enabled, reasoning_effort=reasoning.effort,
                user=user, source=source, session_id=session_id, run_id=run_id,
                integration_id=integration_id, snapshot_id=barrier.snapshot.snapshot_id, iteration=iteration)
            request_diagnostics.append(provider_request_diagnostic(native_request, bundle, iteration=iteration, attempt=1))
            if cancel_event.is_set():
                raise RetryCancelled("用户停止了当前整合")
            yield RunEvent(type="context_window", metadata={
                "context_snapshot": request_context_snapshot(barrier),
            })
            integration.request_started()
            request_inflight = True
            observed_text.clear()
            observed_reasoning.clear()
            try:
                accepted, provider_retry_recovery = yield from observed_exchange(
                    exchange(provider, native_request, config, root=root, user=user,
                             retry=retry, cancel_event=cancel_event, allow_interim=dependencies_pending),
                    store=store, source=source, session_id=session_id, integration_id=integration_id,
                    integration=integration,
                    observed_text=observed_text, observed_reasoning=observed_reasoning,
                    usage_total=usage_total, request_diagnostics=request_diagnostics,
                    native_request=native_request, bundle=bundle, iteration=iteration,
                )
            except Exception as error:
                if not is_context_length_exceeded(error) or overflow_attempts >= 3 or cancel_event.is_set():
                    raise
                integration.request_finished(continuation_required=True)
                request_inflight = False
                if getattr(error, "failed_usage", None):
                    record_provider_request(usage_total, usage_from_dict(error.failed_usage))
                if observed_text or observed_reasoning:
                    store.append(source, session_id, integration_id, "agent_text", {
                        "text": "".join(observed_text), "reasoning": "".join(observed_reasoning),
                        "incomplete": True, "retry_snapshot": True,
                        "request_id": getattr(error, "failed_request_id", native_request.request_id),
                    }, level="interim")
                continuation = True
                overflow_attempts += 1
                overflow_wire = barrier.wire_tokens
                overflow_error = error
                input_limit = max(1, min(input_limit - 1, int(overflow_wire * .7)))
                context_stats["recovery_input_limit"] = input_limit
                continue
            overflow_attempts = 0
            overflow_wire = 0
            overflow_error = None
            response = accepted.response
            repair_pending = False
            repair_tool_name = ""
            context_stats["provider_latency"] += accepted.latency_ms
            context_stats["latency_unit"] = "ms"
            actual_input = response.usage.input_tokens
            if (type(actual_input) is int
                    and "input_tokens" in reported_token_fields(response.usage)):
                store.observe_usage(barrier.snapshot, request_id=accepted.request.request_id,
                                    estimated=estimate_request_tokens(
                                        bundle.text, items, tool_wire), actual=actual_input)
            calls = [item for item in response.output if isinstance(item, ToolCallItem)]
            provider_argument_error = response_invalid_tool_arguments_error(response)
            schemas = {tool.name: tool.parameters for tool in request_tools}
            invalid = validate_tool_call_batch(calls, schemas)
            invalid_batch = bool(
                invalid or any(call.name not in registry.tools for call in calls)
                or provider_argument_error
            )
            integration.request_finished(
                accepted_seq=barrier.snapshot.history.last_seq,
                continuation_required=bool(calls or provider_argument_error),
            )
            request_inflight = False
            coordinator.acknowledge()
            shell.acknowledge()
            # A valid response consumes transient media once; invalid batches
            # retain the original input for correction, with RAW unchanged.
            if not invalid_batch:
                media.acknowledge()
                virtual_media.clear()
            if media.audit:
                yield RunEvent(type="media_input_update", metadata={"media_inputs": copy.deepcopy(media.audit)})
            for seq, item in list(pending_guidance):
                if seq <= barrier.snapshot.history.last_seq:
                    detail = {**item.history_detail(), "ok": True, "status": "applied"}
                    consumed_guidance.append(detail)
                    pending_guidance.remove((seq, item))
                    yield RunEvent(type="guidance_applied", content=item.display_text, metadata=detail)
            record_provider_request(usage_total, usage_from_dict(
                response.usage.model_dump(mode="json", exclude_none=True)))
            interim_payload, final_payload = response_payloads(
                response, model=runtime["model"],
                allow_final=not dependencies_pending,
            )
            for payload in (interim_payload, final_payload):
                if payload is not None:
                    all_text.append(payload["text"])
                    all_reasoning.append(payload["reasoning"])
            if interim_payload is not None:
                store.append(source, session_id, integration_id, "agent_text", interim_payload, level="interim")
            if invalid_batch:
                # Record what the model requested and why no tool ran. Correction
                # is a new inference over these facts; no integration/tool replay.
                for call in calls:
                    tool_runtime.record(call, {
                        "ok": False, "execution": {"executed": False},
                        "error": {"code": "invalid_tool_batch",
                                  "message": "整批未执行。请按当前工具 Schema 修正参数后重新提出调用。"},
                    }, status="invalid_arguments")
                invalid_batches += 1
                if invalid_batches > argument_repairs:
                    raise EngineError("Provider 工具参数纠错次数耗尽；整批未执行，未重放任何副作用")
                argument_corrections += 1
                if provider_argument_error is not None and not calls:
                    repair_pending = True
                    repair_tool_name = invalid_tool_name(provider_argument_error)
                continuation = True
                observed_text.clear()
                observed_reasoning.clear()
                continue
            invalid_batches = 0
            started_executions = []
            batch_signatures: set[str] = set()
            continuation = False
            for call, plan_generation in task_plan_creation_batch(calls, record=tool_runtime.record):
                continuation = (continuation or _call_is_explicitly_async(call)) and not plan_generation
                call_iterations[call.call_id] = iteration
                signature = tool_call_signature(call.name, call.arguments)
                if signature in batch_signatures:
                    tool_runtime.record(call, {"ok": False, "execution": {"executed": False},
                        "error": {"code": "duplicate_blocked",
                                  "message": "同一工具批次已提交相同调用；本次未再次启动，也未复用前次结果"}},
                        status="duplicate_blocked")
                    continue
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
                    continue
                if signature in live_signatures:
                    active_execution_id = live_signatures[signature]
                    if _duplicate_execution_should_block(
                        integration, active_execution_id,
                    ):
                        tool_runtime.record(call, {"ok": False, "execution": {"executed": False},
                            "error": {"code": "duplicate_blocked",
                                      "message": "相同调用仍在执行；本次未再次启动工具",
                                      "execution_id": active_execution_id}},
                            status="duplicate_blocked")
                        continue
                    live_signatures.pop(signature, None)
                dedup = seen.check(call.name, call.arguments, identical,
                                   retry_recovery=provider_retry_recovery)
                if dedup is not None:
                    tool_runtime.record(call, dedup[0], status=dedup[1])
                    continue
                definition = registry.get(call.name)
                context = {
                    "root": str(root), "user": user, "source": source, "session_id": session_id,
                    "run_id": run_id, "integration_id": integration_id,
                    "shell_ownership": shell.ownership(), "tool_timeout": tool_settings.get("timeout", 240),
                    "agent_timeout": (config.get("agent_runtime") or {}).get("default_timeout", 600),
                    "knowledge_scopes": list(prepared.source_policy.direct_knowledge_scopes()),
                    "uploaded_files": copy.deepcopy(descriptors),
                    "task_plan_id": request.get("_task_plan_id"),
                    "task_plan_step_id": request.get("_task_plan_step_id"),
                    "task_plan_mode": request.get("_task_plan_mode"),
                }
                if call.name == "external_message":
                    context["transport_registry"] = request.get("_transport_registry")
                if call.name == "shell":
                    coordinator.ensure_started()
                if call.name == "multimodal":
                    context.update(user_text=prompt, tool_records=copy.deepcopy(tool_records))
                if call.name == "subagent_dispatch":
                    callback = request.get("_subagent_event_callback")
                    if callable(callback):
                        context["event_callback"] = lambda event, call=call: callback(replace(
                            event, tool_call_id=call.call_id,
                            metadata={**event.metadata, "parent_run_id": run_id}))
                    if call.arguments.get("action") == "call":
                        context["task_ownership_factory"] = lambda call=call: coordinator.ownership(call.call_id)
                    elif call.arguments.get("action") in {"resume", "guide"}:
                        context["task_ownership"] = coordinator.ownership(call.call_id)
                transform = None
                if call.name == "attach_media":
                    transform = lambda value, call=call, definition=definition: media.accept(
                        definition, call.arguments, value, call_id=call.call_id, tool_records=tool_records)
                execution_id = tool_runtime.start(call, definition, context=context,
                    timeout=float(tool_settings.get("timeout", 240)), transform=transform)
                started_executions.append(execution_id)
                live_signatures[signature] = execution_id
                dispatched += 1
            tool_runtime.handoff(started_executions)
            observed_text.clear()
            observed_reasoning.clear()
    except BaseException as error:
        if compression_started:
            yield RunEvent(type="context_compression", content="压缩未完成，原有上下文保持不变", metadata={
                "status": "failed", "trigger": str(compression_started.get("trigger") or "occupancy"),
                "run_id": run_id,
                "history_tokens_before": int(compression_started.get("history_tokens_before") or 0),
                "absorbed_cards": int(compression_started.get("absorbed_cards") or 0),
            })
            compression_started.clear()
        if error is not overflow_error and getattr(error, "failed_usage", None):
            record_provider_request(usage_total, usage_from_dict(error.failed_usage))
        account_compression_failure(error, usage_total, context_stats)
        cleanup_attempted = True
        cleanup_errors = []
        def cleanup(operation):
            try:
                return operation()
            except Exception as cleanup_error:
                # Retain the primary error and durable execution fence. Never
                # pretend a failed cleanup committed, nor rerun it in finally.
                cleanup_errors.append({"exception_type": type(cleanup_error).__name__})
                return None
        if integration is not None and not finished:
            if request_inflight:
                cleanup(lambda: integration.request_finished())
            final_guidance = cleanup(lambda: _close(channel)) or []
            if "persist_guidance" in locals():
                final_guidance = cleanup(lambda: persist_guidance(final_guidance)) or final_guidance
            consumed_guidance.extend(guidance_failure_details(
                [item for _, item in pending_guidance] + preparing_guidance + final_guidance,
            "run_cancelled" if cancel_event.is_set() else "run_failed"))
            pending_guidance.clear()
            preparing_guidance.clear()
            cancelled = (isinstance(error, (RetryCancelled, GeneratorExit))
                         or cancel_event.is_set() or getattr(error, "cancelled", False) is True)
            if isinstance(error, GeneratorExit):
                cancel_event.set()
            terminal_status = "cancelled" if cancelled else "failed"
            receipt = (cleanup(lambda: coordinator.finish(terminal_status, defer_terminal=True))
                       if coordinator is not None else None) or {}
            live_tools = (cleanup(integration.live_executions) or [])
            partial_status = bool(receipt or live_tools)
            partial = final_payload or {"text": "".join(observed_text), "reasoning": "".join(observed_reasoning),
                                        "incomplete": True}
            failure_metadata = build_failure_metadata(
                error, usage=usage_total, context=context_stats,
                guidance=consumed_guidance, argument_corrections=locals().get("argument_corrections", 0),
                media_inputs=media.audit if media is not None else [],
                request_diagnostics=request_diagnostics, receipt=receipt, live_tools=live_tools,
                cancelled=cancelled,
            )
            cleanup(lambda: integration.finish(
                failure_metadata["status"],
                final_payload=partial if partial.get("text") or partial.get("reasoning") else None,
                metadata=failure_metadata))
            if not cleanup_errors and coordinator is not None:
                cleanup(lambda: coordinator.finish(terminal_status))
            finished = not cleanup_errors
            if isinstance(error, HOST_CONTROL_EXCEPTIONS) or not isinstance(error, Exception):
                raise
            if (cancelled or partial_status) and finished:
                outcome = (cleanup(lambda: metadata(
                    failure_metadata["status"], **{
                        key: value for key, value in failure_metadata.items()
                        if key not in {"status", "usage", "context", "guidance_details", "media_inputs"}
                    },
                )) if "metadata" in locals() else {
                    **failure_metadata, "integration_id": integration_id,
                })
                if outcome is not None:
                    yield RunEvent(type="done", metadata=outcome)
                    return
        if isinstance(error, HOST_CONTROL_EXCEPTIONS) or not isinstance(error, Exception):
            raise
        event = error_event(error, phase="cardstream")
        event.metadata.update(committed=integration is not None, retryable=False,
                              status="failed", cancelled=cancel_event.is_set(),
                              integration_id=integration_id, context=context_stats,
                              media_inputs=copy.deepcopy(media.audit) if media is not None else [],
                              guidance_details=consumed_guidance, cleanup_errors=cleanup_errors,
                              terminal_committed=finished)
        if event.error is not None and event.metadata.get("retryable") is False:
            event.error["retryable"] = False
        yield event
    finally:
        if not finished and not cleanup_attempted and integration is not None:
            # Generator close is not permission to orphan live execution truth.
            cancel_event.set()
            try:
                if request_inflight:
                    integration.request_finished()
                receipt = coordinator.finish("cancelled", defer_terminal=True) if coordinator is not None else None
                live_tools = integration.live_executions()
                integration.finish("partial" if receipt or live_tools else "cancelled", final_payload=final_payload, metadata={
                    **(receipt or {}),
                    "status": "partial" if receipt or live_tools else "cancelled",
                    "unfinished_tools": live_tools,
                })
                if coordinator is not None:
                    coordinator.finish("cancelled")
            except Exception:
                logging.getLogger(__name__).exception("CardStream generator cleanup failed")
        try:
            _close(channel)
        except Exception:
            logging.getLogger(__name__).exception("CardStream guidance mailbox cleanup failed")
