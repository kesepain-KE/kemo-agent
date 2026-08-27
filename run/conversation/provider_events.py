"""Translate Provider protocol responses into stable runtime events."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any, Iterator

from events import RunEvent
from provider.protocol.diagnostics import (
    redact_diagnostic_text,
    sanitize_provider_diagnostic,
)
from provider.protocol.enums import ResponseStatus, StreamEventType
from provider.protocol.models import (
    KemoRequest,
    KemoResponse,
    MessageItem,
    ProviderState,
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
from provider.tool_arguments import MISSING, parse_tool_arguments


_MAX_DURABLE_RESPONSE_CHARS = 256_000
_MAX_DURABLE_ITEM_CHARS = 32_000
_MAX_DURABLE_PROVIDER_STATE_CHARS = 8_192


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


def _bounded_durable_text(value: Any) -> str:
    text = redact_diagnostic_text(str(value or ""))
    if len(text) <= _MAX_DURABLE_ITEM_CHARS:
        return text
    return text[:_MAX_DURABLE_ITEM_CHARS] + "…(历史内容已截断)"


def _bounded_durable_json(value: Any) -> Any:
    bounded = sanitize_provider_diagnostic(value)
    try:
        encoded = json.dumps(
            bounded,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError, RecursionError):
        return {"content_omitted": True, "reason": "durable_diagnostic_error"}
    if len(encoded) <= _MAX_DURABLE_ITEM_CHARS:
        return bounded
    return {
        "content_omitted": True,
        "reason": "durable_item_limit",
        "payload_length": len(encoded),
    }


def _durable_provider_state(value: Any) -> dict[str, Any] | None:
    """Keep the minimum bounded state needed for native reasoning replay.

    Provider state is executable continuation data, not a general diagnostic
    tree.  Only the protocol fields are retained; the opaque payload is
    redacted and bounded so arbitrary metadata or credential-shaped text cannot
    expand durable history without a limit.
    """

    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip()
    provider = str(value.get("provider") or "").strip()
    data = value.get("data")
    if kind not in {"encrypted", "opaque"} or not provider or not isinstance(data, str):
        return None
    safe_data = redact_diagnostic_text(data)
    # Provider continuation state is opaque and cannot be repaired after a
    # partial redaction.  Omit a credential-shaped payload rather than writing
    # a corrupted state that would fail on the next request.
    if safe_data != data:
        return None
    if not safe_data or len(safe_data) > _MAX_DURABLE_PROVIDER_STATE_CHARS:
        return None
    state: dict[str, Any] = {
        "kind": kind,
        "data": safe_data,
        "provider": provider[:160],
    }
    for key in ("model", "version", "expires_at"):
        if value.get(key) is not None:
            state[key] = redact_diagnostic_text(str(value[key]))[:160]
    try:
        ProviderState.model_validate(state)
    except Exception:
        return None
    return state


def _durable_content_blocks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    blocks: list[dict[str, Any]] = []
    for raw in value[:256]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "")
        if kind == "text":
            block = {"type": "text", "text": _bounded_durable_text(raw.get("text"))}
            if raw.get("language") is not None:
                block["language"] = _bounded_durable_text(raw.get("language"))[:128]
            blocks.append(block)
            continue
        if kind == "json":
            blocks.append(
                {
                    "type": "json",
                    "data": _bounded_durable_json(raw.get("data")),
                }
            )
            continue
        if kind in {"image", "audio", "video", "file"}:
            block: dict[str, Any] = {"type": kind}
            for key in (
                "asset_id",
                "mime_type",
                "checksum_sha256",
                "detail",
                "width",
                "height",
                "duration_ms",
                "filename",
            ):
                if raw.get(key) is not None:
                    block[key] = raw[key]
            # Do not persist source.uri/data or any other transient media body.
            blocks.append(block)
            continue
        if kind == "reference":
            block = {"type": "reference", "target_id": str(raw.get("target_id") or "")}
            if raw.get("label") is not None:
                block["label"] = _bounded_durable_text(raw.get("label"))[:256]
            blocks.append(block)
    return blocks


def _durable_item_payload(item: Any) -> dict[str, Any] | None:
    raw = item.model_dump(mode="json", by_alias=True, exclude_none=True)
    kind = str(raw.get("type") or "")
    item_id = str(raw.get("id") or "")
    status = str(raw.get("status") or "completed")
    if not item_id or kind not in {"message", "reasoning", "tool_call", "tool_result"}:
        return None
    base: dict[str, Any] = {"id": item_id, "type": kind, "status": status}
    if kind == "tool_call":
        call_id = str(raw.get("call_id") or "")
        name = str(raw.get("name") or "")
        if not call_id or not name:
            return None
        arguments = _bounded_durable_json(raw.get("arguments") or {})
        base.update(
            {
                "call_id": call_id,
                "name": name,
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        )
        return base
    if kind == "reasoning":
        for key in ("summary", "content"):
            if key in raw:
                base[key] = _bounded_durable_text(raw[key])
        provider_state = _durable_provider_state(raw.get("provider_state"))
        if provider_state is not None:
            base["provider_state"] = provider_state
        if "token_count" in raw:
            base["token_count"] = raw["token_count"]
        # Only the validated, bounded provider_state projection above is kept;
        # arbitrary reasoning metadata and opaque response extensions are not.
        return base
    if kind == "message":
        role = str(raw.get("role") or "assistant")
        content = _durable_content_blocks(raw.get("content") or [])
        base.update(
            {
                "role": role,
                "content": content,
            }
        )
        return base
    call_id = str(raw.get("call_id") or "")
    name = str(raw.get("name") or "")
    if not call_id or not name:
        return None
    content = _durable_content_blocks(raw.get("content") or [])
    base.update(
        {
            "call_id": call_id,
            "name": name,
            "is_error": bool(raw.get("is_error")),
            "content": content,
        }
    )
    return base


def durable_provider_response_payload(response: KemoResponse) -> dict[str, Any]:
    """Return the minimal native response needed to rebuild durable history.

    Public runtime events keep using the bounded diagnostic representation.  The
    history copy must not pass through that formatter because its global budget
    can truncate structural Item fields such as ``type``, ``call_id`` and
    ``name``.  Keeping only the response identity and canonical output Items
    also prevents unrelated Provider metadata from entering conversation
    history through this private channel.
    """

    output: list[dict[str, Any]] = []
    encoded_size = 0
    for item in response.output:
        safe_item = _durable_item_payload(item)
        if safe_item is None:
            continue
        encoded_item = json.dumps(
            safe_item,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        if encoded_size + len(encoded_item) > _MAX_DURABLE_RESPONSE_CHARS:
            if safe_item.get("type") == "tool_call":
                safe_item = {
                    key: value
                    for key, value in safe_item.items()
                    if key != "arguments"
                }
                safe_item["arguments"] = {}
                encoded_item = json.dumps(
                    safe_item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            else:
                continue
        if encoded_size + len(encoded_item) > _MAX_DURABLE_RESPONSE_CHARS:
            continue
        output.append(safe_item)
        encoded_size += len(encoded_item)
    return {
        "id": redact_diagnostic_text(str(response.id or ""))[:160],
        "request_id": redact_diagnostic_text(str(response.request_id or ""))[:160],
        "status": str(response.status),
        "model": redact_diagnostic_text(str(response.model or ""))[:160],
        "output": output,
    }


def metric_provider_response_payload(value: Any) -> dict[str, Any] | None:
    """Project a bounded Provider diagnostic before placing it in history metrics."""

    if not isinstance(value, dict):
        return None

    def project(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                str(key): project(item)
                for key, item in node.items()
                if str(key) not in {"provider_state", "provider_raw"}
            }
        if isinstance(node, list):
            return [project(item) for item in node[:256]]
        return node

    try:
        bounded = sanitize_provider_diagnostic(value)
    except (TypeError, ValueError, RecursionError):
        return None
    projected = project(bounded)
    return projected if isinstance(projected, dict) else None


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
            error=response_terminal_error(response),
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
        if item is not None:
            arguments = item.arguments
        else:
            raw_arguments = (
                event.data["arguments"]
                if isinstance(event.data, dict) and "arguments" in event.data
                else MISSING
            )
            parsed_arguments = parse_tool_arguments(raw_arguments)
            item = ToolCallItem(
                id=event.item_id or f"call_{event.sequence or 0}",
                call_id=event.call_id or f"call_{event.sequence or 0}",
                name=event.name or "unknown_tool",
                arguments=parsed_arguments.arguments,
                arguments_raw=parsed_arguments.arguments_raw,
                parse_error=parsed_arguments.parse_error,
            )
            if item.parse_error is not None:
                yield RunEvent(
                    type="error",
                    error=invalid_tool_call_error(item),
                    **common,
                )
                return
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
                error=response_terminal_error(response),
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
