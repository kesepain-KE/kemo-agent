"""Provider streaming, recovery, and tool exchange implementation."""
from __future__ import annotations
from typing import Any
from run.conversation.provider_state import finalize_provider_loop, initialize_provider_exchange
from run.conversation.tool_batch import ToolBatchContext, execute_tool_batch
def run_provider_exchange(state):
    values = state.values
    dependencies = state.dependencies
    (_remember_retry_guidance, _replace_primary_system_message, prepare_guidance, refresh_dynamic_prompt_bundle) = map(dependencies.__getitem__, ("_remember_retry_guidance", "_replace_primary_system_message", "prepare_guidance", "refresh_dynamic_prompt_bundle"))
    (ChatRequest, ContextLengthExceededError, EngineError, ProviderCongestionError, ProviderError, RunEvent, SUMMARY_CHUNK_TOKEN_BUDGET, SUMMARY_MAX_OUTPUT_TOKENS, ToolCall, _close_guidance, _committed_failure_event, _drain_guidance, _drain_or_close_guidance, _ensure_fixed_content_fits, _event_provider_response, _extract_memory_backlog, _invalid_tool_name, _is_context_length_exceeded, _is_invalid_tool_arguments_error, _messages_with_tool_argument_repair, _provider_events, _raise_if_context_length_exceeded, _record_provider_request, _retry_recovery_messages, _retry_recovery_tool_records, _tool_context_diagnostics, _tool_schema_map, _usage_from_dict, _validate_tool_call_batch, build_summary_message, chat_request_to_kemo, copy, error_event, estimate_messages_tokens, estimate_tools_tokens, get_or_create_summary, provider_request_slot, replace, select_context, uuid) = map(dependencies.__getitem__, ('ChatRequest', 'ContextLengthExceededError', 'EngineError', 'ProviderCongestionError', 'ProviderError', 'RunEvent', 'SUMMARY_CHUNK_TOKEN_BUDGET', 'SUMMARY_MAX_OUTPUT_TOKENS', 'ToolCall', '_close_guidance', '_committed_failure_event', '_drain_guidance', '_drain_or_close_guidance', '_ensure_fixed_content_fits', '_event_provider_response', '_extract_memory_backlog', '_invalid_tool_name', '_is_context_length_exceeded', '_is_invalid_tool_arguments_error', '_messages_with_tool_argument_repair', '_provider_events', '_raise_if_context_length_exceeded', '_record_provider_request', '_retry_recovery_messages', '_retry_recovery_tool_records', '_tool_context_diagnostics', '_tool_schema_map', '_usage_from_dict', '_validate_tool_call_batch', 'build_summary_message', 'chat_request_to_kemo', 'copy', 'error_event', 'estimate_messages_tokens', 'estimate_tools_tokens', 'get_or_create_summary', 'provider_request_slot', 'replace', 'select_context', 'uuid'))
    (prompt_bundle, system_message, compression_memory, context_selection, context_stats, messages, summary_cache, all_text, all_reasoning, agent_runner, agent_timeout, archive_window, base, cancel_event, commit_cancelled_round, commit_failed_round, commit_terminal_round, compression_usage, config, consumed_guidance, consumed_guidance_details, context_policy, current_user_message, defer_failure_commit, durable_provider_responses, failure_limit, failures, identical_call_limit, identical_calls, invalid_tool_arguments_retry_limit, max_provider_iterations, max_tool_calls, observed_reasoning, observed_text, pending_tool_calls, projected_current_rounds, provider, provider_responses, queue_compression_memory, recovery_map, registry, request, round_state, run_id, runtime_path, runtime_provider, session_id, source, source_policy, summary_diagnostics, summary_usage, terminal_committer, tool_records, tool_schemas, tool_timeout, uploaded_descriptors, usage_total, user, window, window_path) = map(values.__getitem__, ('prompt_bundle', 'system_message', 'compression_memory', 'context_selection', 'context_stats', 'messages', 'summary_cache', 'all_text', 'all_reasoning', 'agent_runner', 'agent_timeout', 'archive_window', 'base', 'cancel_event', 'commit_cancelled_round', 'commit_failed_round', 'commit_terminal_round', 'compression_usage', 'config', 'consumed_guidance', 'consumed_guidance_details', 'context_policy', 'current_user_message', 'defer_failure_commit', 'durable_provider_responses', 'failure_limit', 'failures', 'identical_call_limit', 'identical_calls', 'invalid_tool_arguments_retry_limit', 'max_provider_iterations', 'max_tool_calls', 'observed_reasoning', 'observed_text', 'pending_tool_calls', 'projected_current_rounds', 'provider', 'provider_responses', 'queue_compression_memory', 'recovery_map', 'registry', 'request', 'round_state', 'run_id', 'runtime_path', 'runtime_provider', 'session_id', 'source', 'source_policy', 'summary_diagnostics', 'summary_usage', 'terminal_committer', 'tool_records', 'tool_schemas', 'tool_timeout', 'uploaded_descriptors', 'usage_total', 'user', 'window', 'window_path'))
    initialized = initialize_provider_exchange({**state.dependencies, **values})
    reasoning_selection = initialized["reasoning_selection"]
    reasoning_extra = initialized["reasoning_extra"]
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
    last_provider_input_tokens: int | None = None
    last_sent_local_tokens: int | None = None

    guidance_messages_for_retry: list[dict[str, Any]] = []

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

    for iteration in range(1, max_provider_iterations + 1):
        if cancel_event is not None and cancel_event.is_set():
            yield commit_cancelled_round()
            return
        refresh_dynamic_system_message()
        if iteration > 1:
            active_tool_schemas = (
                registry.schemas(exclude=failures.unavailable) or None
            )
            current_local_tokens = estimate_messages_tokens(
                messages
            ) + estimate_tools_tokens(
                active_tool_schemas
            )
            if (
                last_provider_input_tokens is not None
                and last_sent_local_tokens is not None
            ):
                incremental_tokens = (
                    current_local_tokens - last_sent_local_tokens
                )
                projected_tokens = max(
                    0,
                    last_provider_input_tokens + incremental_tokens,
                )
                measurement = "provider_plus_increment"
            else:
                incremental_tokens = None
                projected_tokens = current_local_tokens
                measurement = "local_estimate"
            if projected_tokens > context_policy.token_limit:
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
                terminal_event.metadata["context_guard"] = {
                    "measurement": measurement,
                    "provider_input_tokens": last_provider_input_tokens,
                    "previous_local_tokens": last_sent_local_tokens,
                    "current_local_tokens": current_local_tokens,
                    "incremental_tokens": incremental_tokens,
                    "projected_tokens": projected_tokens,
                    "token_limit": context_policy.token_limit,
                    "iteration": iteration,
                    "latest_tools": _tool_context_diagnostics(
                        tool_records,
                        iteration=iteration - 1,
                    ),
                }
                yield terminal_event
                return
        else:
            active_tool_schemas = tool_schemas
        configured_max_tokens = runtime_provider.get("max_tokens")
        request_max_tokens = (
            min(
                context_policy.output_reserve,
                max(1, int(configured_max_tokens)),
            )
            if configured_max_tokens is not None
            else None
        )
        provider_attempt = 0
        invalid_tool_arguments_retries = 0
        repair_tool_name = ""
        retry_preview_text = ""
        retry_preview_reasoning = ""
        iteration_text: list[str] = []
        iteration_reasoning: list[str] = []
        iteration_observed_committed = False
        calls: list[ToolCall] = []
        iteration_done: RunEvent | None = None
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
            protocol_request = chat_request_to_kemo(chat_request).model_copy(
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
                        "prompt_hash": prompt_bundle.diagnostics.get("hash"),
                    },
                }
            )
            iteration_done = None
            iteration_usage = None
            iteration_text = []
            iteration_reasoning = []
            iteration_observed_committed = False
            retry_invalid_tool_arguments = False
            attempt_published_media = False
            attempt_calls: list[ToolCall] = []
            attempt_tool_events: list[RunEvent] = []
            attempt_usage_events: list[RunEvent] = []
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
                                    event.metadata.get("raw_arguments")
                                    if isinstance(
                                        event.metadata.get("raw_arguments"), str
                                    )
                                    else None
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
                            can_retry_invalid_arguments = (
                                _is_invalid_tool_arguments_error(event.error)
                                and invalid_tool_arguments_retries
                                < invalid_tool_arguments_retry_limit
                                and not attempt_published_media
                            )
                            if iteration_usage is not None:
                                _record_provider_request(
                                    usage_total, iteration_usage
                                )
                                iteration_usage = None
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
                if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                    raise
                if cancel_event is not None and cancel_event.is_set():
                    flush_iteration_observed()
                    yield commit_cancelled_round()
                    return
                context_length_error = _is_context_length_exceeded(exc)
                if (
                    not context_length_error
                    or iteration != 1
                    or context_retry_count >= 2
                ):
                    flush_iteration_observed()
                    if iteration_usage is not None:
                        _record_provider_request(
                            usage_total, iteration_usage
                        )
                        iteration_usage = None
                    terminal_event = commit_failed_round(
                        exc,
                        reason=(
                            "provider_context_limit"
                            if context_length_error
                            else "provider_exception"
                        ),
                    )
                    yield _committed_failure_event(
                        error_event(exc, phase="provider"),
                        terminal_event,
                    )
                    return
                context_retry_count += 1
                divisor = 2**context_retry_count
                retry_policy = replace(
                    context_policy,
                    rounds_after_compression=max(
                        context_policy.recent_full_rounds,
                        context_policy.rounds_after_compression // divisor,
                    ),
                )
                retry_selection = select_context(
                    window=window,
                    policy=retry_policy,
                    system_message=system_message,
                    summary_message=build_summary_message(summary_cache),
                    current_user_message=current_user_message,
                    tools=active_tool_schemas,
                    force_compress=True,
                )
                if not retry_selection.removed_rounds:
                    raise ContextLengthExceededError(
                        "Provider 上下文超限，但没有可继续裁剪的历史轮次"
                    ) from exc
                yield RunEvent(
                    type="context_compression",
                    content="Provider 上下文超限，正在进一步压缩对话",
                    metadata={
                        "status": "started",
                        "trigger": "api_context_length",
                        "run_id": run_id,
                        "rounds_before": len(retry_selection.all_rounds)
                        + projected_current_rounds,
                        "rounds_removed": len(retry_selection.removed_rounds),
                        "rounds_remaining": len(retry_selection.kept_rounds)
                        + projected_current_rounds,
                        "memory_mode": (
                            "background"
                            if queue_compression_memory
                            else "synchronous"
                        ),
                    },
                )
                if compression_memory is None and not queue_compression_memory:
                    compression_memory = _extract_memory_backlog(
                        root=base,
                        user=user,
                        source=source,
                        session_id=session_id,
                        directory=window_path,
                        window=archive_window,
                        config=config,
                        agent_runner=agent_runner,
                        cancel_event=cancel_event,
                    )
                    raw_memory_usage = compression_memory.get("usage")
                    if isinstance(raw_memory_usage, dict) and raw_memory_usage.get(
                        "provider_request_count", 0
                    ):
                        _record_provider_request(
                            compression_usage,
                            _usage_from_dict(raw_memory_usage),
                        )
                        _record_provider_request(
                            usage_total,
                            _usage_from_dict(raw_memory_usage),
                        )
                retry_events: list[RunEvent] = []
                summary_cache, retry_diagnostics = get_or_create_summary(
                    runtime_path=runtime_path,
                    groups=retry_selection.removed_rounds,
                    agent_runner=agent_runner,
                    agent_name="context_manage",
                    trigger="api_context_length",
                    cancel_event=cancel_event,
                    chunk_token_budget=min(
                        SUMMARY_CHUNK_TOKEN_BUDGET,
                        max(256, retry_policy.input_budget // 2),
                    ),
                    max_tokens=min(
                        SUMMARY_MAX_OUTPUT_TOKENS,
                        max(256, retry_policy.output_reserve),
                    ),
                    response_hook=lambda raw: (
                        _record_provider_request(
                            summary_usage, _usage_from_dict(raw)
                        ),
                        _record_provider_request(
                            usage_total, _usage_from_dict(raw)
                        ),
                    ),
                    event_callback=retry_events.append,
                    source=source,
                    session_id=session_id,
                    skip_memory_extraction=True,
                    previous_cache=summary_cache,
                    round_offset=max(
                        0,
                        int(
                            (window.get("data", {}).get("context") or {}).get(
                                "round_offset", 0
                            )
                        ),
                    ),
                    persist=False,
                )
                if summary_cache is None:
                    raise ContextLengthExceededError(
                        "Provider 上下文超限，且 context_manage 摘要生成失败"
                    ) from exc
                context_selection = select_context(
                    window=window,
                    policy=retry_policy,
                    system_message=system_message,
                    summary_message=build_summary_message(summary_cache),
                    current_user_message=current_user_message,
                    tools=active_tool_schemas,
                    force_compress=True,
                )
                _ensure_fixed_content_fits(
                    context_selection, system_message=system_message
                )
                messages = context_selection.messages
                if recovery_map:
                    messages.extend(_retry_recovery_messages(recovery_map))
                if guidance_messages_for_retry:
                    messages.extend(copy.deepcopy(guidance_messages_for_retry))
                context_stats = context_selection.stats()
                context_stats["summary"] = retry_diagnostics
                context_stats["summary_usage"] = summary_usage
                context_stats["api_context_retries"] = context_retry_count
                yield RunEvent(
                    type="context_compression",
                    content="对话上下文摘要已就绪，正在重试请求",
                    metadata={
                        "status": "ready",
                        "trigger": "api_context_length",
                        "run_id": run_id,
                        "rounds_before": len(context_selection.all_rounds)
                        + projected_current_rounds,
                        "rounds_removed": len(context_selection.removed_rounds),
                        "rounds_remaining": len(context_selection.kept_rounds)
                        + projected_current_rounds,
                        "memory_mode": (
                            "background"
                            if queue_compression_memory
                            else "synchronous"
                        ),
                        "memory_status": (
                            "queued_after_commit"
                            if queue_compression_memory
                            else str((compression_memory or {}).get("status") or "")
                        ),
                    },
                )
                for retry_event in retry_events:
                    yield retry_event

        if iteration_done is None:
            exc = EngineError("Provider 事件流缺少 done 终态")
            flush_iteration_observed()
            if iteration_usage is not None:
                _record_provider_request(usage_total, iteration_usage)
                iteration_usage = None
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
            if iteration_usage is not None:
                _record_provider_request(usage_total, iteration_usage)
                iteration_usage = None
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
        if (
            not iteration_usage.estimated
            and iteration_usage.prompt_tokens > 0
        ):
            last_provider_input_tokens = iteration_usage.prompt_tokens
            last_sent_local_tokens = request_local_tokens
        all_text.extend(iteration_text)
        all_reasoning.extend(iteration_reasoning)
        flush_iteration_observed()
        _record_provider_request(usage_total, iteration_usage)
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
            failure_error = retryable_tool_failure.get("error")
            if not isinstance(failure_error, dict):
                failure_error = {}
            retry_after_ms: int | None = None
            try:
                raw_retry_after = failure_error.get("retry_after_ms")
                if raw_retry_after is not None:
                    retry_after_ms = max(0, int(raw_retry_after))
            except (TypeError, ValueError):
                retry_after_ms = None
            raise ProviderError(
                "工具调用失败，正在准备自动重试",
                category="tool_error",
                status_code=(
                    int(failure_error["status_code"])
                    if str(failure_error.get("status_code") or "").isdigit()
                    else None
                ),
                retryable=True,
                retry_after_ms=retry_after_ms,
            )

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
        flush_iteration_observed()
        yield commit_cancelled_round()
        return


    finalize_provider_loop(state, {**globals(), **locals()})


