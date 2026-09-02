"""Conversation context loading and compression preparation stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from run.conversation.runtime_state import RoundRuntime


@dataclass(slots=True)
class CompressionPreparation:
    runtime_path: Path
    window: dict[str, Any]
    registry: Any
    tool_schemas: Any
    tool_timeout: float
    agent_timeout: Any
    max_tool_calls: int
    max_provider_iterations: int
    identical_call_limit: int
    invalid_tool_arguments_retry_limit: int
    failure_limit: int
    failures: Any
    identical_calls: Any
    memory_store: Any
    prompt_bundle: Any
    system_message: dict[str, Any] | None
    compress_only: bool
    queue_compression_memory: bool
    provider_content_blocks: list[Any]
    current_user_message: dict[str, Any] | None
    force_compress: bool
    persisted_summary_cache: dict[str, Any] | None
    context_selection: Any
    context_stats: dict[str, Any]
    summary_usage: dict[str, Any]
    compression_memory: Any
    compression_usage: dict[str, Any]
    compression_notice_active: bool
    projected_current_rounds: int
    compression_trigger: str
    subagent_events: list[Any]
    summary_cache: dict[str, Any] | None
    summary_diagnostics: dict[str, Any]
    events: list[Any] = field(default_factory=list)


def prepare_compression(rt: RoundRuntime, *, cancel_event: Any) -> CompressionPreparation:
    import importlib
    _runtime = importlib.import_module("run.conversation.runtime")
    Any = _runtime.Any
    EngineError = _runtime.EngineError
    MemoryStore = _runtime.MemoryStore
    RunEvent = _runtime.RunEvent
    ConsecutiveIdenticalToolCallTracker = _runtime.ConsecutiveIdenticalToolCallTracker
    ConsecutiveToolFailureTracker = _runtime.ConsecutiveToolFailureTracker
    SUMMARY_CHUNK_TOKEN_BUDGET = _runtime.SUMMARY_CHUNK_TOKEN_BUDGET
    SUMMARY_MAX_OUTPUT_TOKENS = _runtime.SUMMARY_MAX_OUTPUT_TOKENS
    SUMMARY_STORE_REF = _runtime.SUMMARY_STORE_REF
    ToolRegistry = _runtime.ToolRegistry
    _ensure_fixed_content_fits = _runtime._ensure_fixed_content_fits
    _content_for_message = _runtime._content_for_message
    _extract_memory_backlog = _runtime._extract_memory_backlog
    _record_provider_request = _runtime._record_provider_request
    _trim_to_max_rounds = _runtime._trim_to_max_rounds
    _usage_from_dict = _runtime._usage_from_dict
    _usage_total = _runtime._usage_total
    apply_runtime_tool_policy = _runtime.apply_runtime_tool_policy
    build_context_snapshot = _runtime.build_context_snapshot
    build_prompt_bundle = _runtime.build_prompt_bundle
    build_summary_message = _runtime.build_summary_message
    copy = _runtime.copy
    estimate_messages_tokens = _runtime.estimate_messages_tokens
    estimate_tools_tokens = _runtime.estimate_tools_tokens
    get_or_create_summary = _runtime.get_or_create_summary
    load_runtime_window = _runtime.load_runtime_window
    memory_extraction_mode = _runtime.memory_extraction_mode
    queue_memory_extraction = _runtime.queue_memory_extraction
    read_summary_cache = _runtime.read_summary_cache
    restore_summary_cache = _runtime.restore_summary_cache
    select_context = _runtime.select_context

    base = rt.identity.root
    user = rt.identity.user
    source = rt.identity.source
    session_id = rt.identity.session_id
    run_id = rt.identity.run_id
    request = rt.request
    dependencies = rt.dependencies
    round_state = rt.round_state
    config = rt.config or {}
    context_policy = rt.context_policy
    provider = rt.provider
    runtime_provider = rt.runtime_provider
    window_path = rt.window_path
    archive_window = rt.archive_window
    content_blocks = rt.content_blocks
    durable_user_content_blocks = rt.durable_user_content_blocks
    provider_media = rt.provider_media
    agent_runner = rt.agent_runner
    events: list[Any] = []

    runtime_path, window = load_runtime_window(
        window_path,
        archive_window,
        max_rounds=context_policy.max_rounds,
    )
    tool_config = config.get("tools") or {}
    tools_enabled = bool(tool_config.get("enabled", True))
    registry = (
        apply_runtime_tool_policy(
            dependencies.tool_registry_factory(base, user), config
        )
        if tools_enabled
        else ToolRegistry({})
    )
    tool_schemas = registry.schemas() or None
    tool_timeout = float(tool_config.get("timeout", 240))
    agent_timeout = (config.get("agent_runtime") or {}).get(
        "default_timeout", 600
    )
    raw_max_tool_calls = tool_config.get("max_iterations", 80)
    if (
        isinstance(raw_max_tool_calls, bool)
        or not isinstance(raw_max_tool_calls, int)
        or raw_max_tool_calls < 1
    ):
        raise EngineError("tools.max_iterations 必须是正整数")
    max_tool_calls = raw_max_tool_calls
    # A run that executes N tool calls needs at most N tool-producing
    # Provider turns plus one final answer turn. This is only an internal
    # safety bound; tools.max_iterations itself counts tool calls.
    max_provider_iterations = max_tool_calls + 1
    raw_identical_call_limit = tool_config.get(
        "consecutive_identical_call_limit", 8
    )
    if (
        isinstance(raw_identical_call_limit, bool)
        or not isinstance(raw_identical_call_limit, int)
        or raw_identical_call_limit < 1
    ):
        raise EngineError(
            "tools.consecutive_identical_call_limit 必须是正整数"
        )
    identical_call_limit = raw_identical_call_limit
    raw_invalid_tool_arguments_retries = tool_config.get(
        "invalid_tool_arguments_retries", 2
    )
    if (
        isinstance(raw_invalid_tool_arguments_retries, bool)
        or not isinstance(raw_invalid_tool_arguments_retries, int)
        or raw_invalid_tool_arguments_retries < 0
    ):
        raise EngineError(
            "tools.invalid_tool_arguments_retries 必须是大于等于 0 的整数"
        )
    invalid_tool_arguments_retry_limit = raw_invalid_tool_arguments_retries
    raw_failure_limit = (config.get("history") or {}).get(
        "consecutive_tool_fail_limit", 5
    )
    if (
        isinstance(raw_failure_limit, bool)
        or not isinstance(raw_failure_limit, int)
        or raw_failure_limit < 1
    ):
        raise EngineError("history.consecutive_tool_fail_limit 必须是正整数")
    failure_limit = raw_failure_limit
    failures = ConsecutiveToolFailureTracker(failure_limit)
    identical_calls = ConsecutiveIdenticalToolCallTracker(
        identical_call_limit
    )

    memory_store = MemoryStore(base, user, config)
    prompt_bundle = build_prompt_bundle(
        base,
        user,
        config,
        plugin_manifests=registry.plugin_manifests,
        memory_store=memory_store,
        source=source,
        session_id=session_id,
    )
    system_message = (
        {"role": "system", "content": prompt_bundle.text}
        if prompt_bundle.text
        else None
    )
    compress_only = bool(request.get("compress_only", False))
    memory_extraction_policy = str(
        request.get("memory_extraction_policy")
        or ("sync" if compress_only else "queue")
    ).strip().casefold()
    if memory_extraction_policy not in {"sync", "queue"}:
        raise EngineError(
            "memory_extraction_policy 必须是 sync 或 queue"
        )
    queue_compression_memory = memory_extraction_policy == "queue"
    provider_content_blocks = list(durable_user_content_blocks)
    provider_content_blocks.extend(provider_media)
    current_user_message = (
        None
        if compress_only
        else {"role": "user", "content": _content_for_message(provider_content_blocks)}
    )
    force_compress = bool(request.get("compress", False) or compress_only)
    persisted_summary_cache = read_summary_cache(runtime_path)
    persisted_summary_message = build_summary_message(persisted_summary_cache)
    context_selection = select_context(
        window=window,
        policy=context_policy,
        system_message=system_message,
        summary_message=persisted_summary_message,
        current_user_message=current_user_message,
        tools=tool_schemas,
        force_compress=force_compress,
    )
    _ensure_fixed_content_fits(context_selection, system_message=system_message)
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
    context_stats = context_selection.stats()
    round_state.context_stats = context_stats
    summary_usage = _usage_total()
    compression_memory: dict[str, Any] | None = None
    compression_usage = _usage_total()
    compression_notice_active = bool(context_selection.removed_rounds)
    projected_current_rounds = 0 if compress_only else 1
    compression_trigger = (
        "token_limit"
        if context_selection.token_limit_triggered
        else ("manual" if force_compress else "round_limit")
    )
    if compression_notice_active:
        events.append(RunEvent(
            type="context_compression",
            content="正在压缩对话上下文",
            metadata={
                "status": "started",
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
            },
        ))
    if (
        force_compress or context_selection.removed_rounds
    ) and not queue_compression_memory:
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
    subagent_events: list[RunEvent] = []
    summary_cache = persisted_summary_cache
    summary_diagnostics: dict[str, Any] = {
        "cache_hit": False,
        "generated": False,
        "failed": False,
        "covered_rounds": [],
    }
    # 摘要也消耗输入标记。重新选择，直至移除的整轮稳定，
    # 因此没有一轮被摘要取代却又未包含在摘要中。
    max_summary_passes = len(context_selection.all_rounds) + 1
    for _ in range(max_summary_passes):
        removed_before = [item.number for item in context_selection.removed_rounds]
        if not removed_before:
            break
        summary_agent = "context_manage"
        summary_trigger = (
            "token_limit"
            if context_selection.token_limit_triggered
            else ("manual" if force_compress else "round_limit")
        )
        summary_cache, summary_diagnostics = get_or_create_summary(
            runtime_path=runtime_path,
            groups=context_selection.removed_rounds,
            agent_runner=agent_runner,
            agent_name=summary_agent,
            trigger=summary_trigger,
            cancel_event=cancel_event,
            chunk_token_budget=min(
                SUMMARY_CHUNK_TOKEN_BUDGET,
                max(256, context_policy.input_budget // 2),
            ),
            max_tokens=min(
                SUMMARY_MAX_OUTPUT_TOKENS,
                max(256, context_policy.output_reserve),
            ),
            response_hook=lambda raw: _record_provider_request(
                summary_usage, _usage_from_dict(raw)
            ),
            event_callback=subagent_events.append,
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
        next_selection = select_context(
            window=window,
            policy=context_policy,
            system_message=system_message,
            summary_message=build_summary_message(summary_cache),
            current_user_message=current_user_message,
            tools=tool_schemas,
            force_compress=force_compress,
        )
        removed_after = [item.number for item in next_selection.removed_rounds]
        context_selection = next_selection
        _ensure_fixed_content_fits(context_selection, system_message=system_message)
        if removed_after == removed_before:
            break

    # A later incremental pass can fail after an earlier pass wrote a
    # newer cache. Never leave that partial cache paired with the old
    # runtime workspace; the next request would otherwise read a
    # summary for rounds that were never removed.
    if bool(summary_diagnostics.get("failed")):
        restore_summary_cache(runtime_path, persisted_summary_cache)
        summary_cache = persisted_summary_cache


    rt.runtime_path = runtime_path
    rt.window = window
    rt.config = config
    rt.context_policy = context_policy
    rt.source_policy = getattr(rt, "source_policy", None)
    rt.runtime_provider = runtime_provider
    rt.provider = provider
    rt.window_path = window_path
    rt.archive_window = archive_window
    rt.uploaded_descriptors = rt.uploaded_descriptors
    rt.prompt_bundle = prompt_bundle
    rt.system_message = system_message
    rt.registry = registry
    rt.tool_schemas = tool_schemas
    rt.summary_cache = summary_cache
    return CompressionPreparation(
        runtime_path=runtime_path,
        window=window,
        registry=registry,
        tool_schemas=tool_schemas,
        tool_timeout=tool_timeout,
        agent_timeout=agent_timeout,
        max_tool_calls=max_tool_calls,
        max_provider_iterations=max_provider_iterations,
        identical_call_limit=identical_call_limit,
        invalid_tool_arguments_retry_limit=invalid_tool_arguments_retry_limit,
        failure_limit=failure_limit,
        failures=failures,
        identical_calls=identical_calls,
        memory_store=memory_store,
        prompt_bundle=prompt_bundle,
        system_message=system_message,
        compress_only=compress_only,
        queue_compression_memory=queue_compression_memory,
        provider_content_blocks=provider_content_blocks,
        current_user_message=current_user_message,
        force_compress=force_compress,
        persisted_summary_cache=persisted_summary_cache,
        context_selection=context_selection,
        context_stats=context_stats,
        summary_usage=summary_usage,
        compression_memory=compression_memory,
        compression_usage=compression_usage,
        compression_notice_active=compression_notice_active,
        projected_current_rounds=projected_current_rounds,
        compression_trigger=compression_trigger,
        subagent_events=subagent_events,
        summary_cache=summary_cache,
        summary_diagnostics=summary_diagnostics,
        events=events,
    )
