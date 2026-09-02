"""Conversation request loop extracted from the public runtime entry point.

The function remains behavior-compatible; runtime dependencies are resolved lazily
so existing monkeypatch and injection points continue to target run.conversation.runtime.
"""

from __future__ import annotations

from run.conversation.request_setup import build_request_context as _build_request_context
from run.conversation.media_routing import (
    prepare_provider_request as _prepare_provider_request,
)
from run.conversation.compression import (
    prepare_compression as _prepare_compression,
)
from run.conversation.runtime_state import RoundRuntime
from run.conversation.provider_loop import (
    ProviderLoopState,
    run_provider_loop as _run_provider_loop,
)

def iter_request_events_impl(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] | None = None,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] | None = None,
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    """Run one complete model/tool loop, committing only on successful done."""

    # Resolve dependencies lazily to keep runtime's public import surface stable.
    from run.conversation import runtime as _runtime
    if provider_factory is None:
        provider_factory = _runtime.create_provider
    if tool_registry_factory is None:
        tool_registry_factory = _runtime.discover_tools
    AgentRunner = _runtime.AgentRunner
    Any = _runtime.Any
    AttachmentError = _runtime.AttachmentError
    AudioContent = _runtime.AudioContent
    Callable = _runtime.Callable
    ChatRequest = _runtime.ChatRequest
    ConsecutiveIdenticalToolCallTracker = _runtime.ConsecutiveIdenticalToolCallTracker
    ConsecutiveToolFailureTracker = _runtime.ConsecutiveToolFailureTracker
    ContextLengthExceededError = _runtime.ContextLengthExceededError
    ContextPolicy = _runtime.ContextPolicy
    EngineError = _runtime.EngineError
    FileContent = _runtime.FileContent
    GuidanceInput = _runtime.GuidanceInput
    ImageContent = _runtime.ImageContent
    Iterator = _runtime.Iterator
    MainAgentSourcePolicy = _runtime.MainAgentSourcePolicy
    MemoryStore = _runtime.MemoryStore
    Path = _runtime.Path
    ProviderCongestionError = _runtime.ProviderCongestionError
    ProviderError = _runtime.ProviderError
    RoundState = _runtime.RoundState
    RunAssetResolver = _runtime.RunAssetResolver
    RunDependencies = _runtime.RunDependencies
    RunEvent = _runtime.RunEvent
    RunIdentity = _runtime.RunIdentity
    SUMMARY_CHUNK_TOKEN_BUDGET = _runtime.SUMMARY_CHUNK_TOKEN_BUDGET
    SUMMARY_MAX_OUTPUT_TOKENS = _runtime.SUMMARY_MAX_OUTPUT_TOKENS
    SUMMARY_STORE_REF = _runtime.SUMMARY_STORE_REF
    TaskPlanCreationBoundary = _runtime.TaskPlanCreationBoundary
    TerminalRoundCommitter = _runtime.TerminalRoundCommitter
    TerminalRoundContext = _runtime.TerminalRoundContext
    TextContent = _runtime.TextContent
    ToolCall = _runtime.ToolCall
    ToolCancelledError = _runtime.ToolCancelledError
    ToolRegistry = _runtime.ToolRegistry
    ToolResultTooLargeError = _runtime.ToolResultTooLargeError
    Usage = _runtime.Usage
    VideoContent = _runtime.VideoContent
    _assistant_tool_message = _runtime._assistant_tool_message
    _close_guidance = _runtime._close_guidance
    _commit_verified_manual_compression = _runtime._commit_verified_manual_compression
    _committed_failure_event = _runtime._committed_failure_event
    _compress_per_round_tool_think = _runtime._compress_per_round_tool_think
    _content_display = _runtime._content_display
    _content_for_message = _runtime._content_for_message
    _copy_committed_round_to_archive = _runtime._copy_committed_round_to_archive
    _drain_guidance = _runtime._drain_guidance
    _drain_or_close_guidance = _runtime._drain_or_close_guidance
    _ensure_fixed_content_fits = _runtime._ensure_fixed_content_fits
    _event_provider_response = _runtime._event_provider_response
    _extract_memory_backlog = _runtime._extract_memory_backlog
    _extract_round_memory = _runtime._extract_round_memory
    _failure_requires_immediate_commit = _runtime._failure_requires_immediate_commit
    _invalid_tool_name = _runtime._invalid_tool_name
    _is_context_length_exceeded = _runtime._is_context_length_exceeded
    _is_invalid_tool_arguments_error = _runtime._is_invalid_tool_arguments_error
    _json_result = _runtime._json_result
    _memory_injected_chars = _runtime._memory_injected_chars
    _merge_usage = _runtime._merge_usage
    _messages_with_tool_argument_repair = _runtime._messages_with_tool_argument_repair
    _metric_provider_responses = _runtime._metric_provider_responses
    _provider_events = _runtime._provider_events
    _queue_summary_memory_extraction = _runtime._queue_summary_memory_extraction
    _raise_if_context_length_exceeded = _runtime._raise_if_context_length_exceeded
    _record_provider_request = _runtime._record_provider_request
    _remember_retry_guidance = _runtime._remember_retry_guidance
    _replace_primary_system_message = _runtime._replace_primary_system_message
    _request_content_blocks = _runtime._request_content_blocks
    _required_text = _runtime._required_text
    _response_reasoning_item = _runtime._response_reasoning_item
    _retry_recovery_messages = _runtime._retry_recovery_messages
    _retry_recovery_provider_responses = _runtime._retry_recovery_provider_responses
    _retry_recovery_tool_records = _runtime._retry_recovery_tool_records
    _session_lock = _runtime._session_lock
    _tool_context_diagnostics = _runtime._tool_context_diagnostics
    _tool_error_payload = _runtime._tool_error_payload
    _tool_failure_is_retryable = _runtime._tool_failure_is_retryable
    _tool_result_reuse_allowed = _runtime._tool_result_reuse_allowed
    _tool_schema_map = _runtime._tool_schema_map
    _trim_to_max_rounds = _runtime._trim_to_max_rounds
    _uploaded_file_context = _runtime._uploaded_file_context
    _usage_from_dict = _runtime._usage_from_dict
    _usage_total = _runtime._usage_total
    _validate_tool_call_batch = _runtime._validate_tool_call_batch
    append_round_items = _runtime.append_round_items
    apply_runtime_tool_policy = _runtime.apply_runtime_tool_policy
    build_context_snapshot = _runtime.build_context_snapshot
    build_prompt_bundle = _runtime.build_prompt_bundle
    build_summary_message = _runtime.build_summary_message
    chat_request_to_kemo = _runtime.chat_request_to_kemo
    commit_terminal_windows = _runtime.commit_terminal_windows
    copy = _runtime.copy
    detect_task_plan_creation_boundary = _runtime.detect_task_plan_creation_boundary
    error_event = _runtime.error_event
    estimate_messages_tokens = _runtime.estimate_messages_tokens
    estimate_tools_tokens = _runtime.estimate_tools_tokens
    execute_tool = _runtime.execute_tool
    get_or_create_summary = _runtime.get_or_create_summary
    history_attachment_descriptors = _runtime.history_attachment_descriptors
    load_config = _runtime.load_config
    load_runtime_window = _runtime.load_runtime_window
    main_model_supports_input = _runtime.main_model_supports_input
    memory_extraction_mode = _runtime.memory_extraction_mode
    patch_archive_metadata = _runtime.patch_archive_metadata
    prepare_guidance = _runtime.prepare_guidance
    prepare_window = _runtime.prepare_window
    project_root = _runtime.project_root
    provider_request_slot = _runtime.provider_request_slot
    provider_runtime_config = _runtime.provider_runtime_config
    queue_memory_extraction = _runtime.queue_memory_extraction
    read_summary_cache = _runtime.read_summary_cache
    replace = _runtime.replace
    refresh_dynamic_prompt_bundle = _runtime.refresh_dynamic_prompt_bundle
    resolve_reasoning_selection = _runtime.resolve_reasoning_selection
    restore_summary_cache = _runtime.restore_summary_cache
    select_context = _runtime.select_context
    select_vision_route = _runtime.select_vision_route
    threading = _runtime.threading
    time = _runtime.time
    tool_call_signature = _runtime.tool_call_signature
    update_run_state = _runtime.update_run_state
    uuid = _runtime.uuid

    round_state = RoundState(run_started=time.monotonic())
    run_started = round_state.run_started
    dependencies = RunDependencies(
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=cancel_event,
    )
    try:
        request_context = _build_request_context(
            request,
            root=root,
            project_root_fn=project_root,
            required_text_fn=_required_text,
            request_content_blocks_fn=_request_content_blocks,
            content_display_fn=_content_display,
        )
        base = request_context.base
        user = request_context.user
        source = request_context.source
        session_id = request_context.session_id
        run_id = request_context.run_id
        prompt = request_context.prompt
        content_blocks = request_context.content_blocks
        identity = request_context.identity
        uploaded_file_context = ""
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
            raise
        yield error_event(exc, phase="request")
        return

    with _session_lock(identity.root, identity.user, identity.source, identity.session_id):
        terminal_committer = None
        try:
            prepared_request = _prepare_provider_request(
                request,
                base=base,
                user=user,
                source=source,
                session_id=session_id,
                content_blocks=content_blocks,
                dependencies=dependencies,
                cancel_event=cancel_event,
            )
            config = prepared_request.config
            context_policy = prepared_request.context_policy
            source_policy = prepared_request.source_policy
            runtime_provider = prepared_request.runtime_provider
            provider = prepared_request.provider
            uploaded_descriptors = prepared_request.uploaded_descriptors
            history_attachments = prepared_request.history_attachments
            provider_media = prepared_request.provider_media
            direct_asset_ids = prepared_request.direct_asset_ids
            vision_route = prepared_request.vision_route
            uploaded_file_context = prepared_request.uploaded_file_context
            durable_user_content_blocks = prepared_request.durable_user_content_blocks
            agent_runner = prepared_request.agent_runner
            window_path = prepared_request.window_path
            archive_window = prepared_request.archive_window
            try:
                round_state.history_run_registered = (
                    update_run_state(
                        base,
                        user,
                        source,
                        session_id,
                        run_state="running",
                        run_id=run_id or None,
                        directory=window_path,
                        session_generation=str(
                            (archive_window.get("data") or {}).get(
                                "session_generation"
                            )
                            or ""
                        ),
                    )
                    is not None
                )
            except Exception as exc:
                round_state.history_run_error = {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                }
            round_runtime = RoundRuntime(
                request=request,
                identity=identity,
                dependencies=dependencies,
                round_state=round_state,
                config=config,
                context_policy=context_policy,
                source_policy=source_policy,
                runtime_provider=runtime_provider,
                provider=provider,
                agent_runner=agent_runner,
                window_path=window_path,
                archive_window=archive_window,
                content_blocks=content_blocks,
                prompt=prompt,
                uploaded_descriptors=uploaded_descriptors,
                provider_media=provider_media,
                vision_route=vision_route,
                uploaded_file_context=uploaded_file_context,
                durable_user_content_blocks=durable_user_content_blocks,
            )
            compression = _prepare_compression(
                round_runtime,
                cancel_event=cancel_event,
            )
            runtime_path = compression.runtime_path
            window = compression.window
            registry = compression.registry
            tool_schemas = compression.tool_schemas
            tool_timeout = compression.tool_timeout
            agent_timeout = compression.agent_timeout
            max_tool_calls = compression.max_tool_calls
            max_provider_iterations = compression.max_provider_iterations
            identical_call_limit = compression.identical_call_limit
            invalid_tool_arguments_retry_limit = (
                compression.invalid_tool_arguments_retry_limit
            )
            failure_limit = compression.failure_limit
            failures = compression.failures
            identical_calls = compression.identical_calls
            memory_store = compression.memory_store
            prompt_bundle = compression.prompt_bundle
            system_message = compression.system_message
            compress_only = compression.compress_only
            queue_compression_memory = compression.queue_compression_memory
            provider_content_blocks = compression.provider_content_blocks
            current_user_message = compression.current_user_message
            force_compress = compression.force_compress
            persisted_summary_cache = compression.persisted_summary_cache
            persisted_summary_message = build_summary_message(
                persisted_summary_cache
            )
            context_selection = compression.context_selection
            all_text = round_state.all_text
            all_reasoning = round_state.all_reasoning
            observed_text = round_state.observed_text
            observed_reasoning = round_state.observed_reasoning
            tool_records = round_state.tool_records
            pending_tool_calls = round_state.pending_tool_calls
            consumed_guidance = round_state.consumed_guidance
            consumed_guidance_details = round_state.consumed_guidance_details
            provider_responses = round_state.provider_responses
            durable_provider_responses = round_state.durable_provider_responses
            usage_total = round_state.usage_total
            context_stats = compression.context_stats
            summary_usage = compression.summary_usage
            compression_memory = compression.compression_memory
            compression_usage = compression.compression_usage
            compression_notice_active = compression.compression_notice_active
            projected_current_rounds = compression.projected_current_rounds
            compression_trigger = compression.compression_trigger
            subagent_events = compression.subagent_events
            summary_cache = compression.summary_cache
            summary_diagnostics = compression.summary_diagnostics
            for compression_event in compression.events:
                yield compression_event
            terminal_committer = TerminalRoundCommitter(
                TerminalRoundContext(
                    identity=identity,
                    dependencies=dependencies,
                    state=round_state,
                    request=request,
                    content_blocks=durable_user_content_blocks,
                    prompt=prompt,
                    window=window,
                    archive_window=archive_window,
                    window_path=window_path,
                    runtime_path=runtime_path,
                    context_selection=context_selection,
                    context_policy=context_policy,
                    summary_cache=summary_cache,
                    system_message=system_message,
                    tool_schemas=tool_schemas,
                    prompt_bundle=prompt_bundle,
                    runtime_provider=runtime_provider,
                    queue_compression_memory=queue_compression_memory,
                )
            )
            commit_terminal_round = terminal_committer.commit_terminal_round
            commit_cancelled_round = terminal_committer.commit_cancelled_round
            defer_failure_commit = bool(request.get("_defer_failure_commit", False))
            task_plan_boundary = None

            def commit_failed_round(
                error: Any,
                *,
                reason: str = "provider_error",
            ) -> RunEvent:
                return terminal_committer.commit_failed_round(
                    error,
                    reason=reason,
                    persist=(
                        not defer_failure_commit
                        or _failure_requires_immediate_commit(error)
                    ),
                )

            if cancel_event is not None and cancel_event.is_set():
                yield commit_cancelled_round()
                return
            messages = context_selection.messages
            retry_recovery = request.get("_retry_recovery")
            recovery_map: dict[str, dict[str, Any]] = {}
            if isinstance(retry_recovery, list):
                for raw in retry_recovery:
                    if not isinstance(raw, dict):
                        continue
                    name = str(raw.get("name") or "").strip()
                    arguments = raw.get("arguments")
                    result = raw.get("result")
                    if not name or not isinstance(arguments, dict) or not isinstance(result, dict):
                        continue
                    ok = result.get("ok") is True
                    if ok and not _tool_result_reuse_allowed(name, arguments):
                        continue
                    recovery_map[tool_call_signature(name, arguments)] = {
                        "id": str(raw.get("id") or ""),
                        "name": name,
                        "arguments": copy.deepcopy(arguments),
                        "result": copy.deepcopy(result),
                        "replay_policy": "reuse" if ok else "blocked",
                    }
                if recovery_map:
                    messages.extend(_retry_recovery_messages(recovery_map))
            context_stats = context_selection.stats()
            context_stats["summary"] = summary_diagnostics
            context_stats["summary_usage"] = summary_usage
            round_state.context_stats = context_stats
            if compression_notice_active:
                compression_failed = bool(summary_diagnostics.get("failed"))
                yield RunEvent(
                    type="context_compression",
                    content=(
                        "对话上下文压缩失败"
                        if compression_failed
                        else "对话上下文摘要已就绪"
                    ),
                    metadata={
                        "status": "failed" if compression_failed else "ready",
                        "trigger": compression_trigger,
                        "run_id": run_id,
                        "rounds_before": len(context_selection.all_rounds)
                        + projected_current_rounds,
                        "rounds_removed": len(context_selection.removed_rounds),
                        "rounds_remaining": len(context_selection.kept_rounds)
                        + projected_current_rounds,
                        "memory_mode": (
                            "background" if queue_compression_memory else "synchronous"
                        ),
                        "memory_status": (
                            "queued_after_commit"
                            if queue_compression_memory and not compression_failed
                            else str((compression_memory or {}).get("status") or "")
                        ),
                    },
                )
            for subagent_event in subagent_events:
                yield subagent_event
            if compress_only:
                compression_applied = False
                summary_failed = bool(summary_diagnostics.get("failed"))
                if summary_failed:
                    restore_summary_cache(runtime_path, persisted_summary_cache)
                    summary_cache = persisted_summary_cache
                if (
                    not summary_failed
                    and summary_cache is not None
                    and context_selection.removed_rounds
                ):
                    previous_round_offset = max(
                        0,
                        int(
                            (window.get("data", {}).get("context") or {}).get(
                                "round_offset", 0
                            )
                        ),
                    )
                    runtime_window = _trim_to_max_rounds(
                        window,
                        max(1, len(context_selection.kept_rounds)),
                    )
                    runtime_window["data"]["context"] = {
                        **context_stats,
                        "round_offset": max(
                            0,
                            int(archive_window.get("data", {}).get("rounds") or 0)
                            - int(runtime_window["data"].get("rounds") or 0),
                        ),
                        "workspace_rounds": int(
                            runtime_window["data"].get("rounds") or 0
                        ),
                        "summary_cache": SUMMARY_STORE_REF,
                    }
                    runtime_selection = select_context(
                        window=runtime_window,
                        policy=context_policy,
                        system_message=system_message,
                        summary_message=build_summary_message(summary_cache),
                        current_user_message=None,
                        tools=tool_schemas,
                    )
                    runtime_window["data"]["context_snapshot"] = build_context_snapshot(
                        runtime_selection,
                        system_prompt=prompt_bundle.text,
                        summary_message=build_summary_message(summary_cache),
                        capacity_tokens=context_policy.token_limit,
                    )
                    expected_rounds = int(
                        runtime_window["data"].get("rounds") or 0
                    )
                    expected_round_offset = max(
                        0,
                        int(archive_window.get("data", {}).get("rounds") or 0)
                        - expected_rounds,
                    )
                    _commit_verified_manual_compression(
                        runtime_path=runtime_path,
                        original_window=window,
                        compacted_window=runtime_window,
                        summary_cache=summary_cache,
                        previous_summary_cache=persisted_summary_cache,
                        removed_round_numbers=[
                            item.number for item in context_selection.removed_rounds
                        ],
                        previous_round_offset=previous_round_offset,
                        expected_rounds=expected_rounds,
                        expected_round_offset=expected_round_offset,
                    )
                    compression_applied = True
                if queue_compression_memory:
                    if bool(summary_diagnostics.get("failed")):
                        compression_memory = {
                            "status": "failed",
                            "reason": "context_summary_failed",
                            "round": int(
                                archive_window.get("data", {}).get("rounds") or 0
                            ),
                            "candidates": 0,
                            "error": {
                                "message": "上下文摘要生成失败，未登记后台记忆提取",
                                "exception_type": "ContextSummaryError",
                            },
                        }
                    else:
                        compression_memory = queue_memory_extraction(
                            base,
                            user,
                            source,
                            session_id,
                            target_round=int(
                                archive_window.get("data", {}).get("rounds") or 0
                            ),
                            reason="manual_compression",
                        )
                compression_total_usage = copy.deepcopy(summary_usage)
                if compression_usage.get("provider_request_count", 0):
                    _record_provider_request(
                        compression_total_usage,
                        _usage_from_dict(compression_usage),
                    )
                yield RunEvent(
                    type="done",
                    usage=dict(compression_total_usage),
                    metadata={
                        "text": "",
                        "reasoning": "",
                        "usage": dict(compression_total_usage),
                        "model": runtime_provider["model"],
                        "user": user,
                        "source": source,
                        "session_id": session_id,
                        "window": window_path.name,
                        "context": context_stats,
                        "prompt": prompt_bundle.diagnostics,
                        "summary_cache": (
                            SUMMARY_STORE_REF
                            if summary_cache is not None
                            else None
                        ),
                        "compressed": compression_applied,
                        "compression_verified": compression_applied,
                        "committed": False,
                        "memory": compression_memory,
                    },
                )
                return

            provider_loop_state = ProviderLoopState(values=locals())
            for provider_event in _run_provider_loop(provider_loop_state):
                yield provider_event
            provider_values = provider_loop_state.values
            prompt_bundle = provider_values.get("prompt_bundle", prompt_bundle)
            system_message = provider_values.get("system_message", system_message)
            context_selection = provider_values.get(
                "context_selection", context_selection
            )
            context_stats = provider_values.get("context_stats", context_stats)
            summary_cache = provider_values.get("summary_cache", summary_cache)
            summary_diagnostics = provider_values.get(
                "summary_diagnostics", summary_diagnostics
            )
            compression_memory = provider_values.get(
                "compression_memory", compression_memory
            )
            compression_usage = provider_values.get(
                "compression_usage", compression_usage
            )
            tool_argument_retry_count = provider_values.get(
                "tool_argument_retry_count", 0
            )
            final_metadata = provider_values.get("final_metadata", {})
            window = provider_values.get("window", window)
            runtime_path = provider_values.get("runtime_path", runtime_path)
            task_plan_boundary = provider_values.get(
                "task_plan_boundary", task_plan_boundary
            )
            if provider_loop_state.stop_main:
                return
            round_number = int(window["data"].get("rounds", 0)) + 1
            archive_round_number = int(
                archive_window["data"].get("rounds", 0)
            ) + 1
            round_elapsed_ms = max(0, round((time.monotonic() - run_started) * 1000))
            text = "".join(all_text)
            reasoning = "".join(all_reasoning)
            user_metadata = {
                **(
                    {"input_attachments": history_attachments}
                    if history_attachments
                    else {}
                ),
                **(
                    copy.deepcopy(request.get("_user_metadata"))
                    if isinstance(request.get("_user_metadata"), dict)
                    else {}
                ),
            }
            window["text"]["messages"].extend(
                [
                    {
                        "role": "user",
                        "content": prompt,
                        **(
                            {"attachments": copy.deepcopy(history_attachments)}
                            if history_attachments
                            else {}
                        ),
                        **({"metadata": copy.deepcopy(user_metadata)} if user_metadata else {}),
                    },
                    {"role": "assistant", "content": text},
                ]
            )
            window["think"]["rounds"].append({"round": round_number, "content": reasoning})
            committed_tool_records = [
                *round_state.recovered_tool_records,
                *tool_records,
            ]
            window["tool"]["rounds"].append(
                {"round": round_number, "calls": committed_tool_records}
            )
            history_provider_responses = [
                *_retry_recovery_provider_responses(recovery_map),
                *durable_provider_responses,
            ]
            append_round_items(
                window,
                round_number=round_number,
                user_content=[
                    block.model_dump(mode="json", exclude_none=True)
                    for block in durable_user_content_blocks
                ],
                reasoning=reasoning,
                text=text,
                tool_records=tool_records,
                provider_responses=history_provider_responses,
                user_metadata=user_metadata or None,
            )
            window["data"]["rounds"] = round_number
            round_metrics = window["data"].setdefault("round_metrics", [])
            if not isinstance(round_metrics, list):
                round_metrics = []
                window["data"]["round_metrics"] = round_metrics
            round_metrics.append(
                {
                    "round": round_number,
                    "usage": dict(usage_total),
                    "elapsed_ms": round_elapsed_ms,
                    "tool_calls": len(committed_tool_records),
                    "tool_argument_retries": tool_argument_retry_count,
                    "guidance": list(consumed_guidance),
                    "guidance_details": copy.deepcopy(consumed_guidance_details),
                    "provider_responses": _metric_provider_responses(provider_responses),
                    **(
                        {
                            "status": "completed",
                            "stop_reason": task_plan_boundary.stop_reason,
                            "plan_id": task_plan_boundary.plan_id,
                            "task_plan_status": task_plan_boundary.status,
                            "task_plan_auto_accept": task_plan_boundary.auto_accept,
                            "awaiting_user_approval": (
                                task_plan_boundary.awaiting_user_approval
                            ),
                        }
                        if task_plan_boundary is not None
                        else {}
                    ),
                    **(
                        {"input_attachments": copy.deepcopy(history_attachments)}
                        if history_attachments
                        else {}
                    ),
                }
            )
            window["data"]["context"] = {
                **context_stats,
                "round_offset": max(0, archive_round_number - round_number),
                "workspace_rounds": round_number,
                "summary_cache": (
                    SUMMARY_STORE_REF if summary_cache is not None else None
                ),
            }
            _merge_usage(window["data"]["token_usage"], _usage_from_dict(usage_total))
            _copy_committed_round_to_archive(
                archive_window,
                window,
                round_number,
                archive_round_number,
            )
            tool_think_compression: dict[str, Any]
            try:
                tool_think_compression = _compress_per_round_tool_think(
                    window=window,
                    conserved_rounds=context_policy.recent_tool_rounds,
                    agent_runner=agent_runner,
                    cancel_event=cancel_event,
                    source=source,
                    session_id=session_id,
                )
                compression_usage = _usage_from_dict(
                    tool_think_compression.get("usage") or {}
                )
                if compression_usage.total_tokens:
                    _merge_usage(usage_total, compression_usage)
                    _merge_usage(window["data"]["token_usage"], compression_usage)
                    _merge_usage(
                        archive_window["data"]["token_usage"], compression_usage
                    )
                    window["data"]["round_metrics"][-1]["usage"] = dict(usage_total)
                    archive_window["data"]["round_metrics"][-1]["usage"] = dict(
                        usage_total
                    )
            except Exception as exc:
                tool_think_compression = {
                    "compressed": False,
                    "round": None,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                }
            compression_applied = bool(
                summary_cache is not None and context_selection.removed_rounds
            )
            runtime_round_limit = (
                max(1, len(context_selection.kept_rounds) + 1)
                if compression_applied
                else context_policy.max_rounds
            )
            runtime_window = _trim_to_max_rounds(window, runtime_round_limit)
            next_summary_message = build_summary_message(summary_cache)
            next_context_selection = select_context(
                window=runtime_window,
                policy=context_policy,
                system_message=system_message,
                summary_message=next_summary_message,
                current_user_message=None,
                tools=tool_schemas,
            )
            runtime_window["data"]["context"] = {
                **next_context_selection.stats(),
                "summary": summary_diagnostics,
                "summary_usage": summary_usage,
                "round_offset": max(
                    0,
                    archive_round_number
                    - int(runtime_window["data"].get("rounds", 0)),
                ),
                "workspace_rounds": int(runtime_window["data"].get("rounds", 0)),
                "summary_cache": (
                    SUMMARY_STORE_REF if summary_cache is not None else None
                ),
            }
            runtime_window["data"]["context_snapshot"] = build_context_snapshot(
                next_context_selection,
                system_prompt=prompt_bundle.text,
                summary_message=next_summary_message,
                capacity_tokens=context_policy.token_limit,
            )
            extraction_mode = memory_extraction_mode(config)
            archive_data = archive_window.setdefault("data", {})
            if archive_data.get("memory_processed_round") is None:
                archive_data["memory_processed_round"] = max(
                    0, archive_round_number - 1
                )
            memory_processed_round = max(
                0, int(archive_data.get("memory_processed_round") or 0)
            )
            extract_current_round = bool(
                extraction_mode == "on_commit"
                and memory_processed_round == archive_round_number - 1
            )
            if extract_current_round:
                initial_memory_status = "processing"
            elif extraction_mode in {"background", "on_commit"}:
                initial_memory_status = "pending"
            elif extraction_mode == "compression_only":
                initial_memory_status = "deferred"
            else:
                initial_memory_status = "disabled"
            archive_data["memory_status"] = initial_memory_status
            archive_data.pop("memory_error", None)
            active_key = request.get("_history_active_key")
            commit_terminal_windows(
                window_path,
                archive_window,
                runtime_path,
                runtime_window,
                summary_cache=summary_cache,
                run_state="idle",
                active_key=(
                    active_key.strip()
                    if isinstance(active_key, str) and active_key.strip()
                    else None
                ),
            )
            round_state.finalized = True
            round_state.history_run_registered = False
            if queue_compression_memory and compression_applied:
                compression_memory = _queue_summary_memory_extraction(
                    root=base,
                    user=user,
                    source=source,
                    session_id=session_id,
                    summary_cache=summary_cache,
                    archive_round_number=archive_round_number,
                    reason="automatic_compression",
                )
            history_index_error: dict[str, Any] | None = round_state.history_run_error

            memory_extraction: dict[str, Any] = {
                "status": "skipped",
                "candidate_count": 0,
                "reason": (
                    "memory_backlog_pending"
                    if extraction_mode == "on_commit" and not extract_current_round
                    else (
                        "deferred_until_compression"
                        if extraction_mode == "compression_only"
                        else (
                            "background_extraction_pending"
                            if extraction_mode == "background"
                            else "memory_extraction_disabled"
                        )
                    )
                ),
                "error": None,
            }
            if extract_current_round:
                try:
                    memory_extraction = _extract_round_memory(
                        root=base,
                        user=user,
                        config=config,
                        round_number=archive_round_number,
                        prompt=prompt,
                        text=text,
                        reasoning=reasoning,
                        tool_records=tool_records,
                        agent_runner=agent_runner,
                        cancel_event=cancel_event,
                        agent_source=source,
                        session_id=session_id,
                    )
                except Exception as exc:
                    memory_extraction = {
                        "status": "failed",
                        "candidate_count": 0,
                        "error": {
                            "message": str(exc),
                            "exception_type": type(exc).__name__,
                        },
                    }
            extraction_status = str(memory_extraction.get("status") or "pending")
            memory_error = (
                memory_extraction.get("error")
                if isinstance(memory_extraction.get("error"), dict)
                else {"message": "记忆提取失败"}
            )
            if extraction_status == "completed":
                archive_data["memory_processed_round"] = archive_round_number
                archive_data["memory_status"] = "completed"
                archive_data.pop("memory_error", None)
                try:
                    patch_archive_metadata(
                        window_path,
                        archive_window,
                        updates={
                            "memory_processed_round": archive_round_number,
                            "memory_status": "completed",
                        },
                        removals=("memory_error",),
                        run_state="idle",
                    )
                except Exception as exc:
                    history_index_error = history_index_error or {
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    }
            elif extraction_status == "failed":
                archive_data["memory_status"] = "failed"
                archive_data["memory_error"] = memory_error
                try:
                    patch_archive_metadata(
                        window_path,
                        archive_window,
                        updates={
                            "memory_status": "failed",
                            "memory_error": memory_error,
                        },
                        run_state="idle",
                    )
                except Exception as exc:
                    history_index_error = history_index_error or {
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    }

            # Prompt 注入和用户主动查看只是读操作，不得改变临时记忆权重。
            # 权重只由保存/压缩等历史整理管线的用户原文命中更新。
            memory_weighted_files: list[str] = []
            memory_weight_error = None
            final_metadata.update(
                {
                    "text": text,
                    "reasoning": reasoning,
                    "usage": usage_total,
                    "model": final_metadata.get("model") or runtime_provider["model"],
                    "user": user,
                    "source": source,
                    "session_id": session_id,
                    "window": window_path.name,
                    "tool_calls": len(tool_records),
                    "tool_argument_retries": tool_argument_retry_count,
                    "elapsed_ms": round_elapsed_ms,
                    "run_id": run_id,
                    "guidance_count": len(consumed_guidance),
                    "guidance_details": copy.deepcopy(consumed_guidance_details),
                    "context": context_stats,
                    "tool_think_compression": tool_think_compression,
                    "prompt": prompt_bundle.diagnostics,
                    "memory": {
                        "injected_files": list(prompt_bundle.memory_files),
                        "weighted_files": memory_weighted_files,
                        "weight_error": memory_weight_error,
                        "injected_chars": _memory_injected_chars(prompt_bundle),
                        "extraction_task_id": None,
                        "extraction_error": None,
                        "extraction_mode": extraction_mode,
                        "compression_extraction": compression_memory,
                        "round_extraction": memory_extraction,
                    },
                    "history_index_error": history_index_error,
                    "knowledge": {
                        "documents": prompt_bundle.diagnostics["knowledge_documents"],
                        "injected_chars": prompt_bundle.diagnostics["sections"]
                        .get("knowledge_index", {})
                        .get("injected_chars", 0),
                    },
                    "committed": True,
                    "status": "completed",
                    **(
                        {
                            "stop_reason": task_plan_boundary.stop_reason,
                            "plan_id": task_plan_boundary.plan_id,
                            "task_plan_status": task_plan_boundary.status,
                            "task_plan_auto_accept": task_plan_boundary.auto_accept,
                            "awaiting_user_approval": (
                                task_plan_boundary.awaiting_user_approval
                            ),
                        }
                        if task_plan_boundary is not None
                        else {}
                    ),
                }
            )
            yield RunEvent(type="done", usage=usage_total, metadata=final_metadata)
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException as exc:
            if terminal_committer is not None and not round_state.finalized:
                if cancel_event is not None and cancel_event.is_set():
                    yield terminal_committer.commit_cancelled_round()
                else:
                    defer_failure_commit = bool(
                        request.get("_defer_failure_commit", False)
                    )
                    terminal_event = terminal_committer.commit_failed_round(
                        exc,
                        reason=(
                            "provider_context_recovery_failed"
                            if isinstance(exc, ContextLengthExceededError)
                            else "runtime_exception"
                        ),
                        persist=(
                            not defer_failure_commit
                            or _failure_requires_immediate_commit(exc)
                        ),
                    )
                    yield _committed_failure_event(
                        error_event(
                            exc,
                            phase=(
                                "provider"
                                if isinstance(exc, ContextLengthExceededError)
                                else "run"
                            ),
                        ),
                        terminal_event,
                    )
            else:
                yield error_event(exc, phase="run")
        finally:
            if (
                cancel_event is not None
                and cancel_event.is_set()
                and not round_state.finalized
                and terminal_committer is not None
            ):
                try:
                    terminal_committer.commit_cancelled_round()
                except Exception:
                    pass
            if round_state.history_run_registered:
                try:
                    update_run_state(
                        base,
                        user,
                        source,
                        session_id,
                        run_state=(
                            "running"
                            if request.get("_defer_failure_commit")
                            and not round_state.finalized
                            else "idle"
                        ),
                        run_id=run_id or None,
                    )
                except Exception:
                    pass
