"""Conversation runtime: request orchestration, tools, persistence, and streaming."""

from __future__ import annotations

import asyncio
import copy
import json
import queue
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

from events import RunEvent, error_event
from provider.adapters.compat import chat_request_to_kemo
from provider.factory import (
    ProviderCongestionError,
    create_provider,
    provider_request_slot,
)
from provider.protocol.models import (
    AudioContent,
    FileContent,
    ImageContent,
    TextContent,
    VideoContent,
)
from provider.schema import ChatRequest, ProviderError, ToolCall, Usage
from run.agent_runner import AgentRunner
from run.attachments import (
    AttachmentError,
    RunAssetResolver,
    history_attachment_descriptors,
)
from run.config import load_config, project_root, provider_runtime_config
from run.context import (
    ContextPolicy,
    build_context_snapshot,
    estimate_messages_tokens,
    estimate_tools_tokens,
    select_context,
)
from run.context_summary import (
    SUMMARY_CHUNK_TOKEN_BUDGET,
    SUMMARY_MAX_OUTPUT_TOKENS,
    SUMMARY_STORE_REF,
    build_summary_message,
    get_or_create_summary,
    read_summary_cache,
    restore_summary_cache,
)
from run.context_service import compress_per_round_tool_think as _compress_per_round_tool_think
from run.errors import ContextLengthExceededError, EngineError
from run.history import (
    _trim_to_max_rounds,
    append_round_items,
    commit_window,
    load_window,
    load_runtime_window,
    prepare_window,
    queue_memory_extraction,
)
from run.history_index import set_active as set_active_history_session
from run.history_index import update_memory_state, update_run_state
from run.guidance import GuidanceInput, normalize_guidance
from run.guidance_runtime import prepare_guidance
from run.memory import (
    MemoryStore,
    memory_extraction_mode,
)
from run.memory_analysis import (
    extract_memory_backlog as _extract_memory_backlog,
    extract_round_memory as _extract_round_memory,
)
from run.model_capabilities import resolve_reasoning_selection
from run.multimodal import main_model_supports_input, select_vision_route
from run.prompt import PromptBundle, build_prompt_bundle
from run.provider_events import (
    is_context_length_exceeded as _is_context_length_exceeded,
    provider_events as _provider_events,
    raise_if_context_length_exceeded as _raise_if_context_length_exceeded,
)
from run.request_input import (
    content_display as _content_display,
    content_for_message as _content_for_message,
    request_content_blocks as _request_content_blocks,
    required_text as _required_text,
    uploaded_file_context as _uploaded_file_context,
)
from run.round_finalizer import (
    TerminalRoundCommitter,
    TerminalRoundContext,
    queue_summary_memory_extraction as _queue_summary_memory_extraction,
)
from run.run_state import RoundState, RunDependencies, RunIdentity
from run.session_runtime import (
    copy_committed_round_to_archive as _copy_committed_round_to_archive,
    session_lock as _session_lock,
)
from run.source_policy import MainAgentSourcePolicy
from run.tools import (
    ConsecutiveIdenticalToolCallTracker,
    ConsecutiveToolFailureTracker,
    ToolCancelledError,
    ToolResultTooLargeError,
    ToolRegistry,
    apply_runtime_tool_policy,
    discover_tools,
    execute_tool,
    tool_call_signature,
)
from run.usage import (
    merge_usage as _merge_usage,
    new_usage_total as _usage_total,
    record_provider_request as _record_provider_request,
    usage_from_dict as _usage_from_dict,
)


_EXPAND_CALL_LIVE_READ_COMMANDS = frozenset(
    {"configuration_status", "query", "refresh", "status"}
)
_FILE_LIVE_READ_ACTIONS = frozenset(
    {"exists", "hash", "list_dir", "read", "read_range", "search", "stat", "tree_dir"}
)


def _tool_result_reuse_allowed(name: str, arguments: dict[str, Any]) -> bool:
    """Return whether a successful result is stable for the rest of this run.

    Expand status and query results reflect external state that may change after an
    activate/sync/ingest call, or simply while an asynchronous server operation is
    still progressing. File reads likewise become stale after another tool or process
    mutates the filesystem. Replaying either kind of live read hides the current state.
    """

    if name == "expand_call":
        command = str(arguments.get("command") or "").strip().casefold()
        return command not in _EXPAND_CALL_LIVE_READ_COMMANDS
    if name == "file":
        action = str(arguments.get("action") or "").strip().casefold()
        return action not in _FILE_LIVE_READ_ACTIONS
    return True


def _drain_guidance(channel: Any) -> list[Any]:
    drain = getattr(channel, "drain", None)
    if callable(drain):
        return list(drain())
    if channel is None or not callable(getattr(channel, "get_nowait", None)):
        return []
    values: list[Any] = []
    while True:
        try:
            value = channel.get_nowait()
        except queue.Empty:
            break
        if normalize_guidance(value) is not None:
            values.append(value.strip() if isinstance(value, str) else value)
    return values


def _drain_or_close_guidance(channel: Any) -> list[Any]:
    drain_or_close = getattr(channel, "drain_or_close", None)
    if callable(drain_or_close):
        return list(drain_or_close())
    return _drain_guidance(channel)


def _close_guidance(channel: Any) -> None:
    close = getattr(channel, "close", None)
    if callable(close):
        close()


def _history_messages(window: dict[str, Any]) -> list[dict[str, Any]]:
    messages = window["text"].get("messages", [])
    return [
        dict(message)
        for message in messages
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
    ]


def _assistant_tool_message(
    text: str,
    calls: list[ToolCall],
    *,
    reasoning: str = "",
    native_reasoning: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in calls
        ],
    }
    if reasoning:
        message["reasoning_content"] = reasoning
    if native_reasoning:
        native_copy = copy.deepcopy(native_reasoning)
        # Provider 的 response item id 只在原响应内唯一；多轮工具循环中
        # 厂商可能重复使用 rs_1。进入新 KemoRequest 时换成本地唯一 id，
        # provider_state 本体仍保持不变。
        native_copy["id"] = f"rs_{uuid.uuid4().hex}"
        message["_kemo_reasoning"] = native_copy
    return message


def _response_reasoning_item(
    response: Any,
    *,
    streamed_content: str = "",
) -> dict[str, Any] | None:
    """取回本轮原生 reasoning item，供工具续轮完整回放。"""
    if not isinstance(response, dict):
        return None
    output = response.get("output")
    if not isinstance(output, list):
        return None
    for item in reversed(output):
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        reasoning = copy.deepcopy(item)
        if streamed_content and not reasoning.get("content"):
            reasoning["content"] = streamed_content
        return reasoning
    return None


def _json_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _tool_context_diagnostics(
    tool_records: list[dict[str, Any]],
    *,
    iteration: int,
) -> list[dict[str, Any]]:
    """Describe tool payload sizes without copying result bodies into errors."""

    diagnostics: list[dict[str, Any]] = []
    for record in tool_records:
        if not isinstance(record, dict) or record.get("iteration") != iteration:
            continue
        arguments = record.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        item: dict[str, Any] = {
            "name": str(record.get("name") or ""),
            "argument_chars": len(_json_result(arguments)),
            "result_chars": len(_json_result(record.get("result"))),
            "status": str(record.get("status") or ""),
        }
        action = arguments.get("action")
        if action is not None:
            item["action"] = str(action)
        path = arguments.get("path")
        if path is not None:
            item["path"] = str(path)
        diagnostics.append(item)
    return diagnostics


def _tool_error_payload(exc: BaseException) -> dict[str, Any]:
    """Preserve safe retry classification for the next model iteration."""

    if isinstance(exc, ToolResultTooLargeError):
        return exc.error_payload()
    detail: dict[str, Any] = {
        "message": str(exc),
        "exception_type": type(exc).__name__,
    }
    for field in (
        "category",
        "status_code",
        "retryable",
        "retry_after_ms",
        "attempt_count",
    ):
        value = getattr(exc, field, None)
        if isinstance(value, bool) or isinstance(value, (int, float)):
            detail[field] = value
        elif isinstance(value, str) and value.strip():
            detail[field] = value.strip()[:160]
    return detail


def _ensure_fixed_content_fits(
    selection: Any,
    *,
    system_message: dict[str, Any] | None,
) -> None:
    if not selection.fixed_content_over_budget:
        if not selection.recent_content_over_budget:
            return
        raise EngineError(
            "最近完整历史超过输入预算；请调大 agents.token_limit、调低 "
            "history.recent_full_rounds，或缩小 Prompt 注入内容"
        )
    system_tokens = estimate_messages_tokens([system_message]) if system_message else 0
    raise EngineError(
        "固定提示词和工具定义超过输入预算："
        f"system_prompt≈{system_tokens} tokens，"
        f"tool_schema≈{selection.tool_schema_tokens} tokens，"
        f"input_budget={selection.input_budget} tokens；"
        "请调小 memory.temporary_injection_limits 或 prompt.char_limits"
    )


def _commit_verified_manual_compression(
    *,
    runtime_path: Path,
    original_window: dict[str, Any],
    compacted_window: dict[str, Any],
    summary_cache: dict[str, Any],
    previous_summary_cache: dict[str, Any] | None,
    removed_round_numbers: list[int],
    previous_round_offset: int,
    expected_rounds: int,
    expected_round_offset: int,
) -> None:
    """Commit temp compaction and roll both artifacts back if verification fails."""

    try:
        commit_window(
            runtime_path,
            compacted_window,
            summary_cache=summary_cache,
        )
        stored = load_window(runtime_path)
        stored_data = stored.get("data") or {}
        stored_context = stored_data.get("context") or {}
        actual_rounds = int(stored_data.get("rounds") or 0)
        actual_workspace_rounds = int(
            stored_context.get("workspace_rounds") or 0
        )
        actual_offset = int(stored_context.get("round_offset") or 0)
        if (
            actual_rounds != expected_rounds
            or actual_workspace_rounds != expected_rounds
            or actual_offset != expected_round_offset
        ):
            raise EngineError(
                "上下文压缩落盘校验失败："
                f"期望 workspace={expected_rounds}、offset={expected_round_offset}，"
                f"实际 workspace={actual_rounds}/{actual_workspace_rounds}、"
                f"offset={actual_offset}"
            )
        stored_cache = read_summary_cache(runtime_path)
        if stored_cache is None:
            raise EngineError("上下文压缩落盘校验失败：摘要缓存不可读取")
        if stored_cache.get("source_hash") != summary_cache.get("source_hash"):
            raise EngineError("上下文压缩落盘校验失败：摘要缓存版本不一致")
        covered = stored_cache.get("covered_rounds")
        covered_rounds = {
            int(number)
            for number in covered
            if isinstance(number, int) or str(number).isdigit()
        } if isinstance(covered, list) else set()
        expected_covered = {
            max(0, previous_round_offset) + int(number)
            for number in removed_round_numbers
        }
        if not expected_covered.issubset(covered_rounds):
            raise EngineError("上下文压缩落盘校验失败：摘要未覆盖全部移除轮次")
        if stored_context.get("summary_cache") != SUMMARY_STORE_REF:
            raise EngineError("上下文压缩落盘校验失败：运行窗口未登记摘要缓存")
        snapshot = stored_data.get("context_snapshot") or {}
        if int(snapshot.get("workspace_rounds") or 0) != expected_rounds:
            raise EngineError("上下文压缩落盘校验失败：上下文快照仍指向旧工作区")
    except BaseException:
        rollback_errors: list[str] = []
        try:
            commit_window(
                runtime_path,
                original_window,
                summary_cache=previous_summary_cache,
            )
        except BaseException as exc:
            rollback_errors.append(f"运行窗口与摘要回滚失败：{exc}")
        if rollback_errors:
            raise EngineError("；".join(rollback_errors))
        raise


def _memory_injected_chars(bundle: PromptBundle) -> int:
    return sum(
        section.injected_chars
        for section in bundle.sections
        if section.name in {"permanent_memory", "important_memory"}
        or section.name.startswith("temporary_memory:")
    )


def _committed_failure_event(
    event: RunEvent,
    terminal_event: RunEvent,
) -> RunEvent:
    """Expose durable failure state without replacing the public error terminal."""

    event.metadata = {
        **event.metadata,
        "committed": True,
        "status": "failed",
        "stop_reason": terminal_event.metadata.get(
            "stop_reason", "provider_error"
        ),
        "failure": copy.deepcopy(terminal_event.metadata.get("failure") or {}),
    }
    return event


def _iter_request_events_impl(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    """Run one complete model/tool loop, committing only on successful done."""

    round_state = RoundState(run_started=time.monotonic())
    run_started = round_state.run_started
    dependencies = RunDependencies(
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=cancel_event,
    )
    try:
        user = _required_text(request, "user")
        compress_only_requested = bool(request.get("compress_only", False))
        content_blocks = (
            [] if compress_only_requested else _request_content_blocks(request)
        )
        has_uploaded_files = bool(request.get("uploaded_files"))
        if not content_blocks and not has_uploaded_files and not compress_only_requested:
            raise EngineError("请求必须包含非空 prompt、content[] 或 uploaded_files")
        prompt = _content_display(content_blocks)
        uploaded_file_context = ""
        source = _required_text(request, "source")
        session_id = _required_text(request, "session_id")
        run_id = str(request.get("run_id") or "")
        base = (root or project_root()).resolve()
        identity = RunIdentity(
            root=base,
            user=user,
            source=source,
            session_id=session_id,
            run_id=run_id,
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
            raise
        yield error_event(exc, phase="request")
        return

    with _session_lock(identity.root, identity.user, identity.source, identity.session_id):
        terminal_committer = None
        try:
            config = load_config(user, base)
            context_policy = ContextPolicy.from_config(config)
            source_policy = MainAgentSourcePolicy.from_config(config)
            runtime_provider = provider_runtime_config(config)
            provider = dependencies.provider_factory(runtime_provider)
            uploaded_descriptors = [
                dict(item)
                for item in (request.get("uploaded_files") or [])
                if isinstance(item, dict)
            ]
            history_attachments = history_attachment_descriptors(uploaded_descriptors)
            image_descriptors = [
                item
                for item in uploaded_descriptors
                if str(item.get("media_kind") or "") == "image"
                or bool(item.get("is_image"))
            ]
            inline_images = [
                item for item in content_blocks if isinstance(item, ImageContent)
            ]
            vision_route: str | None = None
            provider_media: list[ImageContent | AudioContent | VideoContent | FileContent] = []
            direct_asset_ids: set[str] = set()
            resolver = RunAssetResolver(base, user, uploaded_descriptors)
            if image_descriptors or inline_images:
                vision_route = select_vision_route(
                    config,
                    runtime_provider,
                    provider,
                    cancel_event=cancel_event,
                )
                if vision_route == "dedicated" and inline_images:
                    raise EngineError(
                        "主模型未声明图片输入能力，inline content 图片不能直接发送；"
                        "请将图片登记为运行资产，或把本地路径交给 multimodal 工具"
                    )
                if vision_route == "main":
                    try:
                        if str(runtime_provider.get("type") or "") == "chat":
                            provider_media.extend(
                                resolver.image_content(
                                    str(item["asset_id"]),
                                    provider="chat",
                                )
                                for item in image_descriptors
                            )
                            direct_asset_ids.update(
                                str(item["asset_id"]) for item in image_descriptors
                            )
                    except AttachmentError as exc:
                        raise EngineError(str(exc)) from None
            inline_media_kinds = {
                "audio"
                if isinstance(item, AudioContent)
                else "video"
                if isinstance(item, VideoContent)
                else "file"
                for item in content_blocks
                if isinstance(item, (AudioContent, VideoContent, FileContent))
            }
            for media_kind in sorted(inline_media_kinds):
                if not main_model_supports_input(
                    config,
                    runtime_provider,
                    provider,
                    media_kind,
                    cancel_event=cancel_event,
                ):
                    raise EngineError(
                        f"主模型未声明 {media_kind} 输入能力，不能直接接收 inline content；"
                        "请将媒体登记为运行资产后调用 multimodal 工具"
                    )
            if uploaded_descriptors and str(runtime_provider.get("type") or "") == "kemo":
                upload_asset = getattr(provider, "upload_asset", None)
                wait_asset_ready = getattr(provider, "wait_asset_ready", None)
                for item in uploaded_descriptors:
                    asset_id = str(item.get("asset_id") or "")
                    media_kind = str(item.get("media_kind") or "file")
                    should_direct = (
                        vision_route == "main"
                        if media_kind == "image"
                        else main_model_supports_input(
                            config,
                            runtime_provider,
                            provider,
                            media_kind,
                            cancel_event=cancel_event,
                        )
                    )
                    if not asset_id or not should_direct:
                        continue
                    if not callable(upload_asset) or not callable(wait_asset_ready):
                        raise EngineError("Kemo Provider 未实现完整多模态 Asset 客户端")
                    try:
                        path, verified = resolver.local_asset(
                            asset_id, expected_kind=media_kind
                        )
                        with provider_request_slot(config, cancel_event=cancel_event):
                            remote = upload_asset(
                                path,
                                metadata={
                                    "user": user,
                                    "session_id": session_id,
                                    "purpose": "input",
                                    "capability": "conversation",
                                },
                                idempotency_key=asset_id,
                                checksum_sha256=str(verified["checksum_sha256"]),
                                mime_type=str(verified["mime_type"]),
                                cancel_event=cancel_event,
                            )
                            remote = wait_asset_ready(
                                remote,
                                cancel_event=cancel_event,
                            )
                        provider_media.append(
                            resolver.remote_content(
                                asset_id,
                                remote_asset_id=str(remote.id),
                            )
                        )
                        direct_asset_ids.add(asset_id)
                    except (AttachmentError, ProviderError) as exc:
                        raise EngineError(str(exc)) from None
            uploaded_file_context = _uploaded_file_context(
                request,
                vision_route=vision_route,
                direct_asset_ids=direct_asset_ids,
            )
            durable_user_content_blocks = list(content_blocks)
            if uploaded_file_context:
                # Keep a stable attachment reference in history, but never persist
                # transient inline Base64 or remote Provider Asset blocks generated
                # for this request. This also guarantees that attachment-only rounds
                # remain valid MessageItems when the next round rebuilds context.
                durable_user_content_blocks.append(
                    TextContent(text=uploaded_file_context)
                )
            agent_runner = AgentRunner(
                base,
                user,
                config=config,
                provider_factory=dependencies.provider_factory,
            )
            window_path, archive_window, _ = prepare_window(base, user, source, session_id)
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
                    )
                    is not None
                )
            except Exception as exc:
                round_state.history_run_error = {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                }
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
            usage_total = round_state.usage_total
            context_stats = context_selection.stats()
            round_state.context_stats = context_stats
            summary_usage = _usage_total()
            compression_memory: dict[str, Any] | None = None
            compression_usage = _usage_total()
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
            commit_failed_round = terminal_committer.commit_failed_round

            if cancel_event is not None and cancel_event.is_set():
                yield commit_cancelled_round()
                return
            messages = context_selection.messages
            context_stats = context_selection.stats()
            context_stats["summary"] = summary_diagnostics
            context_stats["summary_usage"] = summary_usage
            round_state.context_stats = context_stats
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
            pending_guidance_ack: list[GuidanceInput] = []
            remote_guidance_assets: dict[str, str] = {}
            protocol_parent_request_id: str | None = None
            usage_total.clear()
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
            seen_calls: dict[str, dict[str, Any]] = {}
            final_metadata: dict[str, Any] = {}
            completed = False
            context_retry_count = 0
            last_provider_input_tokens: int | None = None
            last_sent_local_tokens: int | None = None

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
                pending_guidance_ack.extend(prepared.inputs)
                return prepared.messages

            for iteration in range(1, max_provider_iterations + 1):
                if cancel_event is not None and cancel_event.is_set():
                    yield commit_cancelled_round()
                    return
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
                while True:
                    request_local_tokens = estimate_messages_tokens(
                        messages
                    ) + estimate_tools_tokens(active_tool_schemas)
                    chat_request = ChatRequest(
                        model=runtime_provider["model"],
                        messages=messages,
                        stream=stream,
                        tools=active_tool_schemas,
                        max_tokens=request_max_tokens,
                        extra=dict(reasoning_extra),
                    )
                    protocol_request = chat_request_to_kemo(chat_request).model_copy(
                        update={
                            "request_id": f"req_{uuid.uuid4().hex}",
                            "parent_request_id": protocol_parent_request_id,
                            "attempt": context_retry_count + 1,
                            "metadata": {
                                "capability": "conversation",
                                "user": user,
                                "source": source,
                                "session_id": session_id,
                                "run_id": run_id,
                                "iteration": iteration,
                                "window": window_path.name,
                                "prompt_hash": prompt_bundle.diagnostics.get("hash"),
                            },
                        }
                    )
                    iteration_text: list[str] = []
                    iteration_reasoning: list[str] = []
                    calls: list[ToolCall] = []
                    iteration_done: RunEvent | None = None
                    iteration_usage: Usage | None = None
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
                                    observed_text.append(event.content)
                                    yield event
                                elif event.type == "reasoning_delta":
                                    iteration_reasoning.append(event.content)
                                    observed_reasoning.append(event.content)
                                    yield event
                                elif event.type == "tool_call_start":
                                    call = ToolCall(
                                        id=event.tool_call_id,
                                        name=event.tool_name,
                                        arguments=event.arguments or {},
                                    )
                                    calls.append(call)
                                    pending_tool_calls[call.id] = {
                                        "name": call.name,
                                        "arguments": copy.deepcopy(call.arguments),
                                        "iteration": iteration,
                                    }
                                    yield event
                                elif event.type == "usage":
                                    iteration_usage = _usage_from_dict(event.usage)
                                    yield RunEvent(
                                        type="usage",
                                        usage=event.usage,
                                        metadata={"iteration": iteration},
                                    )
                                elif event.type == "media_output":
                                    yield event
                                elif event.type == "error":
                                    _raise_if_context_length_exceeded(event.error)
                                    if iteration_usage is not None:
                                        _record_provider_request(
                                            usage_total, iteration_usage
                                        )
                                        iteration_usage = None
                                    terminal_event = commit_failed_round(
                                        event.error,
                                        reason="provider_error_event",
                                    )
                                    yield _committed_failure_event(
                                        event, terminal_event
                                    )
                                    return
                                elif event.type == "done":
                                    iteration_done = event
                        break
                    except ProviderCongestionError as exc:
                        if cancel_event is not None and cancel_event.is_set():
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
                            yield commit_cancelled_round()
                            return
                        context_length_error = _is_context_length_exceeded(exc)
                        if (
                            not context_length_error
                            or iteration != 1
                            or context_retry_count >= 2
                        ):
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
                        context_stats = context_selection.stats()
                        context_stats["summary"] = retry_diagnostics
                        context_stats["summary_usage"] = summary_usage
                        context_stats["api_context_retries"] = context_retry_count
                        for retry_event in retry_events:
                            yield retry_event

                if iteration_done is None:
                    exc = EngineError("Provider 事件流缺少 done 终态")
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
                if iteration_usage is None:
                    iteration_usage = _usage_from_dict(iteration_done.usage)
                if (
                    not iteration_usage.estimated
                    and iteration_usage.prompt_tokens > 0
                ):
                    last_provider_input_tokens = iteration_usage.prompt_tokens
                    last_sent_local_tokens = request_local_tokens
                all_text.extend(iteration_text)
                all_reasoning.extend(iteration_reasoning)
                _record_provider_request(usage_total, iteration_usage)
                final_metadata = dict(iteration_done.metadata)
                provider_response = final_metadata.get("provider_response")
                if isinstance(provider_response, dict):
                    provider_responses.append(copy.deepcopy(provider_response))
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
                for call in calls:
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
                        duplicate = reuse_allowed and signature in seen_calls
                        if duplicate:
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
                pending_guidance = _drain_guidance(guidance_channel)
                messages.extend(prepare_pending_guidance(pending_guidance))

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

            round_number = int(window["data"].get("rounds", 0)) + 1
            archive_round_number = int(
                archive_window["data"].get("rounds", 0)
            ) + 1
            round_elapsed_ms = max(0, round((time.monotonic() - run_started) * 1000))
            text = "".join(all_text)
            reasoning = "".join(all_reasoning)
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
                    },
                    {"role": "assistant", "content": text},
                ]
            )
            window["think"]["rounds"].append({"round": round_number, "content": reasoning})
            window["tool"]["rounds"].append({"round": round_number, "calls": tool_records})
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
                provider_responses=provider_responses,
                user_metadata={"input_attachments": history_attachments}
                if history_attachments
                else None,
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
                    "tool_calls": len(tool_records),
                    "guidance": list(consumed_guidance),
                    "guidance_details": copy.deepcopy(consumed_guidance_details),
                    "provider_responses": copy.deepcopy(provider_responses),
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
            commit_window(window_path, archive_window)
            commit_window(
                runtime_path,
                runtime_window,
                summary_cache=summary_cache,
            )
            round_state.finalized = True
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
            try:
                update_memory_state(
                    base,
                    user,
                    source,
                    session_id,
                    status=(
                        "queued"
                        if isinstance(compression_memory, dict)
                        and compression_memory.get("status") == "queued"
                        else initial_memory_status
                    ),
                )
            except Exception as exc:
                history_index_error = {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                }

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
                commit_window(window_path, archive_window)
            elif extraction_status == "failed":
                archive_data["memory_status"] = "failed"
                archive_data["memory_error"] = memory_error
                commit_window(window_path, archive_window)

            if extraction_status == "completed":
                try:
                    update_memory_state(
                        base,
                        user,
                        source,
                        session_id,
                        processed_round=archive_round_number,
                        status="completed",
                    )
                except Exception as exc:
                    history_index_error = history_index_error or {
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    }
            elif extraction_status == "failed":
                try:
                    update_memory_state(
                        base,
                        user,
                        source,
                        session_id,
                        status="failed",
                        error=memory_error,
                    )
                except Exception as exc:
                    history_index_error = history_index_error or {
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    }
            try:
                update_run_state(
                    base,
                    user,
                    source,
                    session_id,
                    run_state="idle",
                    run_id=run_id or None,
                    directory=window_path,
                )
                round_state.history_run_registered = False
            except Exception as exc:
                history_index_error = history_index_error or {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                }
            active_key = request.get("_history_active_key")
            if isinstance(active_key, str) and active_key.strip():
                try:
                    set_active_history_session(
                        base,
                        user,
                        active_key.strip(),
                        session_id,
                        source=source,
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
                }
            )
            yield RunEvent(type="done", usage=usage_total, metadata=final_metadata)
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException as exc:
            if (
                isinstance(exc, ContextLengthExceededError)
                and terminal_committer is not None
                and not round_state.finalized
            ):
                terminal_event = terminal_committer.commit_failed_round(
                    exc,
                    reason="provider_context_recovery_failed",
                )
                yield _committed_failure_event(
                    error_event(exc, phase="provider"), terminal_event
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
                        run_state="idle",
                        run_id=run_id or None,
                    )
                except Exception:
                    pass


def iter_request_events(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    """Expose a task-wide ordered event stream over per-Provider sequences."""

    run_id = str(request.get("run_id") or "")
    for run_sequence, event in enumerate(
        _iter_request_events_impl(
            request,
            root=root,
            provider_factory=provider_factory,
            tool_registry_factory=tool_registry_factory,
            cancel_event=cancel_event,
        )
    ):
        event.event_id = event.event_id or f"run_evt_{uuid.uuid4().hex}"
        event.run_sequence = run_sequence
        if event.sequence is None:
            event.sequence = run_sequence
        if run_id:
            event.metadata.setdefault("run_id", run_id)
        yield event


async def stream_request(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> AsyncIterator[RunEvent]:
    stopped = cancel_event or threading.Event()
    iterator = iter_request_events(
        request,
        root=root,
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=stopped,
    )
    try:
        while True:
            event = await asyncio.to_thread(next, iterator, None)
            if event is None:
                break
            yield event
    except asyncio.CancelledError:
        stopped.set()
        await asyncio.to_thread(iterator.close)
        raise


def handle_request(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    final: RunEvent | None = None
    for event in iter_request_events(
        request,
        root=root,
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=cancel_event,
    ):
        if event.type == "error":
            detail = event.error or {}
            raise EngineError(str(detail.get("message") or "运行失败"))
        if event.type == "done":
            final = event
    if final is None:
        raise EngineError("运行在完成前被取消")
    return dict(final.metadata)


def compress_context(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    payload = dict(request)
    payload["prompt"] = ""
    payload["compress_only"] = True
    payload["compress"] = True
    return handle_request(
        payload,
        root=root,
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=cancel_event,
    )
