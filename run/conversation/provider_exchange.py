"""Provider streaming, recovery, and tool exchange implementation."""
from __future__ import annotations
from run.retry import HOST_CONTROL_EXCEPTIONS, mark_retry_exhausted

from events import RunEvent as _AnnotationRunEvent
from provider.schema import ToolCall as _AnnotationToolCall

from provider.schema import Usage

from run.tasks import TaskPlanCreationBoundary
from typing import Any
from run.conversation.provider_state import finalize_provider_loop, initialize_provider_exchange
from run.conversation.tool_batch import ToolBatchContext, execute_tool_batch, raise_retryable_tool_failure
from run.conversation.request_diagnostics import record_provider_request_diagnostic, tool_context_guard, project_context_input, record_context_checkpoint, request_output_limit
from run.conversation.media_inputs import initialize_media_inputs
from run.conversation.provider_tasks import prepare_shell_request, settle_children
from run.conversation.provider_events import is_request_size_exceeded
def run_provider_exchange(state):
    values = state.values
    dependencies = state.dependencies
    (_remember_retry_guidance, _replace_primary_system_message, prepare_guidance, refresh_dynamic_prompt_bundle) = map(dependencies.__getitem__, ("_remember_retry_guidance", "_replace_primary_system_message", "prepare_guidance", "refresh_dynamic_prompt_bundle"))
    (ChatRequest, ContextLengthExceededError, EngineError, ProviderCongestionError, ProviderError, RunEvent, SUMMARY_CHUNK_TOKEN_BUDGET, SUMMARY_MAX_OUTPUT_TOKENS, ToolCall, _close_guidance, _committed_failure_event, _drain_guidance, _drain_or_close_guidance, _ensure_fixed_content_fits, _event_provider_response, _extract_memory_backlog, _invalid_tool_name, _is_context_length_exceeded, _is_invalid_tool_arguments_error, _messages_with_tool_argument_repair, _provider_events, _raise_if_context_length_exceeded, _record_provider_request, _retry_recovery_messages, _retry_recovery_tool_records, _tool_context_diagnostics, _tool_schema_map, _usage_from_dict, _validate_tool_call_batch, build_summary_message, chat_request_to_kemo, copy, error_event, estimate_messages_tokens, estimate_tools_tokens, get_or_create_summary, provider_request_slot, replace, select_context, uuid) = map(dependencies.__getitem__, ('ChatRequest', 'ContextLengthExceededError', 'EngineError', 'ProviderCongestionError', 'ProviderError', 'RunEvent', 'SUMMARY_CHUNK_TOKEN_BUDGET', 'SUMMARY_MAX_OUTPUT_TOKENS', 'ToolCall', '_close_guidance', '_committed_failure_event', '_drain_guidance', '_drain_or_close_guidance', '_ensure_fixed_content_fits', '_event_provider_response', '_extract_memory_backlog', '_invalid_tool_name', '_is_context_length_exceeded', '_is_invalid_tool_arguments_error', '_messages_with_tool_argument_repair', '_provider_events', '_raise_if_context_length_exceeded', '_record_provider_request', '_retry_recovery_messages', '_retry_recovery_tool_records', '_tool_context_diagnostics', '_tool_schema_map', '_usage_from_dict', '_validate_tool_call_batch', 'build_summary_message', 'chat_request_to_kemo', 'copy', 'error_event', 'estimate_messages_tokens', 'estimate_tools_tokens', 'get_or_create_summary', 'provider_request_slot', 'replace', 'select_context', 'uuid'))
    (prompt_bundle, system_message, compression_memory, context_selection, context_stats, messages, summary_cache, all_text, all_reasoning, agent_runner, agent_timeout, archive_window, base, cancel_event, commit_cancelled_round, commit_failed_round, commit_terminal_round, compression_usage, config, consumed_guidance, consumed_guidance_details, context_policy, current_user_message, durable_provider_responses, failure_limit, failures, identical_call_limit, identical_calls, invalid_tool_arguments_retry_limit, max_provider_iterations, max_tool_calls, observed_reasoning, observed_text, pending_tool_calls, projected_current_rounds, provider, provider_responses, queue_compression_memory, recovery_map, registry, request, round_state, run_id, runtime_path, runtime_provider, session_id, source, source_policy, summary_diagnostics, summary_usage, terminal_committer, tool_records, tool_schemas, tool_timeout, uploaded_descriptors, usage_total, user, window, window_path) = map(values.__getitem__, ('prompt_bundle', 'system_message', 'compression_memory', 'context_selection', 'context_stats', 'messages', 'summary_cache', 'all_text', 'all_reasoning', 'agent_runner', 'agent_timeout', 'archive_window', 'base', 'cancel_event', 'commit_cancelled_round', 'commit_failed_round', 'commit_terminal_round', 'compression_usage', 'config', 'consumed_guidance', 'consumed_guidance_details', 'context_policy', 'current_user_message', 'durable_provider_responses', 'failure_limit', 'failures', 'identical_call_limit', 'identical_calls', 'invalid_tool_arguments_retry_limit', 'max_provider_iterations', 'max_tool_calls', 'observed_reasoning', 'observed_text', 'pending_tool_calls', 'projected_current_rounds', 'provider', 'provider_responses', 'queue_compression_memory', 'recovery_map', 'registry', 'request', 'round_state', 'run_id', 'runtime_path', 'runtime_provider', 'session_id', 'source', 'source_policy', 'summary_diagnostics', 'summary_usage', 'terminal_committer', 'tool_records', 'tool_schemas', 'tool_timeout', 'uploaded_descriptors', 'usage_total', 'user', 'window', 'window_path'))
    initialized = initialize_provider_exchange({**state.dependencies, **values})
    reasoning_selection = initialized["reasoning_selection"]
    reasoning_extra = initialized["reasoning_extra"]
    declared_provider_input_limit = initialized[
        "declared_provider_input_limit"
    ]
    effective_tool_context_limit = min(
        context_policy.token_limit,
        declared_provider_input_limit
        if declared_provider_input_limit is not None
        else context_policy.token_limit,
    )
    context_stats = {
        **context_stats,
        "configured_token_limit": context_policy.token_limit,
        "provider_declared_input_limit": declared_provider_input_limit,
        "effective_tool_context_limit": effective_tool_context_limit,
    }
    round_state.context_stats = context_stats
    media_inputs = initialize_media_inputs(values, effective_tool_context_limit)
    values["media_inputs"] = media_inputs
    stream = initialized["stream"]
    guidance_channel = initialized["guidance_channel"]
    retry_state = initialized["retry_state"]
    pending_guidance_ack = initialized["pending_guidance_ack"]
    remote_guidance_assets = initialized["remote_guidance_assets"]
    protocol_parent_request_id = initialized["protocol_parent_request_id"]
    seen_calls = initialized["seen_calls"]
    blocked_recovery = initialized["blocked_recovery"]
    if initialized["usage_event"] is not None:
        yield initialized["usage_event"]
    round_state.recovered_tool_records = _retry_recovery_tool_records(
        recovery_map
    )
    final_metadata: dict[str, Any] = {}
    completed = False
    context_retry_count = 0
    tool_argument_retry_count = 0
    task_plan_boundary: TaskPlanCreationBoundary | None = None
    checkpoint = context_stats.get("usage_checkpoint")
    last_provider_input_tokens: int | None = checkpoint["input_tokens"] if checkpoint else None
    last_sent_local_tokens: int | None = checkpoint["local_tokens"] if checkpoint else None
    guidance_messages_for_retry: list[dict[str, Any]] = []
    task_coordinator = values.get("task_coordinator")
    from run.tools import ShellInbox
    shell_inbox = ShellInbox(base, user, source, session_id,
                             root_task_id=task_coordinator.id if task_coordinator else run_id)
    values["shell_inbox"] = shell_inbox

    def prepare_pending_guidance(values: list[Any]) -> list[dict[str, Any]]:
        """Prepare new text/media guidance and register its run assets."""

        prepared = prepare_guidance(
            values,
            root=base,
            user=user,
            session_id=session_id,
            config=config,
            runtime_provider=runtime_provider,
            provider=provider,
            cancel_event=cancel_event,
            known_descriptors=uploaded_descriptors,
            remote_assets=remote_guidance_assets,
        )
        known_ids = {
            str(item.get("asset_id") or "")
            for item in uploaded_descriptors
            if isinstance(item, dict)
        }
        for descriptor in prepared.uploaded_descriptors:
            asset_id = str(descriptor.get("asset_id") or "")
            if asset_id and asset_id not in known_ids:
                uploaded_descriptors.append(descriptor)
                known_ids.add(asset_id)
        guidance_messages_for_retry.extend(copy.deepcopy(prepared.messages))
        media_inputs.visible_asset_ids.update(prepared.direct_asset_ids)
        pending_guidance_ack.extend(prepared.inputs)
        _remember_retry_guidance(retry_state, prepared.inputs)
        return prepared.messages

    replayed_guidance = request.get("_retry_guidance")
    if isinstance(replayed_guidance, list) and replayed_guidance:
        messages.extend(prepare_pending_guidance(replayed_guidance))

    def refresh_dynamic_system_message() -> None:
        nonlocal prompt_bundle, system_message
        prompt_bundle = refresh_dynamic_prompt_bundle(
            base,
            user,
            config,
            prompt_bundle,
        )
        system_message = (
            {"role": "system", "content": prompt_bundle.text}
            if prompt_bundle.text
            else None
        )
        _replace_primary_system_message(messages, system_message)
        terminal_committer.context = replace(
            terminal_committer.context,
            system_message=system_message,
            prompt_bundle=prompt_bundle,
        )

    def current_round_tail() -> list[dict[str, Any]]:
        """Return messages produced after the current user message."""

        if current_user_message is None:
            return []
        for index, item in enumerate(messages):
            if item is current_user_message:
                return messages[index + 1 :]
        for index in range(len(messages) - 1, -1, -1):
            if messages[index] == current_user_message:
                return messages[index + 1 :]
        return []

    def update_terminal_context() -> None:
        terminal_committer.context = replace(
            terminal_committer.context,
            context_selection=context_selection,
            summary_cache=summary_cache,
            system_message=system_message,
            prompt_bundle=prompt_bundle,
        )
        round_state.context_stats = context_stats

    def prepare_context_retry(
        *, iteration: int, preserve_live_tail: bool, trigger: str,
    ) -> tuple[bool, list[_AnnotationRunEvent]]:
        nonlocal context_retry_count, context_selection, context_stats
        nonlocal compression_memory, summary_cache, summary_diagnostics
        if context_retry_count >= 2:
            return False, []
        from run.conversation.context_recovery import recover_provider_context

        recovery = recover_provider_context(
            {**dependencies, **values, **locals_for_recovery()},
            retry_count=context_retry_count + 1,
            live_tail=current_round_tail(),
            preserve_live_tail=preserve_live_tail, trigger=trigger,
        )
        if recovery.memory is not None:
            compression_memory = recovery.memory
        if recovery.result is None:
            for event in recovery.events or []:
                if event.metadata.get("status") == "failed":
                    context_stats["compression_diagnostics"] = event.metadata.get("diagnostics")
                    round_state.context_stats = context_stats
            return False, recovery.events or []
        context_retry_count += 1
        context_selection = recovery.result.selection
        summary_cache = recovery.result.summary_cache
        summary_diagnostics = recovery.result.diagnostics
        # Preserve list identity: the tool executor shares this same list.
        messages[:] = context_selection.messages
        context_stats = {
            **context_selection.stats(), "summary": summary_diagnostics,
            "summary_usage": summary_usage,
            "api_context_retries": context_retry_count,
            "configured_token_limit": context_policy.token_limit,
            "provider_declared_input_limit": declared_provider_input_limit,
            "effective_tool_context_limit": effective_tool_context_limit,
            "usage_checkpoint": None,
        }
        update_terminal_context()
        return True, recovery.events or []

    def locals_for_recovery():
        return dict(
            context_selection=context_selection, summary_cache=summary_cache,
            compression_memory=compression_memory, system_message=system_message,
            active_tool_schemas=active_tool_schemas,
            effective_tool_context_limit=effective_tool_context_limit,
            declared_provider_input_limit=declared_provider_input_limit,
        )

    for iteration in range(1, max_provider_iterations + 1):
        if cancel_event is not None and cancel_event.is_set():
            yield commit_cancelled_round()
            return
        if not (yield from prepare_shell_request(
            shell_inbox, last_iteration=iteration == max_provider_iterations, messages=messages,
            cancel_event=cancel_event, commit_cancelled_round=commit_cancelled_round,
        )):
            return
        if task_coordinator is not None:
            notification = task_coordinator.notifications()
            if notification:
                messages.append(notification)
            task_coordinator.phase("model_request")
        refresh_dynamic_system_message()
        media_inputs.refresh_messages(messages)
        if media_inputs.audit:
            yield RunEvent(type="media_input_update", metadata={"media_inputs": copy.deepcopy(media_inputs.audit)})
        media_inputs.validate_request(messages)
        # Guard the first request too, including restored usage calibration.
        active_tool_schemas = (
            registry.schemas(exclude=failures.unavailable) or None
        )
        current_local_tokens = estimate_messages_tokens(
            messages
        ) + estimate_tools_tokens(
            active_tool_schemas
        )
        incremental_tokens, projected_tokens, measurement = project_context_input(
            current_local_tokens, last_provider_input_tokens, last_sent_local_tokens,
        )
        if projected_tokens >= effective_tool_context_limit:
            recovered_context, retry_events = prepare_context_retry(
                iteration=iteration,
                preserve_live_tail=True,
                trigger="tool_context_limit",
            )
            for retry_event in retry_events:
                yield retry_event
            if recovered_context:
                active_tool_schemas = (
                    registry.schemas(exclude=failures.unavailable) or None
                )
                current_local_tokens = estimate_messages_tokens(
                    messages
                ) + estimate_tools_tokens(active_tool_schemas)
                incremental_tokens = None
                projected_tokens = current_local_tokens
                measurement = "local_estimate_after_compression"
                last_provider_input_tokens = None
                last_sent_local_tokens = None
            if projected_tokens > effective_tool_context_limit:
                terminal_event = commit_terminal_round(
                    status="limited",
                    reason="tool_context_limit",
                    marker=(
                        "[本轮工具循环已达到上下文保护上限；"
                        "为避免拆散工具消息组，本轮已停止]"
                    ),
                    pending_message=(
                        "工具调用因本轮达到上下文保护上限而未执行"
                    ),
                    pending_exception_type="ToolContextLimitExceeded",
                )
                terminal_event.metadata["context_guard"] = tool_context_guard(
                    measurement=measurement, provider_input_tokens=last_provider_input_tokens,
                    previous_local_tokens=last_sent_local_tokens, current_local_tokens=current_local_tokens,
                    incremental_tokens=incremental_tokens, projected_tokens=projected_tokens,
                    token_limit=effective_tool_context_limit, configured_token_limit=context_policy.token_limit,
                    provider_declared_input_limit=declared_provider_input_limit, iteration=iteration,
                    latest_tools=_tool_context_diagnostics(tool_records, iteration=iteration - 1),
                )
                yield terminal_event
                return
        request_max_tokens = request_output_limit(runtime_provider, context_policy)
        provider_attempt = 0
        invalid_tool_arguments_retries = 0
        repair_tool_name = ""
        retry_preview_text = ""
        retry_preview_reasoning = ""
        iteration_text: list[str] = []
        iteration_reasoning: list[str] = []
        iteration_observed_committed = False
        calls: list[_AnnotationToolCall] = []
        iteration_done: _AnnotationRunEvent | None = None
        iteration_usage: Usage | None = None
        tool_schema_map = _tool_schema_map(registry.schemas())

        def flush_iteration_observed() -> None:
            nonlocal iteration_observed_committed
            if iteration_observed_committed:
                return
            observed_text.extend(iteration_text)
            observed_reasoning.extend(iteration_reasoning)
            iteration_observed_committed = True
        while True:
            if provider_attempt > 0:
                refresh_dynamic_system_message()
            provider_attempt += 1
            request_messages = (
                _messages_with_tool_argument_repair(
                    messages,
                    tool_name=repair_tool_name,
                    retry_number=invalid_tool_arguments_retries,
                )
                if invalid_tool_arguments_retries
                else messages
            )
            request_local_tokens = estimate_messages_tokens(
                request_messages
            ) + estimate_tools_tokens(active_tool_schemas)
            chat_request = ChatRequest(
                model=runtime_provider["model"],
                messages=request_messages,
                stream=stream,
                tools=active_tool_schemas,
                max_tokens=request_max_tokens,
                extra=dict(reasoning_extra),
            )
            protocol_request = chat_request_to_kemo(
                chat_request, transport=str(runtime_provider.get("type") or ""),
            ).model_copy(
                update={
                    "request_id": f"req_{uuid.uuid4().hex}",
                    "parent_request_id": protocol_parent_request_id,
                    "attempt": (
                        context_retry_count
                        + invalid_tool_arguments_retries
                        + 1
                    ),
                    "metadata": {
                        "capability": "conversation",
                        "user": user,
                        "source": source,
                        "session_id": session_id,
                        "run_id": run_id,
                        "iteration": iteration,
                        "tool_argument_retry": invalid_tool_arguments_retries,
                        "window": window_path.name,
                    },
                }
            )
            record_provider_request_diagnostic(round_state, protocol_request, prompt_bundle, iteration, provider_attempt)
            iteration_done = None
            iteration_usage = None
            iteration_text = []
            iteration_reasoning = []
            iteration_observed_committed = False
            retry_invalid_tool_arguments = False
            attempt_published_media = False
            attempt_calls: list[_AnnotationToolCall] = []
            attempt_tool_events: list[_AnnotationRunEvent] = []
            attempt_usage_events: list[_AnnotationRunEvent] = []
            attempt_request_recorded = False

            def record_attempt_usage() -> None:
                nonlocal iteration_usage, attempt_request_recorded
                if attempt_request_recorded:
                    return
                _record_provider_request(
                    usage_total,
                    iteration_usage
                    if iteration_usage is not None
                    else _usage_from_dict({}),
                )
                iteration_usage = None
                attempt_request_recorded = True

            try:
                with provider_request_slot(config, cancel_event=cancel_event):
                    for event in _provider_events(
                        provider,
                        protocol_request,
                        root=base,
                        user=user,
                        cancel_event=cancel_event,
                    ):
                        if cancel_event is not None and cancel_event.is_set():
                            flush_iteration_observed()
                            yield commit_cancelled_round()
                            return
                        if pending_guidance_ack:
                            applied_guidance = list(pending_guidance_ack)
                            pending_guidance_ack.clear()
                            consumed_guidance.extend(
                                item.display_text for item in applied_guidance
                            )
                            applied_details = [
                                item.history_detail() for item in applied_guidance
                            ]
                            consumed_guidance_details.extend(applied_details)
                            yield RunEvent(
                                type="guidance_applied",
                                metadata={
                                    "guidance": [
                                        item.display_text for item in applied_guidance
                                    ],
                                    "guidance_details": applied_details,
                                    "guidance_count": len(applied_guidance),
                                    "iteration": iteration,
                                },
                            )
                        if event.type == "text_delta":
                            iteration_text.append(event.content)
                            yield event
                        elif event.type == "reasoning_delta":
                            iteration_reasoning.append(event.content)
                            yield event
                        elif event.type == "tool_call_start":
                            call = ToolCall(
                                id=event.tool_call_id,
                                name=event.tool_name,
                                arguments=event.arguments or {},
                                arguments_raw=(
                                    (checked_412 if isinstance(checked_412 := event.metadata.get("raw_arguments"), str) else None)
                                ),
                                parse_error=(
                                    copy.deepcopy(event.metadata.get("parse_error"))
                                    if isinstance(
                                        event.metadata.get("parse_error"), dict
                                    )
                                    else None
                                ),
                            )
                            # Tool cards and pending-call state are committed only
                            # after the complete Provider attempt is known to be
                            # valid. This lets a later malformed parallel call
                            # discard the whole batch without duplicate cards or
                            # accidental execution.
                            attempt_calls.append(call)
                            attempt_tool_events.append(event)
                        elif event.type == "usage":
                            iteration_usage = _usage_from_dict(event.usage)
                            attempt_usage_events.append(
                                RunEvent(
                                    type="usage",
                                    usage=event.usage,
                                    metadata={"iteration": iteration},
                                )
                            )
                        elif event.type == "media_output":
                            attempt_published_media = True
                            yield event
                        elif event.type == "error":
                            _raise_if_context_length_exceeded(event.error)
                            if is_request_size_exceeded(event.error):
                                raise ProviderError("API 请求体超过传输大小上限", status_code=413)
                            can_retry_invalid_arguments = (
                                _is_invalid_tool_arguments_error(event.error)
                                and invalid_tool_arguments_retries
                                < invalid_tool_arguments_retry_limit
                                and not attempt_published_media
                            )
                            record_attempt_usage()
                            if can_retry_invalid_arguments:
                                if not retry_preview_text:
                                    retry_preview_text = "".join(iteration_text)
                                if not retry_preview_reasoning:
                                    retry_preview_reasoning = "".join(
                                        iteration_reasoning
                                    )
                                invalid_tool_arguments_retries += 1
                                tool_argument_retry_count += 1
                                round_state.tool_argument_retries = (
                                    tool_argument_retry_count
                                )
                                repair_tool_name = _invalid_tool_name(event.error)
                                retry_invalid_tool_arguments = True
                                break
                            failure = copy.deepcopy(event.error)
                            if _is_invalid_tool_arguments_error(failure):
                                failure["retry_count"] = (
                                    invalid_tool_arguments_retries
                                )
                                failure["retry_limit"] = (
                                    invalid_tool_arguments_retry_limit
                                )
                            provider_response = _event_provider_response(event)
                            if isinstance(provider_response, dict):
                                provider_responses.append(
                                    copy.deepcopy(provider_response)
                                )
                            flush_iteration_observed()
                            terminal_event = commit_failed_round(
                                failure,
                                reason="provider_error_event",
                            )
                            event.error = failure
                            yield _committed_failure_event(
                                event, terminal_event
                            )
                            return
                        elif event.type == "done":
                            iteration_done = event
                if retry_invalid_tool_arguments:
                    continue
                break
            except ProviderCongestionError as exc:
                if cancel_event is not None and cancel_event.is_set():
                    flush_iteration_observed()
                    yield commit_cancelled_round()
                    return
                terminal_event = commit_failed_round(
                    exc,
                    reason="provider_congestion",
                )
                yield _committed_failure_event(
                    error_event(exc, phase="provider"), terminal_event
                )
                return
            except BaseException as exc:
                if isinstance(exc, HOST_CONTROL_EXCEPTIONS):
                    raise
                if cancel_event is not None and cancel_event.is_set():
                    flush_iteration_observed()
                    yield commit_cancelled_round()
                    return
                context_length_error = _is_context_length_exceeded(exc)
                request_size_error = is_request_size_exceeded(exc)
                recoverable_size = context_length_error or request_size_error
                if recoverable_size:
                    # A Provider context rejection is still a real Provider
                    # request even when the gateway returned no usage object.
                    # Count it explicitly so request totals and cache
                    # diagnostics do not silently under-report retries.
                    record_attempt_usage()
                if not recoverable_size or context_retry_count >= 2:
                    flush_iteration_observed()
                    record_attempt_usage()
                    if recoverable_size:
                        mark_retry_exhausted(exc, attempts=context_retry_count + 1, max_attempts=3)
                    terminal_event = commit_failed_round(
                        exc,
                        reason=(
                            "provider_context_limit" if context_length_error
                            else "provider_request_size" if request_size_error else "provider_exception"
                        ),
                    )
                    yield _committed_failure_event(
                        error_event(exc, phase="provider"),
                        terminal_event,
                    )
                    return
                recovered_context, retry_events = prepare_context_retry(
                    iteration=iteration,
                    preserve_live_tail=iteration > 1,
                    trigger="api_context_length" if context_length_error else "api_request_size",
                )
                for retry_event in retry_events:
                    yield retry_event
                if not recovered_context:
                    if request_size_error:
                        mark_retry_exhausted(exc, attempts=context_retry_count + 1, max_attempts=3)
                        yield _committed_failure_event(
                            error_event(exc, phase="provider"),
                            commit_failed_round(exc, reason="provider_request_size"),
                        )
                        return
                    exhausted = ContextLengthExceededError(
                        "Provider 上下文超限，但没有可继续裁剪的历史轮次"
                    )
                    mark_retry_exhausted(exhausted, attempts=context_retry_count + 1, max_attempts=3)
                    raise exhausted from exc
                last_provider_input_tokens = None
                last_sent_local_tokens = None

        if iteration_done is None:
            exc = EngineError("Provider 事件流缺少 done 终态")
            flush_iteration_observed()
            record_attempt_usage()
            terminal_event = commit_failed_round(
                exc,
                reason="provider_missing_terminal",
            )
            yield _committed_failure_event(
                error_event(exc, phase="provider"), terminal_event
            )
            return
        invalid_batch = _validate_tool_call_batch(
            attempt_calls,
            tool_schema_map,
        )
        if invalid_batch is not None:
            record_attempt_usage()
            provider_response = _event_provider_response(iteration_done)
            if isinstance(provider_response, dict):
                provider_responses.append(copy.deepcopy(provider_response))
            durable_provider_response = _event_provider_response(
                iteration_done,
                durable=True,
            )
            if isinstance(durable_provider_response, dict):
                durable_provider_responses.append(
                    {
                        **copy.deepcopy(durable_provider_response),
                        "_iteration": iteration,
                    }
                )
            can_retry_invalid_arguments = (
                invalid_tool_arguments_retries
                < invalid_tool_arguments_retry_limit
                and not attempt_published_media
            )
            if can_retry_invalid_arguments:
                if not retry_preview_text:
                    retry_preview_text = "".join(iteration_text)
                if not retry_preview_reasoning:
                    retry_preview_reasoning = "".join(iteration_reasoning)
                invalid_tool_arguments_retries += 1
                tool_argument_retry_count += 1
                round_state.tool_argument_retries = tool_argument_retry_count
                repair_tool_name = _invalid_tool_name(invalid_batch)
                continue
            failure = copy.deepcopy(invalid_batch)
            failure["retry_count"] = invalid_tool_arguments_retries
            failure["retry_limit"] = invalid_tool_arguments_retry_limit
            if retry_preview_text:
                if not "".join(iteration_text).startswith(retry_preview_text):
                    iteration_text.insert(0, retry_preview_text)
                retry_preview_text = ""
            if retry_preview_reasoning:
                if not "".join(iteration_reasoning).startswith(
                    retry_preview_reasoning
                ):
                    iteration_reasoning.insert(0, retry_preview_reasoning)
                retry_preview_reasoning = ""
            flush_iteration_observed()
            terminal_event = commit_failed_round(
                failure,
                reason="invalid_tool_arguments",
            )
            yield _committed_failure_event(
                RunEvent(
                    type="error",
                    error=failure,
                    metadata={"provider_response": provider_response}
                    if isinstance(provider_response, dict)
                    else {},
                ),
                terminal_event,
            )
            return
        round_state.retry_progress_observed = True
        media_inputs.acknowledge()
        if media_inputs.audit:
            yield RunEvent(type="media_input_update", metadata={"media_inputs": copy.deepcopy(media_inputs.audit)})
        if iteration_usage is None:
            iteration_usage = _usage_from_dict(iteration_done.usage)
        if retry_preview_text:
            accepted_text = "".join(iteration_text)
            if not accepted_text.startswith(retry_preview_text):
                iteration_text.insert(0, retry_preview_text)
            retry_preview_text = ""
        if retry_preview_reasoning:
            accepted_reasoning = "".join(iteration_reasoning)
            if not accepted_reasoning.startswith(retry_preview_reasoning):
                iteration_reasoning.insert(0, retry_preview_reasoning)
            retry_preview_reasoning = ""
        for call, tool_event in zip(
            attempt_calls,
            attempt_tool_events,
            strict=True,
        ):
            if cancel_event is not None and cancel_event.is_set():
                flush_iteration_observed()
                yield commit_cancelled_round()
                return
            calls.append(call)
            pending_tool_calls[call.id] = {
                "name": call.name,
                "arguments": copy.deepcopy(call.arguments),
                "iteration": iteration,
            }
            yield tool_event
        for usage_event in attempt_usage_events:
            if cancel_event is not None and cancel_event.is_set():
                flush_iteration_observed()
                yield commit_cancelled_round()
                return
            yield usage_event
        last_provider_input_tokens, last_sent_local_tokens = record_context_checkpoint(
            context_stats, iteration_usage, request_local_tokens, runtime_provider, summary_cache,
            previous=(last_provider_input_tokens, last_sent_local_tokens),
        )
        all_text.extend(iteration_text)
        all_reasoning.extend(iteration_reasoning)
        flush_iteration_observed()
        record_attempt_usage()
        if task_coordinator is not None:
            task_coordinator.acknowledge()
        shell_inbox.acknowledge()
        final_metadata = dict(iteration_done.metadata)
        provider_response = _event_provider_response(iteration_done)
        if isinstance(provider_response, dict):
            provider_responses.append(copy.deepcopy(provider_response))
        durable_provider_response = _event_provider_response(
            iteration_done,
            durable=True,
        )
        if isinstance(durable_provider_response, dict):
            durable_provider_responses.append(
                {
                    **copy.deepcopy(durable_provider_response),
                    "_iteration": iteration,
                }
            )
        protocol_parent_request_id = protocol_request.request_id

        if not calls:
            if task_coordinator is not None:
                task_action = yield from settle_children(
                    task_coordinator, messages=messages, iteration_text=iteration_text,
                    all_text=all_text, observed_text=observed_text, cancel_event=cancel_event,
                    guidance_channel=guidance_channel, drain_guidance=_drain_guidance,
                    prepare_guidance=prepare_pending_guidance, commit_cancelled_round=commit_cancelled_round,
                )
                if task_action == "cancelled":
                    return
                if task_action == "continue":
                    continue
            from run.conversation.provider_tasks import settle_shell
            shell_action = yield from settle_shell(
                shell_inbox, messages=messages, iteration_text=iteration_text,
                cancel_event=cancel_event, commit_cancelled_round=commit_cancelled_round,
            )
            if shell_action == "cancelled":
                return
            if shell_action == "continue":
                continue
            pending_guidance = (
                _drain_or_close_guidance(guidance_channel)
                if iteration < max_provider_iterations
                else []
            )
            if pending_guidance and iteration < max_provider_iterations:
                messages.append(
                    {"role": "assistant", "content": "".join(iteration_text)}
                )
                messages.extend(prepare_pending_guidance(pending_guidance))
                all_text.append("\n\n")
                observed_text.append("\n\n")
                yield RunEvent(type="text_delta", content="\n\n")
                continue
            _close_guidance(guidance_channel)
            completed = True
            break
        batch_result = yield from execute_tool_batch(
            ToolBatchContext(
                shared={**state.dependencies, **values},
                runtime={
                    "calls": calls,
                    "iteration": iteration,
                    "iteration_text": iteration_text,
                    "iteration_reasoning": iteration_reasoning,
                    "provider_response": provider_response,
                    "seen_calls": seen_calls,
                    "blocked_recovery": blocked_recovery,
                    "flush_iteration_observed": flush_iteration_observed,
                    "guidance_channel": guidance_channel,
                    "task_plan_boundary": task_plan_boundary,
                },
            )
        )
        task_plan_boundary = batch_result.task_plan_boundary
        retryable_tool_failure = batch_result.retryable_tool_failure
        completed = batch_result.completed
        if batch_result.stop:
            return
        if task_plan_boundary is not None:
            break
        pending_guidance = _drain_guidance(guidance_channel)
        messages.extend(prepare_pending_guidance(pending_guidance))
        if retryable_tool_failure is not None:
            raise_retryable_tool_failure(retryable_tool_failure, ProviderError)

    if not completed:
        yield commit_terminal_round(
            status="limited",
            reason="tool_loop_incomplete",
            marker="[本轮工具循环未能正常收束，本轮已停止]",
            pending_message="工具调用因本轮工具循环未能正常收束而未执行",
            pending_exception_type="ToolLoopIncomplete",
        )
        return
    if cancel_event is not None and cancel_event.is_set():
        yield commit_cancelled_round()
        return

    if task_coordinator is not None:
        task_coordinator.finish("completed")

    finalize_provider_loop(state, {**globals(), **locals()})


