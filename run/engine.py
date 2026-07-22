"""事件驱动的 kemo-agent 对话引擎。"""

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

from pydantic import TypeAdapter, ValidationError

from events import RunEvent, error_event
from provider.adapters.compat import chat_request_to_kemo
from provider.factory import (
    ProviderCongestionError,
    create_provider,
    provider_request_slot,
)
from provider.protocol.enums import ResponseStatus, StreamEventType
from provider.protocol.models import (
    ContentBlock,
    KemoRequest,
    KemoResponse,
    MessageItem,
    ReasoningItem,
    TextContent,
    ToolCallItem,
    Usage as ProtocolUsage,
)
from provider.protocol.streaming import ProviderStreamEvent
from provider.schema import ChatRequest, ProviderError, ToolCall, Usage
from run.agent_runner import AgentRunner
from run.config import load_config, project_root, provider_runtime_config
from run.context import (
    ContextPolicy,
    build_context_snapshot,
    estimate_messages_tokens,
    estimate_tools_tokens,
    select_context,
)
from run.context_summary import build_summary_message, get_or_create_summary
from run.history import (
    _trim_to_max_rounds,
    append_round_items,
    commit_window,
    find_window,
    load_window,
    load_runtime_window,
    prepare_window,
    runtime_window_path,
)
from run.history_index import set_active as set_active_history_session
from run.history_index import update_memory_state, update_run_state
from run.memory import MemoryStore, memory_extraction_mode
from run.memory_pipeline import memory_round_payload
from run.prompt import PromptBundle, build_prompt_bundle
from run.source_policy import MainAgentSourcePolicy
from run.tools import (
    ConsecutiveToolFailureTracker,
    ToolRegistry,
    apply_runtime_tool_policy,
    discover_tools,
    execute_tool,
)


class EngineError(RuntimeError):
    """The run core rejected or failed a conversation request."""


class ContextLengthExceededError(EngineError):
    """The Provider rejected the request because its context is too large."""


_SESSION_LOCKS: dict[tuple[str, str, str, str], threading.RLock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()
_CONTENT_LIST_ADAPTER = TypeAdapter(list[ContentBlock])


def _session_lock(root: Path, user: str, source: str, session_id: str) -> threading.RLock:
    key = (str(root.resolve()), user, source, session_id)
    with _SESSION_LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(key, threading.RLock())


def _required_text(request: dict[str, Any], name: str) -> str:
    value = request.get(name)
    if not isinstance(value, str) or not value.strip():
        raise EngineError(f"请求字段 {name!r} 必须是非空字符串")
    return value.strip()


def _request_content_blocks(request: dict[str, Any]) -> list[ContentBlock]:
    prompt_value = request.get("prompt", "")
    prompt = prompt_value.strip() if isinstance(prompt_value, str) else ""
    raw_content = request.get("content")
    if raw_content is None:
        raw_content = []
    if not isinstance(raw_content, list):
        raise EngineError("请求字段 'content' 必须是 Content Block 数组")
    combined: list[Any] = []
    if prompt:
        combined.append({"type": "text", "text": prompt})
    combined.extend(raw_content)
    if not combined:
        return []
    try:
        return _CONTENT_LIST_ADAPTER.validate_python(combined)
    except ValidationError as exc:
        raise EngineError(f"请求字段 'content' 无效：{exc.errors(include_url=False)}") from exc


def _content_for_message(blocks: list[ContentBlock]) -> str | list[dict[str, Any]]:
    if all(isinstance(block, TextContent) for block in blocks):
        return "".join(block.text for block in blocks if isinstance(block, TextContent))
    return [block.model_dump(mode="json", exclude_none=True) for block in blocks]


def _content_display(blocks: list[ContentBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextContent):
            parts.append(block.text)
        else:
            asset_id = getattr(block, "asset_id", None)
            parts.append(f"[{block.type}:{asset_id or 'inline'}]")
    return "\n".join(part for part in parts if part)


def _drain_guidance(channel: Any) -> list[str]:
    if channel is None or not callable(getattr(channel, "get_nowait", None)):
        return []
    values: list[str] = []
    while True:
        try:
            value = channel.get_nowait()
        except queue.Empty:
            break
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


def _append_guidance(messages: list[dict[str, Any]], values: list[str]) -> None:
    if values:
        messages.append(
            {
                "role": "user",
                "content": "[运行中引导]\n" + "\n".join(f"- {item}" for item in values),
            }
        )


def _usage_from_dict(value: dict[str, Any] | None) -> Usage:
    raw = value or {}
    prompt_value = raw.get("prompt_tokens")
    if prompt_value is None:
        prompt_value = raw.get("input_tokens")
    completion_value = raw.get("completion_tokens")
    if completion_value is None:
        completion_value = raw.get("output_tokens")
    prompt_tokens = max(0, int(prompt_value or 0))
    completion_tokens = max(0, int(completion_value or 0))
    total_value = raw.get("total_tokens")
    measurement = raw.get("measurement") if isinstance(raw.get("measurement"), dict) else {}
    mode = str(measurement.get("mode") or "")
    estimated = bool(
        raw.get("estimated", False)
        or mode in {"estimated", "mixed", "unknown"}
        or (measurement and not measurement.get("exact", False))
    )
    known = {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated",
        "source",
    }
    return Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=max(
            0,
            int(total_value if total_value is not None else prompt_tokens + completion_tokens),
        ),
        estimated=estimated,
        source=str(raw.get("source") or mode or "provider"),
        extra={key: item for key, item in raw.items() if key not in known},
    )


def _merge_usage(total: dict[str, Any], usage: Usage) -> None:
    total["prompt_tokens"] = int(total.get("prompt_tokens", 0)) + usage.prompt_tokens
    total["completion_tokens"] = int(total.get("completion_tokens", 0)) + usage.completion_tokens
    total["total_tokens"] = int(total.get("total_tokens", 0)) + usage.total_tokens
    total["estimated"] = bool(total.get("estimated", False) or usage.estimated)
    cached: int | None = None
    for key in (
        "cached_input_tokens",
        "cached_prompt_tokens",
        "cache_hit_tokens",
        "cached_tokens",
    ):
        value = usage.extra.get(key)
        if value is not None:
            cached = max(0, int(value))
            break
    details = usage.extra.get("prompt_tokens_details")
    if cached is None and isinstance(details, dict) and details.get("cached_tokens") is not None:
        cached = max(0, int(details["cached_tokens"]))
    missed_raw = usage.extra.get("cache_miss_tokens")
    missed = max(0, int(missed_raw)) if missed_raw is not None else None
    if cached is not None:
        missed = max(0, usage.prompt_tokens - cached) if missed is None else missed
        total["cached_prompt_tokens"] = int(total.get("cached_prompt_tokens", 0)) + cached
        total["cache_miss_tokens"] = int(total.get("cache_miss_tokens", 0)) + missed
        denominator = total["cached_prompt_tokens"] + total["cache_miss_tokens"]
        total["cache_hit_rate"] = (
            round(total["cached_prompt_tokens"] / denominator, 6) if denominator else 0.0
        )
        total["cached_input_tokens"] = total["cached_prompt_tokens"]
    reasoning = usage.extra.get("reasoning_tokens")
    if reasoning is not None:
        total["reasoning_tokens"] = int(total.get("reasoning_tokens", 0)) + max(
            0, int(reasoning)
        )
    visible = usage.extra.get("visible_output_tokens")
    if visible is not None:
        total["visible_output_tokens"] = int(
            total.get("visible_output_tokens", 0)
        ) + max(0, int(visible))
    stages = usage.extra.get("stages")
    if isinstance(stages, list):
        total.setdefault("stages", []).extend(copy.deepcopy(stages))
    media = usage.extra.get("media")
    if isinstance(media, dict):
        aggregate_media = total.setdefault("media", {})
        for key, value in media.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                aggregate_media[key] = aggregate_media.get(key, 0) + value
    measurement = usage.extra.get("measurement")
    if isinstance(measurement, dict):
        previous = total.get("measurement")
        if not isinstance(previous, dict) or previous.get("mode") == "unknown":
            total["measurement"] = copy.deepcopy(measurement)
        elif previous.get("mode") != measurement.get("mode"):
            total["measurement"] = {
                "mode": "mixed",
                "exact": False,
                "exact_fields": [],
                "estimated_fields": sorted(
                    {
                        *previous.get("estimated_fields", []),
                        *measurement.get("estimated_fields", []),
                    }
                ),
            }
    provider_raw = usage.extra.get("provider_raw")
    if isinstance(provider_raw, dict) and provider_raw:
        total.setdefault("provider_raw", []).append(copy.deepcopy(provider_raw))
    provider_request_count = usage.extra.get("provider_request_count")
    if provider_request_count is not None:
        total["provider_request_count"] = int(
            total.get("provider_request_count", 0)
        ) + max(0, int(provider_request_count))
    total["input_tokens"] = total["prompt_tokens"]
    total["output_tokens"] = total["completion_tokens"]


def _record_provider_request(total: dict[str, Any], usage: Usage) -> None:
    declared = usage.extra.get("provider_request_count")
    _merge_usage(total, usage)
    if declared is None or max(0, int(declared)) == 0:
        total["provider_request_count"] = int(
            total.get("provider_request_count", 0)
        ) + 1


def _usage_total() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "provider_request_count": 0,
        "estimated": False,
        "measurement": {"mode": "unknown", "exact": False},
        "stages": [],
        "media": {},
    }


def _history_messages(window: dict[str, Any]) -> list[dict[str, Any]]:
    messages = window["text"].get("messages", [])
    return [
        dict(message)
        for message in messages
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
    ]


def _protocol_usage_dict(usage: ProtocolUsage) -> dict[str, Any]:
    value = usage.model_dump(mode="json", exclude_none=True)
    value.update(
        {
            "prompt_tokens": int(usage.input_tokens or 0),
            "completion_tokens": int(usage.output_tokens or 0),
            "total_tokens": int(
                usage.total_tokens
                if usage.total_tokens is not None
                else (usage.input_tokens or 0) + (usage.output_tokens or 0)
            ),
            "estimated": not usage.measurement.exact,
            "source": str(usage.measurement.mode),
        }
    )
    return value


def _protocol_error(value: Any, *, phase: str = "provider") -> dict[str, Any]:
    if value is None:
        return {
            "message": "Provider 返回了非成功终态",
            "exception_type": "ProviderProtocolError",
            "phase": phase,
        }
    raw = value.model_dump(mode="json", exclude_none=True)
    return {
        **raw,
        "exception_type": str(raw.get("type") or "ProviderProtocolError"),
        "phase": phase,
    }


def _is_context_length_exceeded(value: Any) -> bool:
    if isinstance(value, ContextLengthExceededError):
        return True
    if isinstance(value, ProviderError):
        if str(value.category).casefold() == "context_length_exceeded":
            return True
        return _is_context_length_exceeded(value.body) or (
            "context_length_exceeded" in str(value).casefold()
        )
    if callable(getattr(value, "model_dump", None)):
        value = value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        code = str(value.get("code") or "").casefold()
        error_type = str(value.get("type") or value.get("category") or "").casefold()
        try:
            provider_status = int(value.get("provider_status"))
        except (TypeError, ValueError):
            provider_status = None
        if code == "context_length_exceeded" or error_type == "context_length_exceeded":
            return True
        if code == "provider_bad_response" and provider_status == 400:
            return True
        return any(_is_context_length_exceeded(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_is_context_length_exceeded(item) for item in value)
    return isinstance(value, str) and "context_length_exceeded" in value.casefold()


def _raise_if_context_length_exceeded(value: Any) -> None:
    if _is_context_length_exceeded(value):
        raise ContextLengthExceededError(
            "Provider 返回 context_length_exceeded，已触发运行时上下文压缩"
        )


def _events_for_protocol_response(response: KemoResponse) -> Iterator[RunEvent]:
    if response.status not in {ResponseStatus.COMPLETED, ResponseStatus.REQUIRES_ACTION}:
        _raise_if_context_length_exceeded(response.error)
    common = {
        "request_id": response.request_id,
        "response_id": response.id,
    }
    for item in response.output:
        if isinstance(item, ReasoningItem):
            content = item.content or item.summary or ""
            if content:
                yield RunEvent(
                    type="reasoning_delta",
                    item_id=item.id,
                    protocol_event_type="response.output.reasoning",
                    content=content,
                    **common,
                )
        elif isinstance(item, MessageItem):
            for content_index, block in enumerate(item.content):
                if isinstance(block, TextContent) and block.text:
                    yield RunEvent(
                        type="text_delta",
                        item_id=item.id,
                        content_index=content_index,
                        protocol_event_type="response.output_text",
                        content=block.text,
                        **common,
                    )
        elif isinstance(item, ToolCallItem):
            yield RunEvent(
                type="tool_call_start",
                item_id=item.id,
                protocol_event_type="tool_call.completed",
                tool_call_id=item.call_id,
                tool_name=item.name,
                arguments=item.arguments,
                metadata={"raw_arguments": item.arguments_raw},
                **common,
            )
    usage = _protocol_usage_dict(response.usage)
    yield RunEvent(
        type="usage",
        usage=usage,
        protocol_event_type="usage.updated",
        **common,
    )
    response_payload = response.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    if response.status in {ResponseStatus.COMPLETED, ResponseStatus.REQUIRES_ACTION}:
        yield RunEvent(
            type="done",
            usage=usage,
            protocol_event_type="response.completed",
            metadata={
                "model": response.model,
                "response_id": response.id,
                "provider_response_id": response.provider_response_id,
                "provider_response": response_payload,
                **response.metadata,
            },
            **common,
        )
    else:
        yield RunEvent(
            type="error",
            error=_protocol_error(response.error),
            protocol_event_type=f"response.{response.status}",
            metadata={"provider_response": response_payload},
            **common,
        )


def _run_events_for_protocol_event(event: ProviderStreamEvent) -> Iterator[RunEvent]:
    common = {
        "event_id": event.event_id,
        "sequence": event.sequence,
        "run_sequence": event.run_sequence,
        "request_id": event.request_id,
        "response_id": event.response_id,
        "item_id": event.item_id or "",
        "content_index": event.content_index,
        "protocol_event_type": str(event.type),
    }
    metadata = {"protocol_data": event.data} if event.data else {}
    if event.type == StreamEventType.OUTPUT_TEXT_DELTA and event.delta:
        yield RunEvent(type="text_delta", content=event.delta, metadata=metadata, **common)
    elif event.type in {
        StreamEventType.REASONING_SUMMARY_DELTA,
        StreamEventType.REASONING_CONTENT_DELTA,
    } and event.delta:
        yield RunEvent(
            type="reasoning_delta", content=event.delta, metadata=metadata, **common
        )
    elif event.type == StreamEventType.TOOL_CALL_COMPLETED:
        item = event.item if isinstance(event.item, ToolCallItem) else None
        arguments = item.arguments if item is not None else event.data.get("arguments", {})
        yield RunEvent(
            type="tool_call_start",
            tool_call_id=(item.call_id if item is not None else event.call_id or ""),
            tool_name=(item.name if item is not None else event.name or ""),
            arguments=arguments if isinstance(arguments, dict) else {"_value": arguments},
            metadata={
                **metadata,
                "raw_arguments": item.arguments_raw if item is not None else None,
            },
            **common,
        )
    elif event.type == StreamEventType.USAGE_UPDATED and event.usage is not None:
        yield RunEvent(
            type="usage", usage=_protocol_usage_dict(event.usage), metadata=metadata, **common
        )
    elif event.type == StreamEventType.ERROR:
        _raise_if_context_length_exceeded(event.error)
        yield RunEvent(
            type="error", error=_protocol_error(event.error), metadata=metadata, **common
        )
    elif event.type in {
        StreamEventType.RESPONSE_COMPLETED,
        StreamEventType.RESPONSE_INCOMPLETE,
        StreamEventType.RESPONSE_FAILED,
        StreamEventType.RESPONSE_CANCELLED,
    }:
        response = event.response
        if response is None:
            yield RunEvent(
                type="error",
                error={
                    "message": "Provider 终态事件缺少完整 response",
                    "exception_type": "ProviderProtocolError",
                    "phase": "provider",
                },
                metadata=metadata,
                **common,
            )
            return
        if response.status not in {ResponseStatus.COMPLETED, ResponseStatus.REQUIRES_ACTION}:
            _raise_if_context_length_exceeded(response.error)
        payload = response.model_dump(mode="json", by_alias=True, exclude_none=True)
        terminal_metadata = {
            **metadata,
            **response.metadata,
            "model": response.model,
            "response_id": response.id,
            "provider_response_id": response.provider_response_id,
            "provider_response": payload,
        }
        if response.status in {ResponseStatus.COMPLETED, ResponseStatus.REQUIRES_ACTION}:
            yield RunEvent(
                type="done",
                usage=_protocol_usage_dict(response.usage),
                metadata=terminal_metadata,
                **common,
            )
        else:
            yield RunEvent(
                type="error",
                error=_protocol_error(response.error),
                metadata=terminal_metadata,
                **common,
            )


def _provider_events(
    provider: Any,
    protocol_request: KemoRequest,
) -> Iterator[RunEvent]:
    stream = getattr(provider, "stream", None)
    create = getattr(provider, "create", None)
    if protocol_request.stream:
        if not callable(stream):
            raise EngineError("Provider 必须实现 Kemo stream() 接口")
        for protocol_event in stream(protocol_request):
            if not isinstance(protocol_event, ProviderStreamEvent):
                raise EngineError("Provider stream() 必须返回 ProviderStreamEvent")
            yield from _run_events_for_protocol_event(protocol_event)
        return
    if not callable(create):
        raise EngineError("Provider 必须实现 Kemo create() 接口")
    response = create(protocol_request)
    if not isinstance(response, KemoResponse):
        raise EngineError("Provider create() 必须返回 KemoResponse")
    yield from _events_for_protocol_response(response)


def _assistant_tool_message(text: str, calls: list[ToolCall]) -> dict[str, Any]:
    return {
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


def _json_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


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


def _memory_injected_chars(bundle: PromptBundle) -> int:
    return sum(
        section.injected_chars
        for section in bundle.sections
        if section.name in {"permanent_memory", "important_memory"}
        or section.name.startswith("temporary_memory:")
    )


def _copy_committed_round_to_archive(
    archive_window: dict[str, Any],
    runtime_window: dict[str, Any],
    runtime_round_number: int,
    archive_round_number: int,
) -> None:
    """Append only the new raw round to archive, preserving older uncompressed data."""

    archive_window["text"]["messages"].extend(
        copy.deepcopy(runtime_window["text"]["messages"][-2:])
    )
    for section in ("think", "tool"):
        source_rounds = (runtime_window.get(section) or {}).get("rounds", [])
        target_rounds = archive_window.setdefault(section, {}).setdefault("rounds", [])
        for raw in source_rounds:
            if not isinstance(raw, dict) or raw.get("round") != runtime_round_number:
                continue
            item = copy.deepcopy(raw)
            item["round"] = archive_round_number
            target_rounds.append(item)
    source_items = (runtime_window.get("items") or {}).get("items", [])
    target_items = archive_window.setdefault("items", {}).setdefault("items", [])
    for raw in source_items:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("metadata"), dict)
            or raw["metadata"].get("round") != runtime_round_number
        ):
            continue
        item = copy.deepcopy(raw)
        item["metadata"] = {
            **item["metadata"],
            "round": archive_round_number,
        }
        target_items.append(item)
    runtime_data = runtime_window["data"]
    archive_data = archive_window["data"]
    archive_data["rounds"] = archive_round_number
    archive_metrics = archive_data.setdefault("round_metrics", [])
    if not isinstance(archive_metrics, list):
        archive_metrics = []
        archive_data["round_metrics"] = archive_metrics
    runtime_metric = next(
        (
            item
            for item in reversed(runtime_data.get("round_metrics", []))
            if isinstance(item, dict) and item.get("round") == runtime_round_number
        ),
        None,
    )
    if runtime_metric is not None:
        metric = copy.deepcopy(runtime_metric)
        metric["round"] = archive_round_number
        archive_metrics.append(metric)
    archive_data["token_usage"] = copy.deepcopy(runtime_data.get("token_usage", {}))
    archive_data.pop("context", None)


def _round_item_data(window: dict[str, Any], round_number: int) -> list[dict[str, Any]]:
    return [
        item
        for item in (window.get("items") or {}).get("items", [])
        if isinstance(item, dict)
        and isinstance(item.get("metadata"), dict)
        and item["metadata"].get("round") == round_number
    ]


def _compress_per_round_tool_think(
    *,
    window: dict[str, Any],
    conserved_rounds: int,
    agent_runner: AgentRunner,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    """Compress at most one newly unprotected round in the mutable temp mirror."""

    think_rounds = (window.get("think") or {}).get("rounds", [])
    tool_rounds = (window.get("tool") or {}).get("rounds", [])
    think_by_number = {
        int(item["round"]): item
        for item in think_rounds
        if isinstance(item, dict) and str(item.get("round", "")).isdigit()
    }
    tool_by_number = {
        int(item["round"]): item
        for item in tool_rounds
        if isinstance(item, dict) and str(item.get("round", "")).isdigit()
    }
    latest_round = int((window.get("data") or {}).get("rounds", 0))
    candidates = [
        number
        for number in sorted(set(think_by_number) | set(tool_by_number))
        if latest_round - number > max(0, conserved_rounds)
        and not bool((think_by_number.get(number) or {}).get("compressed"))
        and not bool((tool_by_number.get(number) or {}).get("compressed"))
    ]
    if not candidates:
        return {"compressed": False, "round": None}
    round_number = candidates[0]
    think_data = think_by_number.get(round_number)
    tool_data = tool_by_number.get(round_number)
    item_data = _round_item_data(window, round_number)
    has_payload = bool(str((think_data or {}).get("content") or "").strip()) or bool(
        (tool_data or {}).get("calls")
    ) or any(item.get("type") in {"reasoning", "tool_call", "tool_result"} for item in item_data)
    summary = ""
    usage: dict[str, Any] = {}
    if has_payload:
        result = agent_runner.run(
            "context_manage",
            {
                "previous_summary": None,
                "rounds": [
                    {
                        "round": round_number,
                        "think": copy.deepcopy(think_data),
                        "tool": copy.deepcopy(tool_data),
                        "items": copy.deepcopy(item_data),
                    }
                ],
                "trigger": "tool_think_compress",
            },
            cancel_event=cancel_event,
            max_tokens=512,
        )
        summary = str(result.data.get("narrative") or "").strip()
        usage = dict(result.usage)
    if think_data is not None:
        think_data["content"] = summary
        think_data["summary"] = summary
        think_data["compressed"] = True
    if tool_data is not None:
        tool_data["calls"] = []
        tool_data["compressed"] = True

    items = (window.get("items") or {}).get("items", [])
    rewritten: list[dict[str, Any]] = []
    summary_written = False
    for item in items if isinstance(items, list) else []:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        same_round = isinstance(metadata, dict) and metadata.get("round") == round_number
        if not same_round:
            rewritten.append(item)
            continue
        kind = item.get("type")
        if kind in {"tool_call", "tool_result"}:
            continue
        if kind == "reasoning":
            if summary_written:
                continue
            if not summary:
                rewritten.append(copy.deepcopy(item))
                summary_written = True
                continue
            replacement = copy.deepcopy(item)
            replacement["content"] = summary
            replacement["extensions"] = {
                **(replacement.get("extensions") or {}),
                "compressed": True,
            }
            rewritten.append(replacement)
            summary_written = True
            continue
        if (
            not summary_written
            and summary
            and kind == "message"
            and item.get("role") == "assistant"
        ):
            rewritten.append(
                {
                    "id": f"rs_{uuid.uuid4().hex}",
                    "type": "reasoning",
                    "status": "completed",
                    "content": summary,
                    "metadata": {"round": round_number, "history_source": "runtime_compression"},
                    "extensions": {"compressed": True},
                }
            )
            summary_written = True
        rewritten.append(item)
    if isinstance((window.get("items") or {}).get("items"), list):
        window["items"]["items"] = rewritten
    return {
        "compressed": True,
        "round": round_number,
        "generated": has_payload,
        "usage": usage,
    }


def _extract_round_memory(
    *,
    root: Path,
    user: str,
    config: dict[str, Any],
    round_number: int,
    prompt: str,
    text: str,
    reasoning: str,
    tool_records: list[dict[str, Any]],
    agent_runner: AgentRunner,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    """Extract and persist memory fragments from one completed round."""

    round_data: dict[str, Any] = {
        "round": round_number,
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": text},
        ],
    }
    if reasoning:
        round_data["think"] = {"content": reasoning}
    if tool_records:
        round_data["tools"] = [
            {
                "name": str(record.get("name") or ""),
                "status": str(record.get("status") or ""),
                "result": record.get("result"),
            }
            for record in tool_records
            if isinstance(record, dict)
        ]

    source = {"source": "round_commit", "round": round_number}
    try:
        result = agent_runner.run(
            "self_improve",
            {
                "trigger": "context_compression",
                "rounds": [round_data],
                "source": source,
            },
            cancel_event=cancel_event,
        )
        candidates = result.data.get("candidates")
        if not isinstance(candidates, list):
            raise EngineError("self_improve 输出缺少 candidates 数组")
        persisted = MemoryStore(root, user, config).upsert_candidates(
            candidates,
            source=source,
        )
        return {
            "status": "completed",
            "candidate_count": len(candidates),
            "agent": result.agent,
            "usage": dict(result.usage),
            "persisted": persisted,
            "error": None,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "candidate_count": 0,
            "error": {
                "message": str(exc),
                "exception_type": type(exc).__name__,
            },
        }


def _extract_memory_backlog(
    *,
    root: Path,
    user: str,
    source: str,
    session_id: str,
    directory: Path,
    window: dict[str, Any],
    config: dict[str, Any],
    agent_runner: AgentRunner | None,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    """Extract every unprocessed committed round and advance one durable cursor."""

    data = window.setdefault("data", {})
    rounds = max(0, int(data.get("rounds") or 0))
    base_result: dict[str, Any] = {
        "user": user,
        "source": source,
        "session_id": session_id,
        "round": rounds,
        "candidates": 0,
        "extraction": None,
        "extractions": [],
        "usage": _usage_total(),
        "index_error": None,
    }
    if rounds < 1:
        return {
            **base_result,
            "status": "skipped",
            "reason": "no_complete_round",
        }

    extraction_mode = memory_extraction_mode(config)
    if extraction_mode == "disabled":
        return {
            **base_result,
            "status": "skipped",
            "reason": "memory_extraction_disabled",
            "mode": extraction_mode,
        }

    raw_cursor = data.get("memory_processed_round")
    processed_round = (
        max(0, int(raw_cursor or 0))
        if raw_cursor is not None
        else max(0, rounds - 1)
    )
    if processed_round >= rounds:
        return {
            **base_result,
            "status": "skipped",
            "reason": "already_processed",
        }

    if agent_runner is None:
        raise EngineError("记忆提取缺少 AgentRunner")

    data["memory_processed_round"] = processed_round
    data["memory_status"] = "processing"
    data.pop("memory_error", None)
    commit_window(directory, window)
    index_error: dict[str, Any] | None = None
    try:
        update_memory_state(
            root,
            user,
            source,
            session_id,
            status="processing",
        )
    except Exception as exc:
        index_error = {
            "message": str(exc),
            "exception_type": type(exc).__name__,
        }

    extractions: list[dict[str, Any]] = []
    candidates = 0
    usage = _usage_total()
    for round_number in range(processed_round + 1, rounds + 1):
        try:
            extraction = _extract_round_memory(
                root=root,
                user=user,
                config=config,
                round_number=round_number,
                agent_runner=agent_runner,
                cancel_event=cancel_event,
                **memory_round_payload(window, round_number),
            )
        except Exception as exc:
            extraction = {
                "status": "failed",
                "candidate_count": 0,
                "error": {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                },
            }
        extractions.append(extraction)
        extraction_usage = extraction.get("usage")
        if isinstance(extraction_usage, dict):
            _record_provider_request(usage, _usage_from_dict(extraction_usage))
        if extraction.get("status") != "completed":
            error = (
                extraction.get("error")
                if isinstance(extraction.get("error"), dict)
                else {"message": "记忆提取失败"}
            )
            data["memory_status"] = "failed"
            data["memory_error"] = error
            commit_window(directory, window)
            try:
                update_memory_state(
                    root,
                    user,
                    source,
                    session_id,
                    status="failed",
                    error=error,
                )
            except Exception as exc:
                index_error = index_error or {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                }
            return {
                **base_result,
                "status": "failed",
                "round": round_number,
                "candidates": candidates,
                "extraction": extraction,
                "extractions": extractions,
                "usage": usage,
                "index_error": index_error,
            }

        candidates += int(extraction.get("candidate_count") or 0)
        data["memory_processed_round"] = round_number
        data["memory_status"] = (
            "completed" if round_number >= rounds else "processing"
        )
        data.pop("memory_error", None)
        commit_window(directory, window)
        try:
            update_memory_state(
                root,
                user,
                source,
                session_id,
                processed_round=round_number,
                status=str(data["memory_status"]),
            )
        except Exception as exc:
            index_error = index_error or {
                "message": str(exc),
                "exception_type": type(exc).__name__,
            }
    return {
        **base_result,
        "status": "completed",
        "candidates": candidates,
        "extraction": extractions[-1],
        "extractions": extractions,
        "usage": usage,
        "index_error": index_error,
    }


def _iter_request_events_impl(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    """Run one complete model/tool loop, committing only on successful done."""

    run_started = time.monotonic()
    try:
        user = _required_text(request, "user")
        compress_only_requested = bool(request.get("compress_only", False))
        content_blocks = (
            [] if compress_only_requested else _request_content_blocks(request)
        )
        if not content_blocks and not compress_only_requested:
            raise EngineError("请求必须包含非空 prompt 或 content[]")
        prompt = _content_display(content_blocks)
        source = _required_text(request, "source")
        session_id = _required_text(request, "session_id")
        run_id = str(request.get("run_id") or "")
        base = (root or project_root()).resolve()
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
            raise
        yield error_event(exc, phase="request")
        return

    with _session_lock(base, user, source, session_id):
        history_run_registered = False
        history_run_error: dict[str, Any] | None = None
        try:
            config = load_config(user, base)
            context_policy = ContextPolicy.from_config(config)
            source_policy = MainAgentSourcePolicy.from_config(config)
            runtime_provider = provider_runtime_config(config)
            provider = provider_factory(runtime_provider)
            agent_runner = AgentRunner(
                base,
                user,
                config=config,
                provider_factory=provider_factory,
            )
            window_path, archive_window, _ = prepare_window(base, user, source, session_id)
            try:
                history_run_registered = (
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
                history_run_error = {
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
                apply_runtime_tool_policy(tool_registry_factory(base, user), config)
                if tools_enabled
                else ToolRegistry({})
            )
            tool_schemas = registry.schemas() or None
            tool_timeout = float(tool_config.get("timeout", 240))
            max_iterations = max(1, int(tool_config.get("max_iterations", 8)))
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
            current_user_message = (
                None
                if compress_only
                else {"role": "user", "content": _content_for_message(content_blocks)}
            )
            force_compress = bool(request.get("compress", False) or compress_only)
            context_selection = select_context(
                window=window,
                policy=context_policy,
                system_message=system_message,
                current_user_message=current_user_message,
                tools=tool_schemas,
                force_compress=force_compress,
            )
            _ensure_fixed_content_fits(context_selection, system_message=system_message)
            summary_usage = _usage_total()
            compression_memory: dict[str, Any] | None = None
            compression_usage = _usage_total()
            if force_compress or context_selection.removed_rounds:
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
            summary_cache = None
            summary_diagnostics: dict[str, Any] = {
                "cache_hit": False,
                "generated": False,
                "failed": False,
                "covered_rounds": [],
            }
                        # 摘要也消耗输入标记。  重新选择，直至设置完毕
                        # 移除的整轮是稳定的，因此没有一轮可以被取代
                        # 摘要，但也未包含在该摘要中。
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
                    cache_path=runtime_path / "context_summary.json",
                    groups=context_selection.removed_rounds,
                    agent_runner=agent_runner,
                    agent_name=summary_agent,
                    trigger=summary_trigger,
                    cancel_event=cancel_event,
                    chunk_token_budget=max(256, context_policy.input_budget // 2),
                    max_tokens=min(4096, max(256, context_policy.output_reserve)),
                    response_hook=lambda raw: _record_provider_request(
                        summary_usage, _usage_from_dict(raw)
                    ),
                    event_callback=subagent_events.append,
                    skip_memory_extraction=True,
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
            if cancel_event is not None and cancel_event.is_set():
                return
            messages = context_selection.messages
            context_stats = context_selection.stats()
            context_stats["summary"] = summary_diagnostics
            context_stats["summary_usage"] = summary_usage
            for subagent_event in subagent_events:
                yield subagent_event
            if compress_only:
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
                            str(runtime_path / "context_summary.json")
                            if summary_cache is not None
                            else None
                        ),
                        "compressed": True,
                        "committed": False,
                        "memory": compression_memory,
                    },
                )
                return

            stream = bool(request.get("stream", runtime_provider.get("stream", False)))
            all_text: list[str] = []
            all_reasoning: list[str] = []
            tool_records: list[dict[str, Any]] = []
            guidance_channel = request.get("_guidance_queue")
            consumed_guidance: list[str] = []
            pending_guidance_ack: list[str] = []
            protocol_parent_request_id: str | None = None
            provider_responses: list[dict[str, Any]] = []
            usage_total = copy.deepcopy(summary_usage)
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

            for iteration in range(1, max_iterations + 1):
                if cancel_event is not None and cancel_event.is_set():
                    return
                if iteration > 1:
                    active_tool_schemas = (
                        registry.schemas(exclude=failures.unavailable) or None
                    )
                    current_tokens = estimate_messages_tokens(messages) + estimate_tools_tokens(
                        active_tool_schemas
                    )
                    if current_tokens > context_policy.token_limit:
                        yield error_event(
                            EngineError(
                                "当前工具循环已超过上下文上限；为避免拆散工具消息组，本轮已停止"
                            ),
                            phase="context",
                        )
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
                    chat_request = ChatRequest(
                        model=runtime_provider["model"],
                        messages=messages,
                        stream=stream,
                        tools=active_tool_schemas,
                        max_tokens=request_max_tokens,
                    )
                    protocol_request = chat_request_to_kemo(chat_request).model_copy(
                        update={
                            "request_id": f"req_{uuid.uuid4().hex}",
                            "parent_request_id": protocol_parent_request_id,
                            "attempt": context_retry_count + 1,
                            "metadata": {
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
                            for event in _provider_events(provider, protocol_request):
                                if cancel_event is not None and cancel_event.is_set():
                                    return
                                if pending_guidance_ack:
                                    applied_guidance = list(pending_guidance_ack)
                                    pending_guidance_ack.clear()
                                    consumed_guidance.extend(applied_guidance)
                                    yield RunEvent(
                                        type="guidance_applied",
                                        metadata={
                                            "guidance": applied_guidance,
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
                                    calls.append(
                                        ToolCall(
                                            id=event.tool_call_id,
                                            name=event.tool_name,
                                            arguments=event.arguments or {},
                                        )
                                    )
                                    yield event
                                elif event.type == "usage":
                                    iteration_usage = _usage_from_dict(event.usage)
                                    yield RunEvent(
                                        type="usage",
                                        usage=event.usage,
                                        metadata={"iteration": iteration},
                                    )
                                elif event.type == "error":
                                    _raise_if_context_length_exceeded(event.error)
                                    yield event
                                    return
                                elif event.type == "done":
                                    iteration_done = event
                        break
                    except ProviderCongestionError as exc:
                        if cancel_event is not None and cancel_event.is_set():
                            return
                        yield error_event(exc, phase="provider")
                        return
                    except BaseException as exc:
                        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                            raise
                        if (
                            iteration != 1
                            or context_retry_count >= 2
                            or not _is_context_length_exceeded(exc)
                        ):
                            raise
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
                            current_user_message=current_user_message,
                            tools=active_tool_schemas,
                            force_compress=True,
                        )
                        if not retry_selection.removed_rounds:
                            raise ContextLengthExceededError(
                                "Provider 上下文超限，但没有可继续裁剪的历史轮次"
                            ) from exc
                        if compression_memory is None:
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
                            cache_path=runtime_path / "context_summary.json",
                            groups=retry_selection.removed_rounds,
                            agent_runner=agent_runner,
                            agent_name="context_manage",
                            trigger="api_context_length",
                            cancel_event=cancel_event,
                            chunk_token_budget=max(256, retry_policy.input_budget // 2),
                            max_tokens=min(4096, max(256, retry_policy.output_reserve)),
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
                    yield error_event(EngineError("Provider 事件流缺少 done 终态"), phase="provider")
                    return
                if iteration_usage is None:
                    iteration_usage = _usage_from_dict(iteration_done.usage)
                all_text.extend(iteration_text)
                all_reasoning.extend(iteration_reasoning)
                _record_provider_request(usage_total, iteration_usage)
                final_metadata = dict(iteration_done.metadata)
                provider_response = final_metadata.get("provider_response")
                if isinstance(provider_response, dict):
                    provider_responses.append(copy.deepcopy(provider_response))
                protocol_parent_request_id = protocol_request.request_id

                if not calls:
                    pending_guidance = _drain_guidance(guidance_channel)
                    if pending_guidance and iteration < max_iterations:
                        messages.append(
                            {"role": "assistant", "content": "".join(iteration_text)}
                        )
                        _append_guidance(messages, pending_guidance)
                        pending_guidance_ack.extend(pending_guidance)
                        all_text.append("\n\n")
                        yield RunEvent(type="text_delta", content="\n\n")
                        continue
                    completed = True
                    break
                if iteration >= max_iterations:
                    yield error_event(
                        EngineError(f"工具调用超过最大循环次数 {max_iterations}"),
                        phase="tool_loop",
                    )
                    return

                assistant_text = "".join(iteration_text)
                messages.append(_assistant_tool_message(assistant_text, calls))
                for call in calls:
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    signature = f"{call.name}:{json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)}"
                    duplicate = False
                    tool_started = time.monotonic()
                    if failures.is_unavailable(call.name):
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
                        duplicate = signature in seen_calls
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
                                        "transport_registry": request.get(
                                            "_transport_registry"
                                        ),
                                        "task_plan_id": request.get("_task_plan_id"),
                                        "task_plan_step_id": request.get("_task_plan_step_id"),
                                        "task_plan_mode": request.get("_task_plan_mode"),
                                        "knowledge_scopes": list(
                                            source_policy.direct_knowledge_scopes()
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
                                result_payload = {
                                    "ok": False,
                                    "error": {
                                        "message": str(exc),
                                        "exception_type": type(exc).__name__,
                                    },
                                }
                                status = "failed"
                            seen_calls[signature] = copy.deepcopy(result_payload)
                        failure_count = failures.record(
                            call.name,
                            succeeded=bool(result_payload.get("ok")),
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
                        "result": result_payload,
                        "iteration": iteration,
                        "elapsed_ms": elapsed_ms,
                    }
                    tool_records.append(record)
                    yield RunEvent(
                        type="tool_call_result",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        arguments=call.arguments,
                        result=result_payload,
                        metadata={
                            "status": status,
                            "duplicate": duplicate,
                            "iteration": iteration,
                            "elapsed_ms": elapsed_ms,
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
                _append_guidance(messages, pending_guidance)
                pending_guidance_ack.extend(pending_guidance)

            if not completed:
                yield error_event(EngineError("模型工具循环未完成"), phase="tool_loop")
                return
            if cancel_event is not None and cancel_event.is_set():
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
                    {"role": "user", "content": prompt},
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
                    for block in content_blocks
                ],
                reasoning=reasoning,
                text=text,
                tool_records=tool_records,
                provider_responses=provider_responses,
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
                    "provider_responses": copy.deepcopy(provider_responses),
                }
            )
            window["data"]["context"] = {
                **context_stats,
                "round_offset": max(0, archive_round_number - round_number),
                "workspace_rounds": round_number,
                "summary_cache": (
                    "context_summary.json" if summary_cache is not None else None
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
            runtime_window = _trim_to_max_rounds(window, context_policy.max_rounds)
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
                    "context_summary.json" if summary_cache is not None else None
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
            commit_window(runtime_path, runtime_window)
            history_index_error: dict[str, Any] | None = history_run_error
            try:
                update_memory_state(
                    base,
                    user,
                    source,
                    session_id,
                    status=initial_memory_status,
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
                history_run_registered = False
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

            # 仅对选择并实际发送到主模型的记忆进行加权。取消或失败的
            # 回合不会走到这里，仅被检索但未注入的候选也不会加权。
            memory_weighted_files: list[str] = []
            memory_weight_error = None
            try:
                memory_weighted_files = memory_store.mark_used(list(prompt_bundle.memory_ids))
            except Exception as exc:
                memory_weight_error = {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                }
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
            yield error_event(exc, phase="run")
        finally:
            if history_run_registered:
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


def context_status(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
) -> dict[str, Any]:
    user = _required_text(request, "user")
    source = _required_text(request, "source")
    session_id = _required_text(request, "session_id")
    base = (root or project_root()).resolve()
    config = load_config(user, base)
    policy = ContextPolicy.from_config(config)
    window_path, archive_window, is_new = prepare_window(base, user, source, session_id)
    runtime_path, window = (
        (runtime_window_path(window_path), archive_window)
        if is_new
        else load_runtime_window(
            window_path,
            archive_window,
            max_rounds=policy.max_rounds,
        )
    )
    tool_config = config.get("tools") or {}
    registry = (
        apply_runtime_tool_policy(tool_registry_factory(base, user), config)
        if bool(tool_config.get("enabled", True))
        else ToolRegistry({})
    )
    memory_store = MemoryStore(base, user, config)
    prompt_bundle = build_prompt_bundle(
        base,
        user,
        config,
        plugin_manifests=registry.plugin_manifests,
        memory_store=memory_store,
    )
    selection = select_context(
        window=window,
        policy=policy,
        system_message=(
            {"role": "system", "content": prompt_bundle.text}
            if prompt_bundle.text
            else None
        ),
        current_user_message=None,
        tools=registry.schemas() or None,
    )
    cache_path = runtime_path / "context_summary.json"
    persisted = window.get("data", {}).get("context")
    return {
        "user": user,
        "source": source,
        "session_id": session_id,
        "window": None if is_new else window_path.name,
        "rounds": int(window.get("data", {}).get("rounds", 0)),
        "context": selection.stats(),
        "prompt": prompt_bundle.diagnostics,
        "last_committed_context": persisted if isinstance(persisted, dict) else None,
        "summary_cache_exists": cache_path.is_file(),
        "policy": {
            "recent_tool_rounds": policy.recent_tool_rounds,
            "recent_full_rounds": policy.recent_full_rounds,
            "max_rounds": policy.max_rounds,
            "rounds_after_compression": policy.rounds_after_compression,
            "token_limit": policy.token_limit,
            "compression_ratio": policy.compression_ratio,
            "input_budget": policy.input_budget,
            "output_reserve": policy.output_reserve,
        },
    }


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
