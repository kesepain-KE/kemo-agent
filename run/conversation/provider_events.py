"""Translate Provider protocol responses into stable runtime events."""

from __future__ import annotations

from typing import Callable, Iterable

import copy
import threading
from pathlib import Path
from typing import Any, Generator, Iterator

from events import RunEvent
from provider.protocol.diagnostics import (
    incomplete_retry_metadata,
    sanitize_provider_diagnostic,
)
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
from provider.token_usage import complete_usage, merge_usage_snapshots
from run.infra import ContextLengthExceededError, EngineError
from run.extensions import persist_response_media
from run.tools import invalid_tool_call_error
from run.retry import view_from_error
from run.conversation.provider_projection import durable_provider_response_payload


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
    fields_set = getattr(value, "model_fields_set", set())
    details = raw.get("details")
    if (
        "retryable" not in fields_set
        and not (isinstance(details, dict) and "retryable" in details)
    ):
        # UnifiedError defaults retryable to false for schema compatibility;
        # absence in the upstream payload must remain distinguishable so the
        # outer runtime can apply its transient-error default.
        raw.pop("retryable", None)
    return {
        **raw,
        "exception_type": str(raw.get("type") or "ProviderProtocolError"),
        "phase": phase,
    }


def response_terminal_error(
    response: KemoResponse, *, allow_output_truncated_retry: bool = False,
) -> dict[str, Any]:
    if response.status == ResponseStatus.CANCELLED:
        base_error = (
            protocol_error(response.error)
            if response.error is not None
            else {}
        )
        return {
            **base_error,
            "message": str(
                base_error.get("message") or "Provider 已取消本次响应"
            ),
            "exception_type": str(
                base_error.get("exception_type") or "ProviderResponseCancelled"
            ),
            "phase": "provider",
            "status": str(response.status),
            "stop_reason": "provider_cancelled",
            "cancelled": True,
            "retryable": False,
        }
    if response.status == ResponseStatus.INCOMPLETE:
        raw_incomplete_details: Any = response.incomplete_details or {}
        if callable(getattr(raw_incomplete_details, "model_dump", None)):
            raw_incomplete_details = raw_incomplete_details.model_dump(
                mode="json", exclude_none=True
            )
        details = sanitize_provider_diagnostic(
            copy.deepcopy(raw_incomplete_details),
            key="incomplete_details",
        )
        if not isinstance(details, dict):
            details = {}
        base_error = (
            protocol_error(response.error)
            if response.error is not None
            else {}
        )
        base_retryable = base_error.get("retryable")
        retry = incomplete_retry_metadata(
            details,
            fallback_retryable=(
                base_retryable if isinstance(base_retryable, bool) else None
            ),
            fallback_category=(
                base_error.get("category") or base_error.get("type") or ""
            ),
            fallback_code=base_error.get("code"),
            fallback_status_code=(
                base_error.get("status_code")
                if base_error.get("status_code") is not None
                else base_error.get("provider_status")
            ),
            allow_output_truncated_retry=allow_output_truncated_retry,
        )
        reason = str(retry["reason"])
        error = {
            **base_error,
            "message": str(
                base_error.get("message")
                or f"Provider 输出未完整结束：{reason}"
            ),
            "exception_type": str(
                base_error.get("exception_type") or "ProviderResponseIncomplete"
            ),
            "phase": "provider",
            "status": str(response.status),
            "category": str(retry.get("category") or "provider_incomplete"),
            "stop_reason": reason,
            "incomplete_details": details,
            "retryable": retry["retryable"],
        }
        for field in ("category", "status_code", "retry_after_ms"):
            if field in retry:
                error[field] = retry[field]
        # UnifiedError carries the upstream HTTP status as ``provider_status``;
        # expose the normalized ``status_code`` as well so retry/diagnostic
        # consumers do not lose the boundary evidence on the final attempt.
        if "status_code" not in error and base_error.get("provider_status") is not None:
            error["status_code"] = base_error["provider_status"]
        return error
    if response.error is not None:
        return protocol_error(response.error)
    return protocol_error(response.error)


def is_request_size_exceeded(value: Any) -> bool:
    """A transport limit, deliberately distinct from a model context limit."""
    if isinstance(value, BaseException):
        return getattr(value, "status_code", None) == 413
    dump = getattr(value, "model_dump", None)
    value = dump(mode="json", exclude_none=True) if callable(dump) else value
    if isinstance(value, dict):
        return (
            value.get("status_code") == 413 or value.get("provider_status") == 413
            or any(is_request_size_exceeded(value.get(key)) for key in ("error", "details"))
        )
    return False


def is_context_length_exceeded(value: Any) -> bool:
    """Only explicit context evidence permits compaction, never generic 4xx."""
    if is_request_size_exceeded(value) or view_from_error(value).status_code in {401, 403}:
        return False
    if isinstance(value, ContextLengthExceededError):
        return True
    if isinstance(value, BaseException):
        if any(is_context_length_exceeded(item) for item in (
                getattr(value, "category", None), getattr(value, "body", None),
                getattr(value, "code", None), str(value))):
            return True
    if callable(dump := getattr(value, "model_dump", None)):
        value = dump(mode="json", exclude_none=True)
    if isinstance(value, dict) and any(is_context_length_exceeded(item) for item in value.values()):
        return True
    if isinstance(value, (list, tuple)):
        return any(is_context_length_exceeded(item) for item in value)
    if isinstance(value, str):
        text = value.casefold().replace("_", " ").replace("-", " ")
        if any(marker in text for marker in (
            "context length exceeded", "context window exceeded",
            "maximum context length", "prompt is too long", "prompt too long",
            "input is too long", "input too long", "input token limit exceeded",
            "input length exceeded", "input tokens exceed", "too many input tokens",
            "上下文长度超", "上下文超限", "输入 token 超限",
        )) or (
            ("context length" in text or "context window" in text)
            and any(word in text for word in ("exceed", "too long", "limit reached"))
        ):
            return True
    return False


def raise_if_context_length_exceeded(value: Any) -> None:
    if is_request_size_exceeded(value):
        raise ProviderError("API 请求体超过传输大小上限", status_code=413)
    if is_context_length_exceeded(value):
        raise ContextLengthExceededError(
            "Provider 返回 context_length_exceeded，需要缩小请求上下文后恢复"
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
    defer_tool_validation: bool = False,
    allow_output_truncated_retry: bool = False,
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
    if invalid_call is not None and not defer_tool_validation:
        yield RunEvent(
            type="error",
            error=invalid_tool_call_error(invalid_call),
            protocol_event_type="tool_call.invalid",
            request_id=response.request_id,
            response_id=response.id,
            item_id=invalid_call.id,
        )
        return
    common: dict[str, Any] = {
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
    durable_payload = durable_provider_response_payload(response)
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
            internal={"provider_response": durable_payload},
            **common,
        )
    else:
        yield RunEvent(
            type="error",
            error=response_terminal_error(
                response,
                allow_output_truncated_retry=allow_output_truncated_retry,
            ),
            protocol_event_type=f"response.{response.status}",
            metadata={"provider_response": response_payload},
            internal={"provider_response": durable_payload},
            **common,
        )


def run_events_for_protocol_event(
    event: ProviderStreamEvent,
    *,
    provider: Any | None = None,
    root: Path | None = None,
    user: str = "",
    cancel_event: threading.Event | None = None,
    defer_tool_validation: bool = False,
    allow_output_truncated_retry: bool = False,
) -> Iterator[RunEvent]:
    common: dict[str, Any] = {
        "event_id": event.event_id,
        "sequence": event.sequence,
        "run_sequence": event.run_sequence,
        "request_id": event.request_id,
        "response_id": event.response_id,
        "item_id": event.item_id or "",
        "content_index": event.content_index,
        "protocol_event_type": str(event.type),
    }
    # Kemo 2.0 has no free-form event ``data`` bag.  Event-specific payloads
    # are carried by typed fields (item/delta/usage/response/error), so runtime
    # projection must not depend on the removed 1.x escape hatch.
    metadata: dict[str, Any] = {}
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
        if item is not None and item.parse_error is not None and not defer_tool_validation:
            yield RunEvent(
                type="error",
                error=invalid_tool_call_error(item),
                **common,
            )
            return
        if item is None:
            raise ValueError("Kemo 2.0 tool_call.completed 必须携带完整 ToolCallItem")
        arguments = item.arguments
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
        durable_payload = durable_provider_response_payload(response)
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
                internal={"provider_response": durable_payload},
                **common,
            )
        else:
            yield RunEvent(
                type="error",
                error=response_terminal_error(
                    response,
                    allow_output_truncated_retry=allow_output_truncated_retry,
                ),
                metadata=terminal_metadata,
                internal={"provider_response": durable_payload},
                **common,
            )


def provider_events(
    provider: Any,
    protocol_request: KemoRequest,
    *,
    root: Path | None = None,
    user: str = "",
    cancel_event: threading.Event | None = None,
    on_response: Callable[[KemoResponse], None] | None = None,
    defer_tool_validation: bool = False,
    allow_output_truncated_retry: bool = False,
) -> Generator[RunEvent, None, None]:
    stream: Callable[..., Iterable[ProviderStreamEvent]] | None = getattr(provider, "stream", None)
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
        stream_usage = None
        try:
            for protocol_event in iterator:
                if not isinstance(protocol_event, ProviderStreamEvent):
                    raise EngineError("Provider stream() 必须返回 ProviderStreamEvent")
                if protocol_event.usage is not None:
                    stream_usage = merge_usage_snapshots(protocol_event.usage, stream_usage)
                    protocol_event = protocol_event.model_copy(update={"usage": stream_usage})
                if protocol_event.response is not None:
                    protocol_event = protocol_event.model_copy(update={
                        "response": complete_usage(protocol_request, protocol_event.response, previous=stream_usage),
                    })
                if protocol_event.response is not None and on_response is not None:
                    on_response(protocol_event.response)
                if protocol_event.response is not None:
                    final_usage = protocol_event.response.usage
                    if final_usage != stream_usage:
                        yield RunEvent(
                            type="usage", usage=protocol_usage_dict(final_usage),
                            request_id=protocol_event.response.request_id,
                            response_id=protocol_event.response.id,
                            protocol_event_type="usage.updated",
                        )
                yield from run_events_for_protocol_event(
                    protocol_event,
                    provider=provider,
                    root=root,
                    user=user,
                    cancel_event=cancel_event,
                    defer_tool_validation=defer_tool_validation,
                    allow_output_truncated_retry=allow_output_truncated_retry,
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
    response = complete_usage(protocol_request, response)
    if on_response is not None:
        on_response(response)
    yield from events_for_protocol_response(
        response,
        provider=provider,
        root=root,
        user=user,
        cancel_event=cancel_event,
        defer_tool_validation=defer_tool_validation,
        allow_output_truncated_retry=allow_output_truncated_retry,
    )
