"""Provider/tool exchange stage for one conversation round."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderLoopState:
    """Mutable namespace shared with the conversation orchestration entry point."""

    values: dict[str, Any]
    stop_main: bool = True


def run_provider_loop(state: ProviderLoopState):
    prompt_bundle = state.values["prompt_bundle"]
    system_message = state.values["system_message"]
    Any = state.values["Any"]
    ChatRequest = state.values["ChatRequest"]
    ContextLengthExceededError = state.values["ContextLengthExceededError"]
    EngineError = state.values["EngineError"]
    GuidanceInput = state.values["GuidanceInput"]
    ProviderCongestionError = state.values["ProviderCongestionError"]
    ProviderError = state.values["ProviderError"]
    RunEvent = state.values["RunEvent"]
    SUMMARY_CHUNK_TOKEN_BUDGET = state.values["SUMMARY_CHUNK_TOKEN_BUDGET"]
    SUMMARY_MAX_OUTPUT_TOKENS = state.values["SUMMARY_MAX_OUTPUT_TOKENS"]
    TaskPlanCreationBoundary = state.values["TaskPlanCreationBoundary"]
    ToolCall = state.values["ToolCall"]
    ToolCancelledError = state.values["ToolCancelledError"]
    ToolResultTooLargeError = state.values["ToolResultTooLargeError"]
    Usage = state.values["Usage"]
    _assistant_tool_message = state.values["_assistant_tool_message"]
    _close_guidance = state.values["_close_guidance"]
    _committed_failure_event = state.values["_committed_failure_event"]
    _drain_guidance = state.values["_drain_guidance"]
    _drain_or_close_guidance = state.values["_drain_or_close_guidance"]
    _ensure_fixed_content_fits = state.values["_ensure_fixed_content_fits"]
    _event_provider_response = state.values["_event_provider_response"]
    _extract_memory_backlog = state.values["_extract_memory_backlog"]
    _invalid_tool_name = state.values["_invalid_tool_name"]
    _is_context_length_exceeded = state.values["_is_context_length_exceeded"]
    _is_invalid_tool_arguments_error = state.values["_is_invalid_tool_arguments_error"]
    _json_result = state.values["_json_result"]
    _merge_usage = state.values["_merge_usage"]
    _messages_with_tool_argument_repair = state.values["_messages_with_tool_argument_repair"]
    _provider_events = state.values["_provider_events"]
    _raise_if_context_length_exceeded = state.values["_raise_if_context_length_exceeded"]
    _record_provider_request = state.values["_record_provider_request"]
    _remember_retry_guidance = state.values["_remember_retry_guidance"]
    _replace_primary_system_message = state.values["_replace_primary_system_message"]
    _response_reasoning_item = state.values["_response_reasoning_item"]
    _retry_recovery_messages = state.values["_retry_recovery_messages"]
    _retry_recovery_tool_records = state.values["_retry_recovery_tool_records"]
    _tool_context_diagnostics = state.values["_tool_context_diagnostics"]
    _tool_error_payload = state.values["_tool_error_payload"]
    _tool_failure_is_retryable = state.values["_tool_failure_is_retryable"]
    _tool_result_reuse_allowed = state.values["_tool_result_reuse_allowed"]
    _tool_schema_map = state.values["_tool_schema_map"]
    _usage_from_dict = state.values["_usage_from_dict"]
    _validate_tool_call_batch = state.values["_validate_tool_call_batch"]
    agent_runner = state.values["agent_runner"]
    agent_timeout = state.values["agent_timeout"]
    all_reasoning = state.values["all_reasoning"]
    all_text = state.values["all_text"]
    archive_window = state.values["archive_window"]
    base = state.values["base"]
    build_summary_message = state.values["build_summary_message"]
    cancel_event = state.values["cancel_event"]
    chat_request_to_kemo = state.values["chat_request_to_kemo"]
    commit_cancelled_round = state.values["commit_cancelled_round"]
    commit_failed_round = state.values["commit_failed_round"]
    commit_terminal_round = state.values["commit_terminal_round"]
    compression_memory = state.values["compression_memory"]
    compression_usage = state.values["compression_usage"]
    config = state.values["config"]
    consumed_guidance = state.values["consumed_guidance"]
    consumed_guidance_details = state.values["consumed_guidance_details"]
    context_policy = state.values["context_policy"]
    context_selection = state.values["context_selection"]
    context_stats = state.values["context_stats"]
    copy = state.values["copy"]
    current_user_message = state.values["current_user_message"]
    defer_failure_commit = state.values["defer_failure_commit"]
    detect_task_plan_creation_boundary = state.values["detect_task_plan_creation_boundary"]
    durable_provider_responses = state.values["durable_provider_responses"]
    error_event = state.values["error_event"]
    estimate_messages_tokens = state.values["estimate_messages_tokens"]
    estimate_tools_tokens = state.values["estimate_tools_tokens"]
    execute_tool = state.values["execute_tool"]
    failure_limit = state.values["failure_limit"]
    failures = state.values["failures"]
    get_or_create_summary = state.values["get_or_create_summary"]
    identical_call_limit = state.values["identical_call_limit"]
    identical_calls = state.values["identical_calls"]
    invalid_tool_arguments_retry_limit = state.values["invalid_tool_arguments_retry_limit"]
    max_provider_iterations = state.values["max_provider_iterations"]
    max_tool_calls = state.values["max_tool_calls"]
    observed_reasoning = state.values["observed_reasoning"]
    observed_text = state.values["observed_text"]
    pending_tool_calls = state.values["pending_tool_calls"]
    prepare_guidance = state.values["prepare_guidance"]
    messages = state.values["messages"]
    projected_current_rounds = state.values["projected_current_rounds"]
    provider = state.values["provider"]
    provider_request_slot = state.values["provider_request_slot"]
    provider_responses = state.values["provider_responses"]
    queue_compression_memory = state.values["queue_compression_memory"]
    recovery_map = state.values["recovery_map"]
    refresh_dynamic_prompt_bundle = state.values["refresh_dynamic_prompt_bundle"]
    registry = state.values["registry"]
    replace = state.values["replace"]
    request = state.values["request"]
    resolve_reasoning_selection = state.values["resolve_reasoning_selection"]
    round_state = state.values["round_state"]
    run_id = state.values["run_id"]
    runtime_path = state.values["runtime_path"]
    runtime_provider = state.values["runtime_provider"]
    select_context = state.values["select_context"]
    session_id = state.values["session_id"]
    source = state.values["source"]
    source_policy = state.values["source_policy"]
    summary_cache = state.values["summary_cache"]
    summary_diagnostics = state.values["summary_diagnostics"]
    summary_usage = state.values["summary_usage"]
    terminal_committer = state.values["terminal_committer"]
    time = state.values["time"]
    tool_call_signature = state.values["tool_call_signature"]
    tool_records = state.values["tool_records"]
    tool_schemas = state.values["tool_schemas"]
    tool_timeout = state.values["tool_timeout"]
    uploaded_descriptors = state.values["uploaded_descriptors"]
    usage_total = state.values["usage_total"]
    user = state.values["user"]
    uuid = state.values["uuid"]
    window = state.values["window"]
    window_path = state.values["window_path"]
    reasoning_selection = resolve_reasoning_selection(
        config,
        runtime_provider,
        provider,
        cancel_event=cancel_event,
    )
    reasoning_extra = (
        {"reasoning_effort": reasoning_selection.effort}
        if reasoning_selection.enabled and reasoning_selection.effort
        else {"reasoning_enabled": False}
    )
    stream = bool(request.get("stream", runtime_provider.get("stream", False)))
    guidance_channel = request.get("_guidance_queue")
    retry_state = request.get("_retry_state")
    pending_guidance_ack: list[GuidanceInput] = []
    remote_guidance_assets: dict[str, str] = {}
    protocol_parent_request_id: str | None = None
    usage_total.clear()
    retry_usage_base = request.get("_retry_usage_base")
    if isinstance(retry_usage_base, dict):
        usage_total.update(copy.deepcopy(retry_usage_base))
        if summary_usage.get("total_tokens", 0) or summary_usage.get(
            "provider_request_count", 0
        ):
            _merge_usage(usage_total, _usage_from_dict(summary_usage))
    else:
        usage_total.update(copy.deepcopy(summary_usage))
    if compression_usage.get("provider_request_count", 0):
        _record_provider_request(
            usage_total,
            _usage_from_dict(compression_usage),
        )
    if summary_usage.get("total_tokens", 0):
        yield RunEvent(
            type="usage",
            usage=dict(summary_usage),
            metadata={"phase": "context_summary"},
        )
    seen_calls: dict[str, dict[str, Any]] = {
        signature: copy.deepcopy(item["result"])
        for signature, item in recovery_map.items()
        if item.get("replay_policy") == "reuse"
    } if recovery_map else {}
    blocked_recovery: dict[str, dict[str, Any]] = {
        signature: item
        for signature, item in recovery_map.items()
        if item.get("replay_policy") == "blocked"
    }
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
        assistant_text = "".join(iteration_text)
        iteration_reasoning_text = "".join(iteration_reasoning)
        messages.append(
            _assistant_tool_message(
                assistant_text,
                calls,
                reasoning=iteration_reasoning_text,
                native_reasoning=_response_reasoning_item(
                    provider_response,
                    streamed_content=iteration_reasoning_text,
                ),
            )
        )
        retryable_tool_failure: dict[str, Any] | None = None
        for call_index, call in enumerate(calls):
            if len(tool_records) >= max_tool_calls:
                _close_guidance(guidance_channel)
                yield commit_terminal_round(
                    status="limited",
                    reason="max_tool_iterations",
                    marker=(
                        f"[本轮工具调用已达到最大次数 {max_tool_calls}，"
                        "本轮已停止]"
                    ),
                    pending_message=(
                        "工具调用因本轮达到最大工具调用次数而未执行"
                    ),
                    pending_exception_type="ToolCallLimitExceeded",
                )
                return
            if cancel_event is not None and cancel_event.is_set():
                flush_iteration_observed()
                yield commit_cancelled_round()
                return
            signature = tool_call_signature(call.name, call.arguments)
            reuse_allowed = _tool_result_reuse_allowed(
                call.name,
                call.arguments,
            )
            identical_call_count = identical_calls.record(
                call.name, call.arguments
            )
            duplicate = False
            tool_started = time.monotonic()
            if identical_calls.is_blocked(identical_call_count):
                result_payload = {
                    "ok": False,
                    "error": {
                        "message": (
                            f"工具 {call.name} 使用完全相同参数连续调用已达到"
                            f"上限 {identical_call_limit} 次"
                        ),
                        "exception_type": (
                            "ConsecutiveIdenticalToolCallLimitExceeded"
                        ),
                        "limit": identical_call_limit,
                        "consecutive_identical_calls": identical_call_count,
                        "instruction": (
                            "请修改参数、改用其他工具或根据已有结果继续任务"
                        ),
                    },
                }
                status = "identical_call_blocked"
            elif failures.is_unavailable(call.name):
                result_payload = {
                    "ok": False,
                    "error": {
                        "message": (
                            f"工具 {call.name} 已连续失败 {failure_limit} 次，"
                            "本轮暂时不可用；请更换工具或调整方案"
                        ),
                        "exception_type": "ToolTemporarilyUnavailable",
                        "consecutive_failures": failure_limit,
                        "temporarily_unavailable": True,
                    },
                }
                status = "temporarily_unavailable"
            else:
                blocked_result = blocked_recovery.get(signature)
                duplicate = reuse_allowed and signature in seen_calls
                if blocked_result is not None:
                    result_payload = copy.deepcopy(
                        blocked_result.get("result")
                    )
                    status = "retry_reuse_blocked"
                    duplicate = True
                elif duplicate:
                    result_payload = copy.deepcopy(seen_calls[signature])
                    status = "duplicate_reused"
                else:
                    try:
                        definition = registry.get(call.name)
                        result = execute_tool(
                            definition,
                            call.arguments,
                            context={
                                "root": str(base),
                                "user": user,
                                "source": source,
                                "session_id": session_id,
                                "window": window_path.name,
                                "tool_timeout": tool_timeout,
                                "agent_timeout": agent_timeout,
                                "transport_registry": request.get(
                                    "_transport_registry"
                                ),
                                "task_plan_id": request.get("_task_plan_id"),
                                "task_plan_step_id": request.get("_task_plan_step_id"),
                                "task_plan_mode": request.get("_task_plan_mode"),
                                "knowledge_scopes": list(
                                    source_policy.direct_knowledge_scopes()
                                ),
                                "uploaded_files": copy.deepcopy(
                                    uploaded_descriptors
                                ),
                            },
                            timeout=tool_timeout,
                            cancel_event=cancel_event,
                        )
                        result_payload = {"ok": True, "result": result}
                        status = "completed"
                    except BaseException as exc:
                        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                            raise
                        cancelled_tool = isinstance(exc, ToolCancelledError)
                        oversized_result = isinstance(
                            exc, ToolResultTooLargeError
                        )
                        result_payload = {
                            "ok": False,
                            "error": {
                                **_tool_error_payload(exc),
                                **({"cancelled": True} if cancelled_tool else {}),
                            },
                        }
                        status = (
                            "cancelled"
                            if cancelled_tool
                            else (
                                "result_too_large"
                                if oversized_result
                                else "failed"
                            )
                        )
                        if bool(getattr(exc, "still_running", False)):
                            failures.unavailable.add(call.name)
                            status = "timed_out_running"
                    if result_payload.get("ok") is True:
                        if reuse_allowed:
                            seen_calls[signature] = copy.deepcopy(result_payload)
                        else:
                            seen_calls.pop(signature, None)
                    else:
                        seen_calls.pop(signature, None)
                failure_count = failures.record(
                    call.name,
                    succeeded=(
                        bool(result_payload.get("ok"))
                        or status == "result_too_large"
                    ),
                )
                if failure_count >= failure_limit:
                    result_payload["error"].update(
                        {
                            "consecutive_failures": failure_count,
                            "temporarily_unavailable": True,
                            "instruction": (
                                "请更换工具或调整方案，不要继续重试该工具"
                            ),
                        }
                    )
            elapsed_ms = max(0, round((time.monotonic() - tool_started) * 1000))
            record = {
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "status": status,
                "duplicate": duplicate,
                "consecutive_identical_calls": identical_call_count,
                "result": result_payload,
                "iteration": iteration,
                "elapsed_ms": elapsed_ms,
            }
            tool_records.append(record)
            pending_tool_calls.pop(call.id, None)
            yield RunEvent(
                type="tool_call_result",
                tool_call_id=call.id,
                tool_name=call.name,
                arguments=call.arguments,
                result=result_payload,
                metadata={
                    "status": status,
                    "duplicate": duplicate,
                    "consecutive_identical_calls": identical_call_count,
                    "iteration": iteration,
                    "elapsed_ms": elapsed_ms,
                },
            )
            tool_value = result_payload.get("result")
            tool_artifacts = (
                tool_value.get("artifacts")
                if isinstance(tool_value, dict)
                else None
            )
            if isinstance(tool_artifacts, list):
                for artifact in tool_artifacts:
                    if isinstance(artifact, dict):
                        yield RunEvent(
                            type="media_output",
                            tool_call_id=call.id,
                            tool_name=call.name,
                            result=copy.deepcopy(artifact),
                            metadata={
                                "artifact": copy.deepcopy(artifact),
                                "source": "tool_result",
                            },
                        )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": _json_result(result_payload),
                }
            )
            if (
                defer_failure_commit
                and retryable_tool_failure is None
                and _tool_failure_is_retryable(result_payload, status)
            ):
                retryable_tool_failure = {
                    "tool_name": call.name,
                    "error": copy.deepcopy(result_payload.get("error") or {}),
                    "status": status,
                }
            if request.get("_task_plan_mode") is None:
                task_plan_boundary = detect_task_plan_creation_boundary(
                    tool_name=call.name,
                    arguments=call.arguments,
                    result_payload=result_payload,
                )
            if task_plan_boundary is not None:
                for pending_call in calls[call_index + 1 :]:
                    pending_payload = {
                        "ok": False,
                        "error": {
                            "message": (
                                "任务计划已创建，后续工具必须等待批准或由任务计划执行器处理"
                            ),
                            "exception_type": "TaskPlanCreationBoundary",
                            "plan_id": task_plan_boundary.plan_id,
                        },
                    }
                    pending_record = {
                        "id": pending_call.id,
                        "name": pending_call.name,
                        "arguments": pending_call.arguments,
                        "status": "not_executed",
                        "duplicate": False,
                        "consecutive_identical_calls": 0,
                        "result": pending_payload,
                        "iteration": iteration,
                        "elapsed_ms": 0,
                    }
                    tool_records.append(pending_record)
                    pending_tool_calls.pop(pending_call.id, None)
                    yield RunEvent(
                        type="tool_call_result",
                        tool_call_id=pending_call.id,
                        tool_name=pending_call.name,
                        arguments=pending_call.arguments,
                        result=pending_payload,
                        metadata={
                            "status": "not_executed",
                            "duplicate": False,
                            "consecutive_identical_calls": 0,
                            "iteration": iteration,
                            "elapsed_ms": 0,
                            "plan_id": task_plan_boundary.plan_id,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": pending_call.id,
                            "name": pending_call.name,
                            "content": _json_result(pending_payload),
                        }
                    )
                boundary_text = task_plan_boundary.message
                prefix = "\n\n" if all_text else ""
                visible_boundary_text = f"{prefix}{boundary_text}"
                all_text.append(visible_boundary_text)
                observed_text.append(visible_boundary_text)
                yield RunEvent(type="text_delta", content=visible_boundary_text)
                _close_guidance(guidance_channel)
                completed = True
                break
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


    state.values.update(
        {
            "prompt_bundle": prompt_bundle,
            "system_message": system_message,
            "final_metadata": final_metadata,
            "completed": completed,
            "task_plan_boundary": task_plan_boundary,
            "reasoning_selection": reasoning_selection,
            "reasoning_extra": reasoning_extra,
            "stream": stream,
            "guidance_channel": guidance_channel,
            "retry_state": retry_state,
            "pending_guidance_ack": pending_guidance_ack,
            "remote_guidance_assets": remote_guidance_assets,
            "protocol_parent_request_id": protocol_parent_request_id,
            "messages": messages,
            "usage_total": usage_total,
            "tool_records": tool_records,
            "pending_tool_calls": pending_tool_calls,
            "provider_responses": provider_responses,
            "durable_provider_responses": durable_provider_responses,
            "consumed_guidance": consumed_guidance,
            "consumed_guidance_details": consumed_guidance_details,
            "guidance_messages_for_retry": guidance_messages_for_retry,
            "tool_argument_retry_count": tool_argument_retry_count,
            "last_provider_input_tokens": last_provider_input_tokens,
            "last_sent_local_tokens": last_sent_local_tokens,
            "context_selection": context_selection,
            "context_stats": context_stats,
            "summary_cache": summary_cache,
            "summary_diagnostics": summary_diagnostics,
            "compression_memory": compression_memory,
            "compression_usage": compression_usage,
            "window": window,
            "runtime_path": runtime_path,
        }
    )
    # Reaching the end means the provider loop completed normally; early
    # terminal returns leave the default stop_main flag untouched.
    state.stop_main = False
