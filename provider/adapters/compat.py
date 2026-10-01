"""Mapping between the standard Chat Completions schema and Kemo protocol 2.0."""

from __future__ import annotations

import copy
import json
import re
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
from provider.protocol.diagnostics import (
    invalid_tool_call_diagnostic,
    sanitize_provider_diagnostic,
    tool_arguments_diagnostic,
)
from provider.protocol.models import (
    Annotation,
    AudioContent,
    FileContent,
    ImageContent,
    JsonContent,
    KemoRequest,
    KemoResponse,
    Measurement,
    MessageItem,
    MessageLogprobs,
    ModelCapabilities,
    ReasoningConfig,
    ReasoningItem,
    ReferenceContent,
    TextContent,
    ToolCallItem,
    ToolDefinition,
    ToolResultItem,
    Usage,
    VideoContent,
    validate_structured_output_text,
    normalize_reasoning_effort,
    normalize_kemo_reasoning_effort,
    text_from_content,
)
from provider.protocol.validation import validate_provider_options
from provider.protocol.streaming import (
    MessageItemStart,
    ProviderStreamEvent,
    ReasoningItemStart,
    ToolCallItemStart,
)
from provider.schema import (
    ChatRequest,
    ChatResponse,
    ToolCall,
    Usage as ChatUsage,
)
from provider.tool_arguments import MISSING, parse_tool_arguments


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


_INCOMPLETE_CHAT_FINISH_REASONS = frozenset(
    {
        "length",
        "max_tokens",
        "max_output_tokens",
        "content_filter",
    }
)
_TOOL_CHAT_FINISH_REASONS = frozenset({"tool_calls", "function_call"})


def _chat_incomplete_details(
    finish_reason: str,
    *,
    calls: list[ToolCallItem],
    invalid_calls: list[ToolCallItem],
    invalid_call_diagnostics: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    normalized = str(finish_reason or "").strip().casefold()
    if normalized in _INCOMPLETE_CHAT_FINISH_REASONS:
        reason = "content_filtered" if normalized == "content_filter" else "output_truncated"
        detail_payload: dict[str, Any] = {"finish_reason": normalized}
    elif invalid_calls:
        reason = "invalid_tool_arguments"
        detail_payload = {"finish_reason": normalized or None}
    elif normalized in _TOOL_CHAT_FINISH_REASONS and not calls:
        reason = "missing_tool_call"
        detail_payload = {"finish_reason": normalized}
    else:
        return None
    if invalid_calls:
        detail_payload["invalid_tool_calls"] = [
            invalid_tool_call_diagnostic(
                call_id=item.call_id,
                name=item.name,
                raw_arguments=item.arguments_raw,
                parse_error=item.parse_error,
                arguments_diagnostic=(invalid_call_diagnostics or {}).get(
                    item.call_id
                ),
            )
            for item in invalid_calls
        ]
    return {"reason": reason, "details": detail_payload}


def _unique_id(value: Any, prefix: str, used: set[str]) -> str:
    """Keep valid Kemo IDs; map external IDs to collision-free internal IDs."""
    candidate = str(value or "")
    if (
        not candidate.startswith(f"{prefix}_")
        or candidate in used
        or len(candidate) > 128
        or re.fullmatch(r"[A-Za-z0-9_-]+", candidate[len(prefix) + 1 :]) is None
    ):
        candidate = _id(prefix)
        while candidate in used:
            candidate = _id(prefix)
    used.add(candidate)
    return candidate


def _protocol_usage_from_chat(value: ChatUsage) -> Usage:
    extras = dict(value.extra or {})
    prompt_details = extras.get("prompt_tokens_details")
    completion_details = extras.get("completion_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    completion_details = completion_details if isinstance(completion_details, dict) else {}
    cached = next(
        (
            extras.get(key)
            for key in (
                "cached_tokens",
                "cached_prompt_tokens",
                "cache_read_input_tokens",
                "prompt_cache_hit_tokens",
            )
            if extras.get(key) is not None
        ),
        None,
    )
    if cached is None:
        cached = prompt_details.get("cached_tokens")
    reasoning = extras.get("reasoning_tokens")
    if reasoning is None:
        reasoning = completion_details.get("reasoning_tokens")
    audio_input = prompt_details.get("audio_tokens", extras.get("audio_input_tokens"))
    audio_output = completion_details.get("audio_tokens", extras.get("audio_output_tokens"))
    accepted = completion_details.get(
        "accepted_prediction_tokens", extras.get("accepted_prediction_tokens")
    )
    rejected = completion_details.get(
        "rejected_prediction_tokens", extras.get("rejected_prediction_tokens")
    )
    mode = (
        MeasurementMode.ESTIMATED
        if value.estimated
        else MeasurementMode.PROVIDER
    )
    exact_fields = [] if value.estimated else [
        name
        for name, raw in (
            ("input_tokens", value.prompt_tokens),
            ("output_tokens", value.completion_tokens),
            ("total_tokens", value.total_tokens),
        )
        if raw is not None
    ]
    return Usage(
        input_tokens=max(0, int(value.prompt_tokens)) if value.prompt_tokens is not None else None,
        cached_input_tokens=max(0, int(cached)) if cached is not None else None,
        output_tokens=max(0, int(value.completion_tokens)) if value.completion_tokens is not None else None,
        reasoning_tokens=max(0, int(reasoning)) if reasoning is not None else None,
        audio_input_tokens=max(0, int(audio_input)) if audio_input is not None else None,
        audio_output_tokens=max(0, int(audio_output)) if audio_output is not None else None,
        accepted_prediction_tokens=max(0, int(accepted)) if accepted is not None else None,
        rejected_prediction_tokens=max(0, int(rejected)) if rejected is not None else None,
        total_tokens=max(0, int(value.total_tokens)) if value.total_tokens is not None else None,
        measurement=Measurement(
            mode=mode,
            exact=not value.estimated,
            exact_fields=exact_fields,
            estimated_fields=(["input_tokens", "output_tokens", "total_tokens"] if value.estimated else []),
        ),
        provider_raw=extras,
    )


def _chat_usage_from_raw(raw: dict[str, Any]) -> ChatUsage:
    """Normalize one Chat Completions usage object before protocol bridging."""

    prompt_tokens = max(0, int(raw.get("prompt_tokens") or 0))
    completion_tokens = max(0, int(raw.get("completion_tokens") or 0))
    total_value = raw.get("total_tokens")
    total_tokens = max(
        0,
        int(
            total_value
            if total_value is not None
            else prompt_tokens + completion_tokens
        ),
    )
    known = {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated",
        "source",
    }
    return ChatUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated=bool(raw.get("estimated", False)),
        source=str(raw.get("source") or "provider"),
        extra={key: value for key, value in raw.items() if key not in known},
    )


def _chat_usage(value: Usage) -> ChatUsage:
    return ChatUsage(
        prompt_tokens=int(value.input_tokens or 0),
        completion_tokens=int(value.output_tokens or 0),
        total_tokens=int(value.total_tokens or 0),
        estimated=value.measurement.mode in {MeasurementMode.ESTIMATED, MeasurementMode.MIXED},
        source=str(value.measurement.mode),
        extra={
            "cached_tokens": value.cached_input_tokens,
            "reasoning_tokens": value.reasoning_tokens,
            "audio_input_tokens": value.audio_input_tokens,
            "audio_output_tokens": value.audio_output_tokens,
            "accepted_prediction_tokens": value.accepted_prediction_tokens,
            "rejected_prediction_tokens": value.rejected_prediction_tokens,
            "stages": [item.model_dump(mode="json", exclude_none=True) for item in value.stages],
        },
    )


def _protocol_annotations(values: Any) -> list[Annotation]:
    """Normalize provider annotation shapes to the closed Kemo 2.0 model."""

    if not values:
        return []
    result: list[Annotation] = []
    for raw in values if isinstance(values, list) else []:
        if not isinstance(raw, dict):
            raise CapabilityError("Chat provider annotations 必须是对象数组")
        kind = str(raw.get("type") or "")
        nested = raw.get(kind) if isinstance(raw.get(kind), dict) else raw
        payload: dict[str, Any] = {
            "type": kind,
            "content_index": int(raw.get("content_index", 0)),
            "title": nested.get("title"),
            "start_index": nested.get("start_index"),
            "end_index": nested.get("end_index"),
        }
        if kind == "url_citation":
            payload["url"] = nested.get("url")
        elif kind == "file_citation":
            payload["file_id"] = nested.get("file_id") or nested.get("file_citation")
        else:
            raise CapabilityError(f"Chat provider 返回未支持的 annotation 类型：{kind or 'unknown'}")
        try:
            result.append(Annotation.model_validate(payload))
        except Exception as exc:
            raise CapabilityError("Chat provider annotations 不符合 Kemo 2.0 约束") from exc
    return result


def _protocol_logprobs(value: Any) -> MessageLogprobs | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CapabilityError("Chat provider logprobs 必须是对象")
    content = value.get("content")
    if content is None:
        return MessageLogprobs(content=[])
    try:
        return MessageLogprobs.model_validate({"content": content})
    except Exception as exc:
        raise CapabilityError("Chat provider logprobs 不符合 Kemo 2.0 约束") from exc


def _tool_definition(value: dict[str, Any]) -> ToolDefinition:
    function = value.get("function") if isinstance(value.get("function"), dict) else value
    return ToolDefinition(
        name=str(function.get("name") or ""),
        description=str(function.get("description") or ""),
        parameters=dict(function.get("parameters") or function.get("input_schema") or {"type": "object"}),
        # OpenAI-compatible function tools are non-strict unless the caller
        # explicitly opts into the Structured Outputs schema subset.  Treating
        # an omitted flag as strict rejects ordinary open dictionaries such as
        # expand_call.params before the model can start.
        strict=bool(function.get("strict", False)),
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


def kemo_request_to_chat(
    request: KemoRequest,
    *,
    provider_profile: dict[str, Any] | None = None,
) -> ChatRequest:
    transport_profile = (
        provider_profile.get("transport")
        if isinstance(provider_profile, dict)
        and isinstance(provider_profile.get("transport"), dict)
        else {}
    )
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
            if item.refusal is not None:
                message["refusal"] = item.refusal
            if item.annotations:
                message["annotations"] = [
                    annotation.model_dump(mode="json", exclude_none=True)
                    for annotation in item.annotations
                ]
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
    # Vendor options are never forwarded without an explicit profile
    # allow-list.  Adapter-internal bookkeeping keys from Chat→Kemo are not
    # vendor options and are retained only for the reverse bridge mapping.
    vendor_options = {
        key: value
        for key, value in request.provider_options.items()
        if isinstance(key, str) and key.startswith("vendor.")
    }
    internal_options = {
        key: value
        for key, value in request.provider_options.items()
        if key not in vendor_options
    }
    extra = dict(internal_options)
    extra.update(validate_provider_options(vendor_options, provider_profile))
    if request.reasoning is not None and request.reasoning.enabled:
        extra["reasoning_effort"] = normalize_reasoning_effort(
            request.reasoning.effort
        )
    generation = request.generation
    for key in (
        "top_p",
        "seed",
        "logit_bias",
        "presence_penalty",
        "frequency_penalty",
        "verbosity",
    ):
        value = getattr(generation, key)
        if value is not None:
            extra[key] = value
    if generation.stop is not None:
        extra["stop"] = generation.stop
    if generation.n != 1:
        extra["n"] = generation.n
    if generation.logprobs is not None and generation.logprobs.enabled:
        extra["logprobs"] = True
        extra["top_logprobs"] = generation.logprobs.top_k
    if request.tools:
        extra["parallel_tool_calls"] = request.parallel_tool_calls
        if request.tool_choice.mode == "none":
            extra["tool_choice"] = "none"
        elif request.tool_choice.mode in {"auto", "required"}:
            extra["tool_choice"] = request.tool_choice.mode
        elif request.tool_choice.mode == "named":
            extra["tool_choice"] = {
                "type": "function",
                "function": {"name": request.tool_choice.name},
            }
        elif request.tool_choice.mode == "allowed":
            extra["allowed_tools"] = {
                "mode": request.tool_choice.allowed_mode,
                "tools": [
                    {"type": "function", "function": {"name": name}}
                    for name in request.tool_choice.allowed_tools or []
                ],
            }
    if request.structured_output is not None:
        if request.structured_output.type == "json_object":
            extra["response_format"] = {"type": "json_object"}
        else:
            extra["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.structured_output.schema_name,
                    "schema": request.structured_output.schema_,
                    "strict": request.structured_output.strict,
                },
            }
    if request.prediction is not None:
        extra["prediction"] = request.prediction.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
    if request.service.tier is not None:
        extra["service_tier"] = request.service.tier
    if request.service.prompt_cache_key is not None:
        extra["prompt_cache_key"] = request.service.prompt_cache_key
    if request.service.safety_identifier is not None:
        extra["safety_identifier"] = request.service.safety_identifier
    if request.service.store is not None:
        extra["store"] = request.service.store
    if request.metadata:
        metadata_limit = transport_profile.get("metadata_limit")
        if metadata_limit is not None:
            try:
                metadata_limit = int(metadata_limit)
            except (TypeError, ValueError):
                raise CapabilityError("provider profile 的 metadata_limit 无效")
            if metadata_limit < 0 or len(request.metadata) > metadata_limit:
                raise CapabilityError("metadata 超过上游 profile 声明的容量")
        if transport_profile.get("metadata_supported", True) is False:
            raise CapabilityError("上游 profile 不支持 metadata")
        extra["metadata"] = copy.deepcopy(request.metadata)
    if request.generation.max_output_tokens is not None:
        max_field = str(
            transport_profile.get("max_output_tokens_field") or "max_tokens"
        )
        if max_field not in {"max_tokens", "max_completion_tokens"}:
            raise CapabilityError("上游 profile 的 max_output_tokens_field 无效")
        if max_field != "max_tokens":
            extra[max_field] = request.generation.max_output_tokens
    return ChatRequest(
        model=request.model,
        messages=messages,
        stream=request.stream,
        tools=[_tool_schema(item) for item in request.tools] or None,
        temperature=request.generation.temperature,
        max_tokens=(
            request.generation.max_output_tokens
            if not transport_profile.get("max_output_tokens_field")
            or transport_profile.get("max_output_tokens_field") == "max_tokens"
            else None
        ),
        extra=extra,
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
    item_ids: set[str] = set()
    call_ids: set[str] = set()
    pending_call_ids: dict[str, list[str]] = {}
    for raw in request.messages:
        role = str(raw.get("role") or "")
        if role == "system":
            system_parts.append(str(raw.get("content") or ""))
            continue
        native_reasoning = raw.get("_kemo_reasoning")
        native_message = raw.get("_kemo_message")
        reasoning = str(raw.get("reasoning_content") or "")
        if isinstance(native_reasoning, dict):
            native_reasoning = copy.deepcopy(native_reasoning)
            native_reasoning.pop("metadata", None)
            native_reasoning.pop("extensions", None)
            native_reasoning["id"] = _unique_id(
                native_reasoning.get("id"), "rs", item_ids
            )
            content = native_reasoning.get("content")
            summary = native_reasoning.get("summary")
            provider_state = native_reasoning.get("provider_state")
            if content or summary or provider_state:
                items.append(ReasoningItem.model_validate(native_reasoning))
        elif reasoning:
            items.append(
                ReasoningItem(
                    id=_unique_id(None, "rs", item_ids), content=reasoning
                )
            )
        if role in {"user", "developer", "assistant"} and (
            raw.get("content") not in (None, "")
            or raw.get("refusal") not in (None, "")
        ):
            native_content = (
                native_message.get("content")
                if isinstance(native_message, dict)
                else None
            )
            if isinstance(native_content, list) and native_content:
                native_message_copy = copy.deepcopy(native_message)
                native_message_copy.pop("metadata", None)
                native_message_copy.pop("extensions", None)
                native_message_copy["id"] = _unique_id(
                    native_message_copy.get("id"), "msg", item_ids
                )
                items.append(MessageItem.model_validate(native_message_copy))
            else:
                try:
                    annotations = _protocol_annotations(raw.get("annotations"))
                    logprobs = _protocol_logprobs(raw.get("logprobs"))
                    items.append(
                        MessageItem(
                            id=_unique_id(None, "msg", item_ids),
                            role=role,
                            phase=(MessagePhase.COMMENTARY if role == "assistant" else None),
                            content=_content_blocks(raw.get("content")) if raw.get("content") not in (None, "") else [],
                            refusal=(str(raw.get("refusal")) if raw.get("refusal") else None),
                            annotations=annotations,
                            logprobs=logprobs,
                        )
                    )
                except ValueError as exc:
                    raise CapabilityError("Chat message 无法转换为 Kemo MessageItem") from exc
        for raw_call in raw.get("tool_calls") or []:
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function")
            if not isinstance(function, dict):
                function = {}
            parsed_arguments = parse_tool_arguments(
                function["arguments"] if "arguments" in function else MISSING
            )
            arguments = parsed_arguments.arguments
            arguments_raw = parsed_arguments.arguments_raw
            parse_error = parsed_arguments.parse_error
            original_call_id = str(raw_call.get("id") or "")
            call_id = _unique_id(original_call_id, "callid", call_ids)
            if original_call_id:
                pending_call_ids.setdefault(original_call_id, []).append(call_id)
            items.append(
                ToolCallItem(
                    id=_unique_id(None, "call", item_ids),
                    call_id=call_id,
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
            original_call_id = str(raw.get("tool_call_id") or "")
            call_queue = pending_call_ids.get(original_call_id) or []
            call_id = call_queue.pop(0) if call_queue else original_call_id
            items.append(
                ToolResultItem(
                    id=_unique_id(None, "result", item_ids),
                    call_id=call_id,
                    name=str(raw.get("name") or "unknown_tool"),
                    content=blocks,
                )
            )
    reasoning_enabled = request.extra.get("reasoning_enabled") is not False
    effort = normalize_kemo_reasoning_effort(request.extra.get("reasoning_effort"))
    mapped_chat_fields = {
        "reasoning_enabled", "reasoning_effort", "top_p", "seed", "logit_bias",
        "presence_penalty", "frequency_penalty", "verbosity", "stop", "n",
        "logprobs", "top_logprobs", "tool_choice", "allowed_tools",
        "parallel_tool_calls", "response_format", "prediction", "service_tier",
        "prompt_cache_key", "safety_identifier", "store",
        "metadata",
    }
    provider_options = {
        key: value
        for key, value in request.extra.items()
        if key not in mapped_chat_fields
    }
    if reasoning_enabled:
        provider_options["reasoning_effort"] = effort
    generation: dict[str, Any] = {
        "max_output_tokens": request.max_tokens,
        "temperature": request.temperature,
    }
    for key in (
        "top_p",
        "seed",
        "logit_bias",
        "presence_penalty",
        "frequency_penalty",
        "verbosity",
        "stop",
        "n",
    ):
        if request.extra.get(key) is not None:
            generation[key] = request.extra[key]
    if request.extra.get("logprobs") is True:
        generation["logprobs"] = {
            "enabled": True,
            "top_k": int(request.extra.get("top_logprobs") or 0),
        }
    tool_choice: dict[str, Any] = {"mode": "auto"}
    raw_choice = request.extra.get("tool_choice")
    if raw_choice in {"auto", "none", "required"}:
        tool_choice = {"mode": raw_choice}
    elif isinstance(raw_choice, dict):
        function = raw_choice.get("function")
        if raw_choice.get("type") == "function" and isinstance(function, dict):
            tool_choice = {"mode": "named", "name": function.get("name")}
    raw_allowed = request.extra.get("allowed_tools")
    if isinstance(raw_allowed, dict):
        names = [
            str(item.get("function", {}).get("name") or "")
            for item in raw_allowed.get("tools", [])
            if isinstance(item, dict) and isinstance(item.get("function"), dict)
        ]
        tool_choice = {
            "mode": "allowed",
            "allowed_tools": [name for name in names if name],
            "allowed_mode": raw_allowed.get("mode"),
        }
    structured_output = None
    response_format = request.extra.get("response_format")
    if isinstance(response_format, dict):
        if response_format.get("type") == "json_object":
            structured_output = {"type": "json_object"}
        elif response_format.get("type") == "json_schema" and isinstance(
            response_format.get("json_schema"), dict
        ):
            schema = response_format["json_schema"]
            structured_output = {
                "type": "json_schema",
                "schema_name": schema.get("name"),
                "schema": schema.get("schema"),
                "strict": bool(schema.get("strict", False)),
            }
    return KemoRequest.create(
        model=request.model,
        stream=request.stream,
        system_prompt="\n\n".join(part for part in system_parts if part),
        input=items,
        tools=[_tool_definition(item) for item in (request.tools or [])],
        generation=generation,
        tool_choice=tool_choice,
        parallel_tool_calls=bool(request.extra.get("parallel_tool_calls", True)),
        structured_output=structured_output,
        prediction=request.extra.get("prediction"),
        service={
            "tier": request.extra.get("service_tier"),
            "prompt_cache_key": request.extra.get("prompt_cache_key"),
            "safety_identifier": request.extra.get("safety_identifier"),
            "store": request.extra.get("store"),
        },
        reasoning=(
            ReasoningConfig(
                enabled=True,
                effort=effort,
                return_mode="content",
                context="auto",
            )
            if reasoning_enabled
            else None
        ),
        provider_options=provider_options,
        metadata=(
            dict(request.extra.get("metadata"))
            if isinstance(request.extra.get("metadata"), dict)
            else {}
        ),
    )


def chat_response_to_kemo(response: ChatResponse, request: KemoRequest) -> KemoResponse:
    output: list[Any] = []
    call_ids: set[str] = set()
    call_items = []
    for call in response.tool_calls:
        call_items.append(
            ToolCallItem(
                id=_id("call"),
                call_id=_unique_id(call.id, "callid", call_ids),
                name=call.name,
                arguments=call.arguments,
                arguments_raw=call.arguments_raw,
                parse_error=copy.deepcopy(call.parse_error),
            )
        )
    invalid_calls = [item for item in call_items if item.parse_error is not None]
    incomplete_details = _chat_incomplete_details(
        response.finish_reason,
        calls=call_items,
        invalid_calls=invalid_calls,
    )
    if incomplete_details is None and request.structured_output is not None and response.text:
        structured_error = validate_structured_output_text(
            response.text, request.structured_output
        )
        if structured_error:
            incomplete_details = {
                "reason": "other",
                "message": "structured output 校验失败",
                "details": {"kind": "structured_output_invalid", "error": structured_error},
            }
    executable_calls = call_items if incomplete_details is None else []
    if response.reasoning:
        output.append(ReasoningItem(id=_id("rs"), content=response.reasoning))
    annotations = _protocol_annotations(response.annotations)
    logprobs = _protocol_logprobs(response.logprobs)
    if response.text or response.refusal:
        output.append(
            MessageItem(
                id=_id("msg"),
                role=MessageRole.ASSISTANT,
                phase=(
                    MessagePhase.COMMENTARY
                    if response.tool_calls
                    else MessagePhase.FINAL_ANSWER
                ),
                content=([TextContent(text=response.text)] if response.text else []),
                refusal=response.refusal,
                annotations=annotations,
                logprobs=logprobs,
            )
        )
    elif annotations or logprobs:
        raise CapabilityError("Chat provider 返回了无法附着到消息的 annotations/logprobs")
    output.extend(executable_calls)
    status = (
        ResponseStatus.INCOMPLETE
        if incomplete_details is not None
        else (
            ResponseStatus.REQUIRES_ACTION
            if executable_calls
            else (ResponseStatus.COMPLETED if output else ResponseStatus.INCOMPLETE)
        )
    )
    if status == ResponseStatus.INCOMPLETE and incomplete_details is None:
        incomplete_details = {"reason": "empty_output"}
    return KemoResponse(
        protocol_version=request.protocol_version,
        request_id=request.request_id,
        status=status,
        model=response.model or request.model,
        output=output,
        usage=_protocol_usage_from_chat(response.usage),
        incomplete_details=incomplete_details,
        provider_response_id=response.response_id or None,
        system_fingerprint=response.system_fingerprint,
        service=(
            {"tier": response.service_tier}
            if response.service_tier is not None
            else None
        ),
        choice_index=response.choice_index,
        choice_count=response.choice_count,
        extensions={"kemo.diagnostics": {"finish_reason": response.finish_reason}},
    )


def kemo_response_to_chat(response: KemoResponse) -> ChatResponse:
    texts: list[str] = []
    reasoning: list[str] = []
    calls: list[ToolCall] = []
    refusal: str | None = None
    annotations: list[dict[str, Any]] = []
    logprobs: dict[str, Any] | None = None
    for item in response.output:
        if isinstance(item, ReasoningItem):
            reasoning.append(item.content or item.summary or "")
        elif isinstance(item, MessageItem):
            texts.append(text_from_content(item.content))
            refusal = item.refusal
            annotations.extend(
                annotation.model_dump(mode="json", exclude_none=True)
                for annotation in item.annotations
            )
            if item.logprobs is not None:
                logprobs = item.logprobs.model_dump(mode="json", exclude_none=True)
        elif isinstance(item, ToolCallItem):
            calls.append(ToolCall(item.call_id, item.name, item.arguments))
    return ChatResponse(
        text="".join(texts),
        reasoning="".join(reasoning),
        tool_calls=calls,
        refusal=refusal,
        annotations=annotations,
        logprobs=logprobs,
        finish_reason=("tool_calls" if calls else str(response.status)),
        usage=_chat_usage(response.usage),
        model=response.model,
        response_id=response.provider_response_id or response.id,
        system_fingerprint=response.system_fingerprint,
        service_tier=response.service.tier if response.service is not None else None,
        choice_index=response.choice_index,
        choice_count=response.choice_count,
        raw=response.model_dump(mode="json", by_alias=True, exclude_none=True),
    )


def chat_stream_to_protocol(
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
        previous_sequence=None,
        request_id=request.request_id,
        response_id=response_id,
    )
    sequence += 1
    reasoning_id = _id("rs")
    message_id = _id("msg")
    reasoning_parts: list[str] = []
    text_parts: list[str] = []
    refusal_parts: list[str] = []
    stream_annotations: list[dict[str, Any]] = []
    stream_logprobs: dict[str, Any] | None = None
    stream_system_fingerprint: str | None = None
    stream_service_tier: str | None = None
    calls: list[ToolCallItem] = []
    call_ids: set[str] = set()
    invalid_calls: list[ToolCallItem] = []
    invalid_call_diagnostics: dict[str, dict[str, Any]] = {}
    usage = Usage()
    reasoning_added = False
    message_added = False
    finish_reason = ""
    for event in events:
        if event.type == "reasoning_delta":
            if not reasoning_added:
                yield ProviderStreamEvent(
                    type=StreamEventType.OUTPUT_ITEM_ADDED,
                    sequence=sequence,
            previous_sequence=None if sequence == 0 else sequence - 1,
                    request_id=request.request_id,
                    response_id=response_id,
                    item_id=reasoning_id,
                    item=ReasoningItemStart(id=reasoning_id),
                )
                sequence += 1
                reasoning_added = True
            reasoning_parts.append(event.content)
            yield ProviderStreamEvent(
                type=StreamEventType.REASONING_CONTENT_DELTA,
                sequence=sequence,
            previous_sequence=None if sequence == 0 else sequence - 1,
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
            previous_sequence=None if sequence == 0 else sequence - 1,
                    request_id=request.request_id,
                    response_id=response_id,
                    item_id=message_id,
                    item=MessageItemStart(
                        id=message_id,
                        phase=MessagePhase.FINAL_ANSWER,
                    ),
                )
                sequence += 1
                message_added = True
            text_parts.append(event.content)
            yield ProviderStreamEvent(
                type=StreamEventType.OUTPUT_TEXT_DELTA,
                sequence=sequence,
            previous_sequence=None if sequence == 0 else sequence - 1,
                request_id=request.request_id,
                response_id=response_id,
                item_id=message_id,
                content_index=0,
                delta=event.content,
            )
            sequence += 1
        elif event.type == "tool_call_start":
            legacy_raw_arguments = str(
                event.metadata.get("raw_arguments") or ""
            ) or None
            item = ToolCallItem(
                id=_id("call"),
                call_id=_unique_id(event.tool_call_id, "callid", call_ids),
                name=event.tool_name,
                arguments=event.arguments or {},
                arguments_raw=legacy_raw_arguments,
                parse_error=(
                    copy.deepcopy(event.metadata.get("parse_error"))
                    if isinstance(event.metadata.get("parse_error"), dict)
                    else None
                ),
            )
            yield ProviderStreamEvent(
                type=StreamEventType.OUTPUT_ITEM_ADDED,
                sequence=sequence,
                previous_sequence=None if sequence == 0 else sequence - 1,
                    request_id=request.request_id,
                    response_id=response_id,
                    item_id=item.id,
                    item=ToolCallItemStart(
                    id=item.id,
                    call_id=item.call_id,
                    name=item.name,
                ),
            )
            sequence += 1
            calls.append(item)
            if item.parse_error is not None:
                invalid_calls.append(item)
                invalid_call_diagnostics[item.call_id] = tool_arguments_diagnostic(
                    legacy_raw_arguments,
                    diagnostic=event.metadata.get("arguments_diagnostic"),
                )
        elif event.type == "usage":
            raw = event.usage or {}
            usage = _protocol_usage_from_chat(_chat_usage_from_raw(dict(raw)))
        elif event.type == "error":
            raw_error = event.error or {}
            safe_error = sanitize_provider_diagnostic(raw_error)
            if not isinstance(safe_error, dict):
                safe_error = {}
            from provider.protocol.models import UnifiedError

            yield ProviderStreamEvent(
                type=StreamEventType.ERROR,
                sequence=sequence,
            previous_sequence=None if sequence == 0 else sequence - 1,
                request_id=request.request_id,
                response_id=response_id,
                error=UnifiedError(
                    type=str(safe_error.get("exception_type") or "provider_error"),
                    code="PROVIDER_UNAVAILABLE",
                    message=str(safe_error.get("message") or "Provider stream failed"),
                    details=safe_error,
                ),
            )
            return
        elif event.type == "done":
            finish_reason = str(event.metadata.get("finish_reason") or finish_reason)
            refusal = event.metadata.get("refusal")
            if refusal:
                refusal_parts.append(str(refusal))
            raw_annotations = event.metadata.get("annotations")
            if isinstance(raw_annotations, list):
                stream_annotations.extend(
                    item for item in raw_annotations if isinstance(item, dict)
                )
            if isinstance(event.metadata.get("logprobs"), dict):
                stream_logprobs = event.metadata["logprobs"]
            if event.metadata.get("system_fingerprint") is not None:
                stream_system_fingerprint = str(event.metadata["system_fingerprint"])
            if event.metadata.get("service_tier") is not None:
                stream_service_tier = str(event.metadata["service_tier"])
    output: list[Any] = []
    if reasoning_parts:
        output.append(ReasoningItem(id=reasoning_id, content="".join(reasoning_parts)))
    stream_refusal = "".join(refusal_parts) or None
    if text_parts or stream_refusal:
        output.append(
            MessageItem(
                id=message_id,
                role=MessageRole.ASSISTANT,
                phase=(MessagePhase.COMMENTARY if calls else MessagePhase.FINAL_ANSWER),
                content=[TextContent(text="".join(text_parts))] if text_parts else [],
                refusal=stream_refusal,
                annotations=_protocol_annotations(stream_annotations),
                logprobs=_protocol_logprobs(stream_logprobs),
            )
        )
    incomplete_details = _chat_incomplete_details(
        finish_reason,
        calls=calls,
        invalid_calls=invalid_calls,
        invalid_call_diagnostics=invalid_call_diagnostics,
    )
    if incomplete_details is None and request.structured_output is not None and text_parts:
        structured_error = validate_structured_output_text(
            "".join(text_parts), request.structured_output
        )
        if structured_error:
            incomplete_details = {
                "reason": "other",
                "message": "structured output 校验失败",
                "details": {"kind": "structured_output_invalid", "error": structured_error},
            }
    executable_calls = calls if incomplete_details is None else []
    output.extend(executable_calls)
    status = (
        ResponseStatus.INCOMPLETE
        if incomplete_details is not None
        else (
            ResponseStatus.REQUIRES_ACTION
            if executable_calls
            else (ResponseStatus.COMPLETED if output else ResponseStatus.INCOMPLETE)
        )
    )
    if status == ResponseStatus.INCOMPLETE and incomplete_details is None:
        incomplete_details = {"reason": "empty_output"}
    if text_parts:
        yield ProviderStreamEvent(
            type=StreamEventType.OUTPUT_TEXT_DONE,
            sequence=sequence,
            previous_sequence=None if sequence == 0 else sequence - 1,
            request_id=request.request_id,
            response_id=response_id,
            item_id=message_id,
            content_index=0,
            text="".join(text_parts),
        )
        sequence += 1
    for item in executable_calls:
        yield ProviderStreamEvent(
            type=StreamEventType.TOOL_CALL_COMPLETED,
            sequence=sequence,
            previous_sequence=None if sequence == 0 else sequence - 1,
            request_id=request.request_id,
            response_id=response_id,
            item_id=item.id,
            call_id=item.call_id,
            name=item.name,
            item=item,
        )
        sequence += 1
    if any(
        value is not None
        for value in (
            usage.input_tokens,
            usage.output_tokens,
            usage.total_tokens,
            usage.reasoning_tokens,
        )
    ):
        yield ProviderStreamEvent(
            type=StreamEventType.USAGE_UPDATED,
            sequence=sequence,
            previous_sequence=None if sequence == 0 else sequence - 1,
            request_id=request.request_id,
            response_id=response_id,
            usage=usage,
        )
        sequence += 1
    response = KemoResponse(
        protocol_version=request.protocol_version,
        id=response_id,
        request_id=request.request_id,
        status=status,
        model=request.model,
        output=output,
        usage=usage,
        incomplete_details=incomplete_details,
        system_fingerprint=stream_system_fingerprint,
        service=(
            {"tier": stream_service_tier}
            if stream_service_tier is not None
            else None
        ),
        extensions={"kemo.diagnostics": {"finish_reason": finish_reason}},
    )
    terminal_type = (
        StreamEventType.RESPONSE_INCOMPLETE
        if status == ResponseStatus.INCOMPLETE
        else StreamEventType.RESPONSE_COMPLETED
    )
    yield ProviderStreamEvent(
        type=terminal_type,
        sequence=sequence,
        previous_sequence=None if sequence == 0 else sequence - 1,
        request_id=request.request_id,
        response_id=response_id,
        response=response,
    )
