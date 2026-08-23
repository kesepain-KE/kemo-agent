"""Translate Provider protocol responses into stable runtime events."""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any, Iterator

from events import RunEvent
from provider.protocol.diagnostics import sanitize_provider_diagnostic
from provider.protocol.enums import ResponseStatus, StreamEventType
from provider.protocol.models import (
    KemoRequest,
    KemoResponse,
    MessageItem,
    ReasoningItem,
    TextContent,
    ToolCallItem,
    Usage as ProtocolUsage,
)
from provider.protocol.streaming import ProviderStreamEvent
from provider.schema import ProviderError
from run.infra import ContextLengthExceededError, EngineError
from run.extensions import persist_response_media
from run.tools import invalid_tool_call_error


def protocol_usage_dict(usage: ProtocolUsage) -> dict[str, Any]:
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


def protocol_error(value: Any, *, phase: str = "provider") -> dict[str, Any]:
    if value is None:
        return {
            "message": "Provider 返回了非成功终态",
            "exception_type": "ProviderProtocolError",
            "phase": phase,
        }
    raw = sanitize_provider_diagnostic(
        value.model_dump(mode="json", exclude_none=True)
    )
    if not isinstance(raw, dict):
        raw = {}
    return {
        **raw,
        "exception_type": str(raw.get("type") or "ProviderProtocolError"),
        "phase": phase,
    }


def response_terminal_error(response: KemoResponse) -> dict[str, Any]:
    if response.error is not None:
        return protocol_error(response.error)
    if response.status == ResponseStatus.INCOMPLETE:
        details = sanitize_provider_diagnostic(
            copy.deepcopy(response.incomplete_details or {}),
            key="incomplete_details",
        )
        if not isinstance(details, dict):
            details = {}
        reason = str(details.get("reason") or "incomplete")
        return {
            "message": f"Provider 输出未完整结束：{reason}",
            "exception_type": "ProviderResponseIncomplete",
            "phase": "provider",
            "status": str(response.status),
            "stop_reason": reason,
            "incomplete_details": details,
        }
    if response.status == ResponseStatus.CANCELLED:
        return {
            "message": "Provider 已取消本次响应",
            "exception_type": "ProviderResponseCancelled",
            "phase": "provider",
            "status": str(response.status),
            "stop_reason": "provider_cancelled",
        }
    return protocol_error(response.error)


def is_context_length_exceeded(value: Any) -> bool:
    if isinstance(value, ContextLengthExceededError):
        return True
    if isinstance(value, ProviderError):
        if str(value.category).casefold() == "context_length_exceeded":
            return True
        return is_context_length_exceeded(value.body) or (
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
        return any(is_context_length_exceeded(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(is_context_length_exceeded(item) for item in value)
    return isinstance(value, str) and "context_length_exceeded" in value.casefold()


def raise_if_context_length_exceeded(value: Any) -> None:
    if is_context_length_exceeded(value):
        raise ContextLengthExceededError(
            "Provider 返回 context_length_exceeded，已触发运行时上下文压缩"
        )


def response_media_events(
    response: KemoResponse,
    *,
    provider: Any | None,
    root: Path | None,
    user: str,
    cancel_event: threading.Event | None,
) -> Iterator[RunEvent]:
    if provider is None or root is None or not user:
        return
    artifacts = persist_response_media(
        provider,
        response,
        root=root,
        user=user,
        cancel_event=cancel_event,
    )
    if artifacts:
        response.metadata["artifacts"] = copy.deepcopy(artifacts)
    for artifact in artifacts:
        yield RunEvent(
            type="media_output",
            request_id=response.request_id,
            response_id=response.id,
            result=artifact,
            metadata={"artifact": artifact},
        )


def events_for_protocol_response(
    response: KemoResponse,
    *,
    provider: Any | None = None,
    root: Path | None = None,
    user: str = "",
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    if response.status not in {ResponseStatus.COMPLETED, ResponseStatus.REQUIRES_ACTION}:
        raise_if_context_length_exceeded(response.error)
    invalid_call = next(
        (
            item
            for item in response.output
            if isinstance(item, ToolCallItem) and item.parse_error is not None
        ),
        None,
    )
    if invalid_call is not None:
        yield RunEvent(
            type="error",
            error=invalid_tool_call_error(invalid_call),
            protocol_event_type="tool_call.invalid",
            request_id=response.request_id,
            response_id=response.id,
            item_id=invalid_call.id,
        )
        return
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
                **common,
            )
    yield from response_media_events(
        response,
        provider=provider,
        root=root,
        user=user,
        cancel_event=cancel_event,
    )
    usage = protocol_usage_dict(response.usage)
    yield RunEvent(
        type="usage",
        usage=usage,
        protocol_event_type="usage.updated",
        **common,
    )
    response_payload = sanitize_provider_diagnostic(
        response.model_dump(mode="json", by_alias=True, exclude_none=True)
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
                **(
                    sanitize_provider_diagnostic(response.metadata)
                    if isinstance(response.metadata, dict)
                    else {}
                ),
            },
            **common,
        )
    else:
        yield RunEvent(
            type="error",
            error=response_terminal_error(response),
            protocol_event_type=f"response.{response.status}",
            metadata={"provider_response": response_payload},
            **common,
        )


def run_events_for_protocol_event(
    event: ProviderStreamEvent,
    *,
    provider: Any | None = None,
    root: Path | None = None,
    user: str = "",
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
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
        if item is not None and item.parse_error is not None:
            yield RunEvent(
                type="error",
                error=invalid_tool_call_error(item),
                **common,
            )
            return
        arguments = item.arguments if item is not None else event.data.get("arguments", {})
        yield RunEvent(
            type="tool_call_start",
            tool_call_id=(item.call_id if item is not None else event.call_id or ""),
            tool_name=(item.name if item is not None else event.name or ""),
            arguments=arguments if isinstance(arguments, dict) else {"_value": arguments},
            **common,
        )
    elif event.type == StreamEventType.USAGE_UPDATED and event.usage is not None:
        yield RunEvent(
            type="usage", usage=protocol_usage_dict(event.usage), metadata=metadata, **common
        )
    elif event.type == StreamEventType.ERROR:
        raise_if_context_length_exceeded(event.error)
        yield RunEvent(
            type="error", error=protocol_error(event.error), metadata=metadata, **common
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
            raise_if_context_length_exceeded(response.error)
        payload = sanitize_provider_diagnostic(
            response.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
        safe_response_metadata = sanitize_provider_diagnostic(response.metadata)
        if not isinstance(safe_response_metadata, dict):
            safe_response_metadata = {}
        terminal_metadata = {
            **metadata,
            **safe_response_metadata,
            "model": response.model,
            "response_id": response.id,
            "provider_response_id": response.provider_response_id,
            "provider_response": payload,
        }
        if response.status in {ResponseStatus.COMPLETED, ResponseStatus.REQUIRES_ACTION}:
            yield from response_media_events(
                response,
                provider=provider,
                root=root,
                user=user,
                cancel_event=cancel_event,
            )
            if response.metadata.get("artifacts"):
                terminal_metadata["artifacts"] = copy.deepcopy(
                    response.metadata["artifacts"]
                )
                terminal_metadata["provider_response"] = payload
            yield RunEvent(
                type="done",
                usage=protocol_usage_dict(response.usage),
                metadata=terminal_metadata,
                **common,
            )
        else:
            yield RunEvent(
                type="error",
                error=response_terminal_error(response),
                metadata=terminal_metadata,
                **common,
            )


def provider_events(
    provider: Any,
    protocol_request: KemoRequest,
    *,
    root: Path | None = None,
    user: str = "",
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    stream = getattr(provider, "stream", None)
    create = getattr(provider, "create", None)
    if protocol_request.stream:
        if not callable(stream):
            raise EngineError("Provider 必须实现 Kemo stream() 接口")
        source = (
            stream(protocol_request, cancel_event=cancel_event)
            if getattr(provider, "mode", "") == "kemo"
            else stream(protocol_request)
        )
        iterator = iter(source)
        try:
            for protocol_event in iterator:
                if not isinstance(protocol_event, ProviderStreamEvent):
                    raise EngineError("Provider stream() 必须返回 ProviderStreamEvent")
                yield from run_events_for_protocol_event(
                    protocol_event,
                    provider=provider,
                    root=root,
                    user=user,
                    cancel_event=cancel_event,
                )
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()
        return
    if not callable(create):
        raise EngineError("Provider 必须实现 Kemo create() 接口")
    response = (
        create(protocol_request, cancel_event=cancel_event)
        if getattr(provider, "mode", "") == "kemo"
        else create(protocol_request)
    )
    if not isinstance(response, KemoResponse):
        raise EngineError("Provider create() 必须返回 KemoResponse")
    yield from events_for_protocol_response(
        response,
        provider=provider,
        root=root,
        user=user,
        cancel_event=cancel_event,
    )
