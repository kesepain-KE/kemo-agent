"""Compatibility mapping between the legacy Chat schema and protocol v1."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Iterator
from typing import Any

from events import RunEvent
from provider.protocol.enums import (
    MeasurementMode,
    MessagePhase,
    MessageRole,
    ResponseStatus,
    StreamEventType,
)
from provider.protocol.errors import CapabilityError
from provider.protocol.models import (
    AudioContent,
    FileContent,
    ImageContent,
    JsonContent,
    KemoRequest,
    KemoResponse,
    Measurement,
    MessageItem,
    ModelCapabilities,
    ReasoningItem,
    ReferenceContent,
    TextContent,
    ToolCallItem,
    ToolDefinition,
    ToolResultItem,
    Usage,
    VideoContent,
    text_from_content,
)
from provider.protocol.streaming import ProviderStreamEvent
from provider.schema import (
    ChatRequest,
    ChatResponse,
    ToolCall,
    Usage as LegacyUsage,
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _legacy_usage(value: LegacyUsage) -> Usage:
    extras = dict(value.extra or {})
    cached = extras.get("cached_tokens")
    if cached is None and isinstance(extras.get("prompt_tokens_details"), dict):
        cached = extras["prompt_tokens_details"].get("cached_tokens")
    reasoning = extras.get("reasoning_tokens")
    if reasoning is None and isinstance(extras.get("completion_tokens_details"), dict):
        reasoning = extras["completion_tokens_details"].get("reasoning_tokens")
    mode = (
        MeasurementMode.ESTIMATED
        if value.estimated
        else MeasurementMode.GATEWAY
        if value.source == "kemo_gateway"
        else MeasurementMode.PROVIDER
    )
    return Usage(
        input_tokens=max(0, int(value.prompt_tokens)),
        cached_input_tokens=max(0, int(cached)) if cached is not None else None,
        output_tokens=max(0, int(value.completion_tokens)),
        reasoning_tokens=max(0, int(reasoning)) if reasoning is not None else None,
        total_tokens=max(0, int(value.total_tokens)),
        measurement=Measurement(
            mode=mode,
            exact=not value.estimated,
            exact_fields=([] if value.estimated else ["input_tokens", "output_tokens", "total_tokens"]),
            estimated_fields=(["input_tokens", "output_tokens", "total_tokens"] if value.estimated else []),
        ),
        provider_raw=extras,
    )


def _chat_usage(value: Usage) -> LegacyUsage:
    return LegacyUsage(
        prompt_tokens=int(value.input_tokens or 0),
        completion_tokens=int(value.output_tokens or 0),
        total_tokens=int(value.total_tokens or 0),
        estimated=value.measurement.mode in {MeasurementMode.ESTIMATED, MeasurementMode.MIXED},
        source=str(value.measurement.mode),
        extra={
            "cached_tokens": value.cached_input_tokens,
            "reasoning_tokens": value.reasoning_tokens,
            "stages": [item.model_dump(mode="json", exclude_none=True) for item in value.stages],
        },
    )


def _tool_definition(value: dict[str, Any]) -> ToolDefinition:
    function = value.get("function") if isinstance(value.get("function"), dict) else value
    return ToolDefinition(
        name=str(function.get("name") or ""),
        description=str(function.get("description") or ""),
        parameters=dict(function.get("parameters") or function.get("input_schema") or {"type": "object"}),
        strict=bool(function.get("strict", True)),
        permission=(str(value.get("permission")) if value.get("permission") else None),
    )


def _tool_schema(value: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": value.name,
            "description": value.description,
            "parameters": value.parameters,
            "strict": value.strict,
        },
    }


def _source_url(content: ImageContent) -> str | None:
    if content.source is None:
        return None
    if content.source.kind in {"url", "data_url", "object_store"}:
        return content.source.uri
    if content.source.kind == "inline_base64":
        mime = content.mime_type or "image/png"
        return f"data:{mime};base64,{content.source.data}"
    return None


def _message_content(content: list[Any]) -> str | list[dict[str, Any]]:
    if all(isinstance(item, TextContent) for item in content):
        return text_from_content(content)
    blocks: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, TextContent):
            blocks.append({"type": "text", "text": item.text})
        elif isinstance(item, ImageContent):
            url = _source_url(item)
            if not url:
                raise CapabilityError(
                    f"OpenAI Chat Adapter 无法解析图片：{item.asset_id or 'unknown'}"
                )
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": url, "detail": item.detail},
                }
            )
        elif isinstance(item, AudioContent) and item.transcript:
            blocks.append({"type": "text", "text": f"[audio transcript]\n{item.transcript}"})
        elif isinstance(item, JsonContent):
            blocks.append(
                {
                    "type": "text",
                    "text": json.dumps(item.data, ensure_ascii=False, default=str),
                }
            )
        elif isinstance(item, ReferenceContent):
            blocks.append({"type": "text", "text": f"[reference:{item.target_id}] {item.label or ''}"})
        elif isinstance(item, (AudioContent, VideoContent, FileContent)):
            raise CapabilityError(
                f"OpenAI Chat Adapter 不支持未派生的 {item.type} 内容：{item.asset_id or 'unknown'}"
            )
    return blocks


def kemo_request_to_chat(request: KemoRequest) -> ChatRequest:
    messages: list[dict[str, Any]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    pending_reasoning = ""
    index = 0
    while index < len(request.input):
        item = request.input[index]
        if isinstance(item, ReasoningItem):
            pending_reasoning += item.content or item.summary or ""
            index += 1
            continue
        if isinstance(item, MessageItem):
            message: dict[str, Any] = {
                "role": str(item.role),
                "content": _message_content(item.content),
            }
            if pending_reasoning and item.role == MessageRole.ASSISTANT:
                message["reasoning_content"] = pending_reasoning
                pending_reasoning = ""
            messages.append(message)
            index += 1
            continue
        if isinstance(item, ToolCallItem):
            calls: list[dict[str, Any]] = []
            while index < len(request.input) and isinstance(request.input[index], ToolCallItem):
                call = request.input[index]
                calls.append(
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments_raw
                            or json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                )
                index += 1
            message = {"role": "assistant", "content": None, "tool_calls": calls}
            if pending_reasoning:
                message["reasoning_content"] = pending_reasoning
                pending_reasoning = ""
            messages.append(message)
            continue
        if isinstance(item, ToolResultItem):
            texts: list[str] = []
            for content in item.content:
                if isinstance(content, TextContent):
                    texts.append(content.text)
                elif isinstance(content, JsonContent):
                    texts.append(json.dumps(content.data, ensure_ascii=False, default=str))
                elif isinstance(content, ReferenceContent):
                    texts.append(f"[reference:{content.target_id}] {content.label or ''}")
                else:
                    texts.append(
                        json.dumps(
                            content.model_dump(mode="json", exclude_none=True),
                            ensure_ascii=False,
                        )
                    )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.call_id,
                    "name": item.name,
                    "content": "\n".join(texts),
                }
            )
            index += 1
            continue
        index += 1
    return ChatRequest(
        model=request.model,
        messages=messages,
        stream=request.stream,
        tools=[_tool_schema(item) for item in request.tools] or None,
        temperature=request.generation.temperature,
        max_tokens=request.generation.max_output_tokens,
        extra=dict(request.provider_options),
    )


def _content_blocks(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [TextContent(text=value)]
    blocks: list[Any] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "")
        if kind in {"text", "input_text", "output_text"}:
            blocks.append(TextContent(text=str(raw.get("text") or "")))
        elif kind == "image":
            blocks.append(ImageContent.model_validate(raw))
        elif kind == "audio":
            blocks.append(AudioContent.model_validate(raw))
        elif kind == "video":
            blocks.append(VideoContent.model_validate(raw))
        elif kind == "file":
            blocks.append(FileContent.model_validate(raw))
        elif kind == "json":
            blocks.append(JsonContent.model_validate(raw))
        elif kind == "reference":
            blocks.append(ReferenceContent.model_validate(raw))
        elif kind in {"image_url", "input_image"}:
            image = raw.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if isinstance(url, str) and url:
                source_kind = "data_url" if url.startswith("data:") else "url"
                blocks.append(
                    ImageContent(
                        source={"kind": source_kind, "uri": url},
                        detail=(image.get("detail", "auto") if isinstance(image, dict) else "auto"),
                    )
                )
    return blocks or [TextContent(text="")]


def chat_request_to_kemo(request: ChatRequest) -> KemoRequest:
    system_parts: list[str] = []
    items: list[Any] = []
    for raw in request.messages:
        role = str(raw.get("role") or "")
        if role in {"system", "developer"}:
            system_parts.append(str(raw.get("content") or ""))
            continue
        native_reasoning = raw.get("_kemo_reasoning")
        native_message = raw.get("_kemo_message")
        reasoning = str(raw.get("reasoning_content") or "")
        if isinstance(native_reasoning, dict):
            items.append(ReasoningItem.model_validate(native_reasoning))
        elif reasoning:
            items.append(ReasoningItem(id=_id("rs"), content=reasoning))
        if role in {"user", "assistant"} and raw.get("content") not in (None, ""):
            if isinstance(native_message, dict):
                items.append(MessageItem.model_validate(native_message))
            else:
                items.append(
                    MessageItem(
                        id=_id("msg"),
                        role=role,
                        phase=(MessagePhase.COMMENTARY if role == "assistant" else None),
                        content=_content_blocks(raw.get("content")),
                    )
                )
        for raw_call in raw.get("tool_calls") or []:
            function = raw_call.get("function") if isinstance(raw_call, dict) else {}
            arguments_raw = str(function.get("arguments") or "{}")
            try:
                arguments = json.loads(arguments_raw)
                if not isinstance(arguments, dict):
                    arguments = {"_value": arguments}
                parse_error = None
            except json.JSONDecodeError as exc:
                arguments = {}
                parse_error = {"message": str(exc)}
            items.append(
                ToolCallItem(
                    id=_id("call"),
                    call_id=str(raw_call.get("id") or _id("callid")),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                    arguments_raw=arguments_raw,
                    parse_error=parse_error,
                )
            )
        if role == "tool":
            content = raw.get("content")
            try:
                parsed = json.loads(content) if isinstance(content, str) else content
                blocks = [JsonContent(data=parsed)]
            except json.JSONDecodeError:
                blocks = [TextContent(text=str(content or ""))]
            items.append(
                ToolResultItem(
                    id=_id("result"),
                    call_id=str(raw.get("tool_call_id") or ""),
                    name=str(raw.get("name") or "unknown_tool"),
                    content=blocks,
                )
            )
    return KemoRequest(
        model=request.model,
        stream=request.stream,
        system_prompt="\n\n".join(part for part in system_parts if part),
        input=items,
        tools=[_tool_definition(item) for item in (request.tools or [])],
        generation={
            "max_output_tokens": request.max_tokens,
            "temperature": request.temperature,
        },
        provider_options=dict(request.extra),
    )


def chat_response_to_kemo(response: ChatResponse, request: KemoRequest) -> KemoResponse:
    output: list[Any] = []
    if response.reasoning:
        output.append(ReasoningItem(id=_id("rs"), content=response.reasoning))
    if response.text:
        output.append(
            MessageItem.text(
                MessageRole.ASSISTANT,
                response.text,
                phase=(MessagePhase.COMMENTARY if response.tool_calls else MessagePhase.FINAL_ANSWER),
            )
        )
    for call in response.tool_calls:
        output.append(
            ToolCallItem(
                id=_id("call"),
                call_id=call.id or _id("callid"),
                name=call.name,
                arguments=call.arguments,
            )
        )
    return KemoResponse(
        request_id=request.request_id,
        status=(ResponseStatus.REQUIRES_ACTION if response.tool_calls else ResponseStatus.COMPLETED),
        model=response.model or request.model,
        output=output,
        usage=_legacy_usage(response.usage),
        provider_response_id=response.response_id or None,
        metadata={"finish_reason": response.finish_reason},
    )


def kemo_response_to_chat(response: KemoResponse) -> ChatResponse:
    texts: list[str] = []
    reasoning: list[str] = []
    calls: list[ToolCall] = []
    for item in response.output:
        if isinstance(item, ReasoningItem):
            reasoning.append(item.content or item.summary or "")
        elif isinstance(item, MessageItem):
            texts.append(text_from_content(item.content))
        elif isinstance(item, ToolCallItem):
            calls.append(ToolCall(item.call_id, item.name, item.arguments))
    return ChatResponse(
        text="".join(texts),
        reasoning="".join(reasoning),
        tool_calls=calls,
        finish_reason=("tool_calls" if calls else str(response.status)),
        usage=_chat_usage(response.usage),
        model=response.model,
        response_id=response.provider_response_id or response.id,
        raw=response.model_dump(mode="json", by_alias=True, exclude_none=True),
    )


def legacy_stream_to_protocol(
    events: Iterable[RunEvent],
    request: KemoRequest,
    *,
    capabilities: ModelCapabilities | None = None,
) -> Iterator[ProviderStreamEvent]:
    del capabilities
    response_id = _id("resp")
    sequence = 0
    yield ProviderStreamEvent(
        type=StreamEventType.RESPONSE_CREATED,
        sequence=sequence,
        request_id=request.request_id,
        response_id=response_id,
        data={"model": request.model},
    )
    sequence += 1
    reasoning_id = _id("rs")
    message_id = _id("msg")
    reasoning_parts: list[str] = []
    text_parts: list[str] = []
    calls: list[ToolCallItem] = []
    usage = Usage()
    reasoning_added = False
    message_added = False
    for event in events:
        if event.type == "reasoning_delta":
            if not reasoning_added:
                yield ProviderStreamEvent(
                    type=StreamEventType.OUTPUT_ITEM_ADDED,
                    sequence=sequence,
                    request_id=request.request_id,
                    response_id=response_id,
                    item_id=reasoning_id,
                    data={"item_type": "reasoning"},
                )
                sequence += 1
                reasoning_added = True
            reasoning_parts.append(event.content)
            yield ProviderStreamEvent(
                type=StreamEventType.REASONING_CONTENT_DELTA,
                sequence=sequence,
                request_id=request.request_id,
                response_id=response_id,
                item_id=reasoning_id,
                delta=event.content,
            )
            sequence += 1
        elif event.type == "text_delta":
            if not message_added:
                yield ProviderStreamEvent(
                    type=StreamEventType.OUTPUT_ITEM_ADDED,
                    sequence=sequence,
                    request_id=request.request_id,
                    response_id=response_id,
                    item_id=message_id,
                    data={"item_type": "message"},
                )
                sequence += 1
                message_added = True
            text_parts.append(event.content)
            yield ProviderStreamEvent(
                type=StreamEventType.OUTPUT_TEXT_DELTA,
                sequence=sequence,
                request_id=request.request_id,
                response_id=response_id,
                item_id=message_id,
                content_index=0,
                delta=event.content,
            )
            sequence += 1
        elif event.type == "tool_call_start":
            item = ToolCallItem(
                id=_id("call"),
                call_id=event.tool_call_id or _id("callid"),
                name=event.tool_name,
                arguments=event.arguments or {},
                arguments_raw=str(event.metadata.get("raw_arguments") or "") or None,
            )
            calls.append(item)
            yield ProviderStreamEvent(
                type=StreamEventType.TOOL_CALL_COMPLETED,
                sequence=sequence,
                request_id=request.request_id,
                response_id=response_id,
                item_id=item.id,
                call_id=item.call_id,
                name=item.name,
                item=item,
            )
            sequence += 1
        elif event.type == "usage":
            raw = event.usage or {}
            usage = Usage(
                input_tokens=raw.get("prompt_tokens"),
                output_tokens=raw.get("completion_tokens"),
                total_tokens=raw.get("total_tokens"),
                measurement=Measurement(
                    mode=(MeasurementMode.ESTIMATED if raw.get("estimated") else MeasurementMode.PROVIDER),
                    exact=not bool(raw.get("estimated")),
                ),
                provider_raw=dict(raw),
            )
            yield ProviderStreamEvent(
                type=StreamEventType.USAGE_UPDATED,
                sequence=sequence,
                request_id=request.request_id,
                response_id=response_id,
                usage=usage,
            )
            sequence += 1
        elif event.type == "error":
            raw_error = event.error or {}
            from provider.protocol.models import UnifiedError

            yield ProviderStreamEvent(
                type=StreamEventType.ERROR,
                sequence=sequence,
                request_id=request.request_id,
                response_id=response_id,
                error=UnifiedError(
                    type=str(raw_error.get("exception_type") or "provider_error"),
                    code=str(raw_error.get("code") or "PROVIDER_ERROR"),
                    message=str(raw_error.get("message") or "Provider stream failed"),
                    details=dict(raw_error),
                ),
            )
            return
    output: list[Any] = []
    if reasoning_parts:
        output.append(ReasoningItem(id=reasoning_id, content="".join(reasoning_parts)))
    if text_parts:
        output.append(
            MessageItem.text(
                MessageRole.ASSISTANT,
                "".join(text_parts),
                phase=(MessagePhase.COMMENTARY if calls else MessagePhase.FINAL_ANSWER),
                item_id=message_id,
            )
        )
    output.extend(calls)
    response = KemoResponse(
        id=response_id,
        request_id=request.request_id,
        status=(ResponseStatus.REQUIRES_ACTION if calls else ResponseStatus.COMPLETED),
        model=request.model,
        output=output,
        usage=usage,
    )
    yield ProviderStreamEvent(
        type=StreamEventType.RESPONSE_COMPLETED,
        sequence=sequence,
        request_id=request.request_id,
        response_id=response_id,
        response=response,
    )
