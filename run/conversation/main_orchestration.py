"""Conversation orchestration implementation behind the stable main-loop facade."""
from __future__ import annotations

from typing import Any

def run_conversation(request, *, runtime_bindings, root=None, provider_factory=None, tool_registry_factory=None, cancel_event=None):
    (ContextLengthExceededError, ProviderLoopState, RoundRuntime, RoundState, RunDependencies, RunEvent, SUMMARY_STORE_REF, TerminalRoundCommitter, TerminalRoundContext, _build_request_context, _commit_verified_manual_compression, _committed_failure_event, _compress_per_round_tool_think, _content_display, _copy_committed_round_to_archive, _extract_round_memory, _failure_requires_immediate_commit, _memory_injected_chars, _merge_usage, _metric_provider_responses, _prepare_compression, _prepare_provider_request, _queue_summary_memory_extraction, _record_provider_request, _request_content_blocks, _required_text, _retry_recovery_messages, _retry_recovery_provider_responses, _run_provider_loop, _session_lock, _tool_result_reuse_allowed, _trim_to_max_rounds, _usage_from_dict, append_round_items, build_context_snapshot, build_summary_message, cleanup_run_registration, commit_terminal_windows, copy, datetime, error_event, memory_extraction_mode, patch_archive_metadata, project_root, queue_memory_extraction, restore_summary_cache, select_context, terminal_failure_events, time, timezone, tool_call_signature, update_run_state) = map(runtime_bindings.__getitem__, ('ContextLengthExceededError', 'ProviderLoopState', 'RoundRuntime', 'RoundState', 'RunDependencies', 'RunEvent', 'SUMMARY_STORE_REF', 'TerminalRoundCommitter', 'TerminalRoundContext', '_build_request_context', '_commit_verified_manual_compression', '_committed_failure_event', '_compress_per_round_tool_think', '_content_display', '_copy_committed_round_to_archive', '_extract_round_memory', '_failure_requires_immediate_commit', '_memory_injected_chars', '_merge_usage', '_metric_provider_responses', '_prepare_compression', '_prepare_provider_request', '_queue_summary_memory_extraction', '_record_provider_request', '_request_content_blocks', '_required_text', '_retry_recovery_messages', '_retry_recovery_provider_responses', '_run_provider_loop', '_session_lock', '_tool_result_reuse_allowed', '_trim_to_max_rounds', '_usage_from_dict', 'append_round_items', 'build_context_snapshot', 'build_summary_message', 'cleanup_run_registration', 'commit_terminal_windows', 'copy', 'datetime', 'error_event', 'memory_extraction_mode', 'patch_archive_metadata', 'project_root', 'queue_memory_extraction', 'restore_summary_cache', 'select_context', 'terminal_failure_events', 'time', 'timezone', 'tool_call_signature', 'update_run_state'))
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
        (base, user, source, session_id, run_id, prompt, content_blocks, identity) = map(request_context.__getattribute__, ('base', 'user', 'source', 'session_id', 'run_id', 'prompt', 'content_blocks', 'identity'))
        uploaded_file_context = ""
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
            raise
        yield error_event(exc, phase="request")
        return

    with _session_lock(identity.root, identity.user, identity.source, identity.session_id):
        terminal_committer = None
        try:
            from run.history import ensure_session_appendable

            ensure_session_appendable(base, user, source, session_id)
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
            (config, context_policy, source_policy, runtime_provider, provider, uploaded_descriptors, history_attachments, provider_media, direct_asset_ids, vision_route, uploaded_file_context, durable_user_content_blocks, agent_runner, window_path, archive_window) = map(prepared_request.__getattribute__, ('config', 'context_policy', 'source_policy', 'runtime_provider', 'provider', 'uploaded_descriptors', 'history_attachments', 'provider_media', 'direct_asset_ids', 'vision_route', 'uploaded_file_context', 'durable_user_content_blocks', 'agent_runner', 'window_path', 'archive_window'))
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
            (runtime_path, window, registry, tool_schemas, tool_timeout, agent_timeout, max_tool_calls, max_provider_iterations, identical_call_limit, invalid_tool_arguments_retry_limit, failure_limit, failures, identical_calls, memory_store, prompt_bundle, system_message, compress_only, queue_compression_memory, provider_content_blocks, current_user_message, force_compress, persisted_summary_cache) = map(compression.__getattribute__, ('runtime_path', 'window', 'registry', 'tool_schemas', 'tool_timeout', 'agent_timeout', 'max_tool_calls', 'max_provider_iterations', 'identical_call_limit', 'invalid_tool_arguments_retry_limit', 'failure_limit', 'failures', 'identical_calls', 'memory_store', 'prompt_bundle', 'system_message', 'compress_only', 'queue_compression_memory', 'provider_content_blocks', 'current_user_message', 'force_compress', 'persisted_summary_cache'))
            persisted_summary_message = build_summary_message(
                persisted_summary_cache
            )
            context_selection = compression.context_selection
            (all_text, all_reasoning, observed_text, observed_reasoning, tool_records, pending_tool_calls, consumed_guidance, consumed_guidance_details, provider_responses, durable_provider_responses, usage_total) = map(round_state.__getattribute__, ('all_text', 'all_reasoning', 'observed_text', 'observed_reasoning', 'tool_records', 'pending_tool_calls', 'consumed_guidance', 'consumed_guidance_details', 'provider_responses', 'durable_provider_responses', 'usage_total'))
            (context_stats, summary_usage, compression_memory, compression_usage, compression_notice_active, projected_current_rounds, compression_trigger, subagent_events, summary_cache, summary_diagnostics) = map(compression.__getattribute__, ('context_stats', 'summary_usage', 'compression_memory', 'compression_usage', 'compression_notice_active', 'projected_current_rounds', 'compression_trigger', 'subagent_events', 'summary_cache', 'summary_diagnostics'))
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
                effective_error = error
                if request.get("_retry_final_attempt"):
                    retry_attempt = int(request.get("_retry_attempt") or 1)
                    retry_max_attempts = int(
                        request.get("_retry_max_attempts") or retry_attempt
                    )
                    if isinstance(error, BaseException):
                        setattr(error, "retry_exhausted", True)
                        setattr(error, "retry_budget_exhausted", True)
                        setattr(error, "retry_attempts", retry_attempt)
                        setattr(error, "retry_max_attempts", retry_max_attempts)
                        setattr(error, "retryable_declared", True)
                        setattr(error, "retryable", False)
                    elif isinstance(error, dict):
                        effective_error = {
                            **error,
                            "retry_exhausted": True,
                            "retry_budget_exhausted": True,
                            "retry_attempts": retry_attempt,
                            "retry_max_attempts": retry_max_attempts,
                            "retryable": False,
                        }
                return terminal_committer.commit_failed_round(
                    effective_error,
                    reason=reason,
                    persist=(
                        not defer_failure_commit
                        or _failure_requires_immediate_commit(effective_error)
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

            provider_loop_state = ProviderLoopState(values=locals(), dependencies={**runtime_bindings, **locals()})
            for provider_event in _run_provider_loop(provider_loop_state):
                yield provider_event
            provider_values = provider_loop_state.values
            prompt_bundle = provider_values.get("prompt_bundle", prompt_bundle)
            system_message = provider_values.get("system_message", system_message)
            all_text = provider_values.get("all_text", all_text)
            all_reasoning = provider_values.get("all_reasoning", all_reasoning)
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
                    "committed_at": datetime.now(timezone.utc).isoformat(),
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
                    from run.memory import memory_round_payload

                    memory_payload = memory_round_payload(archive_window, archive_round_number)
                    memory_extraction = _extract_round_memory(
                        root=base,
                        user=user,
                        config=config,
                        round_number=archive_round_number,
                        **memory_payload,
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
            persisted_memory = memory_extraction.get("persisted") or {}
            memory_weighted_files = ([] if persisted_memory.get("replayed") else
                                     list(persisted_memory.get("weighted") or []))
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
            yield from terminal_failure_events(
                exc,
                terminal_committer=terminal_committer,
                round_state=round_state,
                cancel_event=cancel_event,
                request=request,
                context_length_error_type=ContextLengthExceededError,
                failure_requires_immediate_commit=_failure_requires_immediate_commit,
                committed_failure_event=_committed_failure_event,
                error_event=error_event,
            )
        finally:
            cleanup_run_registration(
                terminal_committer=terminal_committer,
                round_state=round_state,
                cancel_event=cancel_event,
                request=request,
                update_run_state=update_run_state,
                base=base,
                user=user,
                source=source,
                session_id=session_id,
                run_id=run_id,
            )


