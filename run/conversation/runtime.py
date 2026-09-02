"""Conversation runtime: request orchestration, tools, persistence, and streaming."""

from __future__ import annotations

import asyncio
import copy
import json
import queue
import threading
import time
import uuid
from contextlib import nullcontext
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
from run.agents import AgentRunner
from run.extensions import (
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
from run.context import (
    SUMMARY_CHUNK_TOKEN_BUDGET,
    SUMMARY_MAX_OUTPUT_TOKENS,
    SUMMARY_STORE_REF,
    build_summary_message,
    get_or_create_summary,
    read_summary_cache,
    restore_summary_cache,
)
from run.context import compress_per_round_tool_think as _compress_per_round_tool_think
from run.infra import ContextLengthExceededError, EngineError
from run.history import (
    _trim_to_max_rounds,
    append_round_items,
    commit_terminal_windows,
    commit_window,
    load_window,
    load_runtime_window,
    prepare_window,
    patch_archive_metadata,
    queue_memory_extraction,
)
from run.history import update_run_state
from run.conversation.guidance import GuidanceInput, normalize_guidance
from run.conversation.guidance_runtime import prepare_guidance
from run.memory import (
    MemoryStore,
    memory_extraction_mode,
)
from run.memory import (
    extract_memory_backlog as _extract_memory_backlog,
    extract_round_memory as _extract_round_memory,
)
from run.extensions import resolve_reasoning_selection
from run.extensions import main_model_supports_input, select_vision_route
from run.config import (
    PromptBundle,
    build_prompt_bundle,
    refresh_dynamic_prompt_bundle,
)
from run.conversation.provider_events import (
    is_context_length_exceeded as _is_context_length_exceeded,
    metric_provider_response_payload as _metric_provider_response_payload,
    provider_events as _provider_events,
    raise_if_context_length_exceeded as _raise_if_context_length_exceeded,
)
from run.tools import (
    invalid_tool_name as _invalid_tool_name,
    is_invalid_tool_arguments_error as _is_invalid_tool_arguments_error,
    messages_with_tool_argument_repair as _messages_with_tool_argument_repair,
    validate_tool_call_batch as _validate_tool_call_batch,
)
from run.conversation.request_input import (
    content_display as _content_display,
    content_for_message as _content_for_message,
    request_content_blocks as _request_content_blocks,
    required_text as _required_text,
    uploaded_file_context as _uploaded_file_context,
)
from run.conversation.round_finalizer import (
    TerminalRoundCommitter,
    TerminalRoundContext,
    queue_summary_memory_extraction as _queue_summary_memory_extraction,
)
from run.conversation.run_state import RoundState, RunDependencies, RunIdentity
from run.conversation.session_runtime import (
    copy_committed_round_to_archive as _copy_committed_round_to_archive,
    session_lock as _session_lock,
)
from run.config import MainAgentSourcePolicy
from run.tasks import (
    TaskPlanCreationBoundary,
    detect_task_plan_creation_boundary,
)
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
from run.conversation.usage import (
    merge_usage as _merge_usage,
    new_usage_total as _usage_total,
    record_provider_request as _record_provider_request,
    usage_from_dict as _usage_from_dict,
)
from run.conversation.helpers import (
    _assistant_tool_message,
    _auto_retry_attempt_limit,
    _close_guidance,
    _collect_retry_recovery,
    _committed_failure_event,
    _drain_guidance,
    _drain_or_close_guidance,
    _ensure_fixed_content_fits,
    _event_provider_response,
    _failure_requires_immediate_commit,
    _guidance_detail_key,
    _history_messages,
    _json_result,
    _memory_injected_chars,
    _metric_provider_responses,
    _remember_retry_guidance,
    _replace_primary_system_message,
    _response_reasoning_item,
    _retry_error_is_eligible,
    _retry_reason,
    _retry_recovery_messages,
    _retry_recovery_provider_responses,
    _retry_recovery_tool_records,
    _retrying_event,
    _tool_context_diagnostics,
    _tool_error_payload,
    _tool_failure_is_retryable,
    _tool_result_reuse_allowed,
    _tool_schema_map,
)
from run.conversation.main_loop import iter_request_events_impl as _main_loop_impl



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



def _iter_request_events_impl(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    """Compatibility wrapper for the extracted conversation loop."""

    yield from _main_loop_impl(
        request,
        root=root,
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=cancel_event,
    )

def _iter_request_events_unlocked(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    """Run bounded automatic retries; the caller owns session serialization."""

    run_id = str(request.get("run_id") or "")
    auto_retry = request.get("_auto_retry", True) is not False
    max_attempts = _auto_retry_attempt_limit(request) if auto_retry else 1
    recovery: dict[str, dict[str, Any]] = {}
    retry_state: dict[str, Any] = {"guidance": []}
    retry_usage: dict[str, Any] = {}
    run_sequence = 0

    def publish(event: RunEvent) -> RunEvent:
        nonlocal run_sequence
        event.event_id = event.event_id or f"run_evt_{uuid.uuid4().hex}"
        event.run_sequence = run_sequence
        if event.sequence is None:
            event.sequence = run_sequence
        if run_id:
            event.metadata.setdefault("run_id", run_id)
        run_sequence += 1
        return event

    def retry_delay_seconds(attempt: int, event: RunEvent) -> float:
        candidates: list[Any] = []
        error = event.error if isinstance(event.error, dict) else {}
        candidates.append(error.get("retry_after_ms"))
        details = error.get("details")
        if isinstance(details, dict):
            candidates.append(details.get("retry_after_ms"))
        candidates.append((event.metadata or {}).get("retry_after_ms"))
        for raw in candidates:
            try:
                milliseconds = int(raw)
            except (TypeError, ValueError):
                continue
            if milliseconds >= 0:
                return min(120.0, max(0.25, milliseconds / 1000.0))
        return min(2.0, 0.25 * (2 ** (attempt - 1)))

    def wait_before_retry(attempt: int, event: RunEvent) -> bool:
        delay = retry_delay_seconds(attempt, event)
        if cancel_event is not None:
            return cancel_event.wait(delay)
        time.sleep(delay)
        return False

    def emit_cancelled_attempt() -> Iterator[RunEvent]:
        cancelled_request = dict(request)
        cancelled_request["_auto_retry"] = False
        cancelled_request["_defer_failure_commit"] = False
        cancelled_request["_retry_recovery"] = [
            copy.deepcopy(value) for value in recovery.values()
        ]
        cancelled_request["_retry_state"] = retry_state
        cancelled_request["_retry_guidance"] = [
            copy.deepcopy(value)
            for value in retry_state.get("guidance", [])
            if isinstance(value, dict)
        ]
        if retry_usage:
            cancelled_request["_retry_usage_base"] = copy.deepcopy(retry_usage)
        yield from _iter_request_events_impl(
            cancelled_request,
            root=root,
            provider_factory=provider_factory,
            tool_registry_factory=tool_registry_factory,
            cancel_event=cancel_event,
        )

    for attempt in range(1, max_attempts + 1):
        attempt_request = dict(request)
        if auto_retry:
            attempt_request["_defer_failure_commit"] = attempt < max_attempts
            attempt_request["_retry_attempt"] = attempt
            attempt_request["_retry_recovery"] = [
                copy.deepcopy(value) for value in recovery.values()
            ]
            attempt_request["_retry_state"] = retry_state
            attempt_request["_retry_guidance"] = [
                copy.deepcopy(value)
                for value in retry_state.get("guidance", [])
                if isinstance(value, dict)
            ]
            if retry_usage:
                attempt_request["_retry_usage_base"] = copy.deepcopy(retry_usage)
        terminal_seen = False
        retry_scheduled = False
        for event in _iter_request_events_impl(
            attempt_request,
            root=root,
            provider_factory=provider_factory,
            tool_registry_factory=tool_registry_factory,
            cancel_event=cancel_event,
        ):
            _collect_retry_recovery(recovery, event)
            if event.type in {"done", "error"}:
                raw_usage = event.usage
                if not isinstance(raw_usage, dict):
                    raw_usage = event.metadata.get("usage")
                if isinstance(raw_usage, dict):
                    retry_usage = copy.deepcopy(raw_usage)
            if event.type == "error" and _retry_error_is_eligible(
                event, cancel_event=cancel_event
            ):
                if attempt < max_attempts:
                    event.metadata = {
                        **event.metadata,
                        "committed": False,
                        "retryable": event.metadata.get("retryable", True),
                        "failed_attempt": attempt,
                        "max_attempts": max_attempts,
                    }
                    yield publish(
                        _retrying_event(
                            event,
                            run_id=run_id,
                            failed_attempt=attempt,
                            next_attempt=attempt + 1,
                            max_attempts=max_attempts,
                        )
                    )
                    retry_scheduled = True
                    break
                event.metadata = {
                    **event.metadata,
                    "retry_attempts": attempt,
                    "max_attempts": max_attempts,
                }
                yield publish(event)
                return
            if event.type in {"done", "error"}:
                terminal_seen = True
                event.metadata = {
                    **event.metadata,
                    "retry_attempts": attempt,
                    "max_attempts": max_attempts,
                }
            yield publish(event)
            if event.type in {"done", "error"}:
                return
        if retry_scheduled:
            if wait_before_retry(attempt, event):
                yield from (publish(item) for item in emit_cancelled_attempt())
                return
            continue
        if terminal_seen:
            return
        # A provider/adapter can terminate its iterator without a terminal
        # event. Treat that as a retryable transport failure rather than
        # silently ending the SSE stream.
        missing = RunEvent(
            type="error",
            error={
                "message": "响应流在终态事件到达前结束",
                "exception_type": "MissingTerminalEvent",
                "phase": "run",
            },
        )
        if attempt < max_attempts:
            missing.metadata = {
                "committed": False,
                "retryable": True,
                "failed_attempt": attempt,
                "max_attempts": max_attempts,
            }
            yield publish(
                _retrying_event(
                    missing,
                    run_id=run_id,
                    failed_attempt=attempt,
                    next_attempt=attempt + 1,
                    max_attempts=max_attempts,
                )
            )
            if wait_before_retry(attempt, missing):
                yield from (publish(item) for item in emit_cancelled_attempt())
                return
            continue
        yield publish(missing)
        return


def _request_session_lock(
    request: dict[str, Any],
    root: Path | None,
):
    try:
        user = _required_text(request, "user")
        source = _required_text(request, "source")
        session_id = _required_text(request, "session_id")
        base = (root or project_root()).resolve()
    except Exception:
        return nullcontext()
    return _session_lock(base, user, source, session_id)


def iter_request_events(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    """Serialize one logical request, including all automatic retry attempts."""

    with _request_session_lock(request, root):
        yield from _iter_request_events_unlocked(
            request,
            root=root,
            provider_factory=provider_factory,
            tool_registry_factory=tool_registry_factory,
            cancel_event=cancel_event,
        )


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
        if event.type == "error" and not (
            event.metadata.get("retryable") is True
            and event.metadata.get("committed") is False
        ):
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
