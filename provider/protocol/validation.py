"""Cross-object validation not naturally expressed by individual models."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from datetime import datetime, timezone

from provider.protocol.errors import CapabilityError, ProtocolValidationError, ToolLinkageError
from provider.protocol.models import (
    Item,
    KemoRequest,
    ModelCapabilities,
    MessageItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
)


def validate_tool_linkage(items: Iterable[Item]) -> None:
    calls: dict[str, ToolCallItem] = {}
    results: set[str] = set()
    for index, item in enumerate(items):
        if isinstance(item, ToolCallItem):
            if item.call_id in calls:
                raise ToolLinkageError(
                    f"tool_call.call_id 重复：{item.call_id}",
                    path=f"items[{index}].call_id",
                )
            calls[item.call_id] = item
        elif isinstance(item, ToolResultItem):
            call = calls.get(item.call_id)
            if call is None:
                raise ToolLinkageError(
                    f"tool_result 无匹配 tool_call：{item.call_id}",
                    path=f"items[{index}].call_id",
                )
            if item.call_id in results:
                raise ToolLinkageError(
                    f"tool_result.call_id 重复：{item.call_id}",
                    path=f"items[{index}].call_id",
                )
            if item.name != call.name:
                raise ToolLinkageError(
                    f"tool_result.name 与 tool_call 不一致：{item.name} != {call.name}",
                    path=f"items[{index}].name",
                )
            results.add(item.call_id)


def validate_request(request: KemoRequest) -> KemoRequest:
    validate_tool_linkage(request.input)
    return request


_VENDOR_OPTION_RE = re.compile(r"^vendor\.[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+$")
_FORBIDDEN_OPTION_NAMES = {
    "authorization", "authorisation", "api_key", "apikey", "token",
    "url", "base_url", "endpoint", "http", "headers", "header",
}
_FORMAL_REQUEST_FIELDS = {
    "model", "stream", "n", "temperature", "top_p", "top_k", "stop",
    "seed", "logit_bias", "presence_penalty", "frequency_penalty", "verbosity",
    "logprobs", "top_logprobs", "tools", "tool_choice", "allowed_tools",
    "parallel_tool_calls", "response_format", "prediction", "service_tier",
    "prompt_cache_key", "safety_identifier", "store", "reasoning_effort",
}


def _option_contains_forbidden_transport_data(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in _FORBIDDEN_OPTION_NAMES:
                return True
            if _option_contains_forbidden_transport_data(child):
                return True
    elif isinstance(value, list):
        return any(_option_contains_forbidden_transport_data(item) for item in value)
    return False


def validate_provider_options(
    options: Mapping[str, object],
    profile: Mapping[str, object] | None,
) -> dict[str, object]:
    """Validate and map the D61 vendor option allow-list.

    ``profile`` is intentionally required for non-empty options: a vendor
    prefix alone is not permission to forward data.  The returned dictionary
    contains upstream keys and is safe for a transport payload.
    """

    if not options:
        return {}
    if not isinstance(profile, Mapping):
        raise ProtocolValidationError(
            "provider_options 需要 provider profile 显式白名单",
            path="provider_options",
            details={"path": "provider_options"},
        )
    # The 2.0 profile name is provider_option_map.  Accept the two internal
    # legacy spellings only as a read-only migration aid; the formal profile
    # contract is never widened by their presence.
    raw_allowlist = profile.get("provider_option_map")
    if raw_allowlist is None:
        raw_allowlist = profile.get("provider_options")
    if raw_allowlist is None:
        raw_allowlist = profile.get("provider_options_allowlist")
    if not isinstance(raw_allowlist, Mapping):
        raw_allowlist = {}
    mapped: dict[str, object] = {}
    for key, value in options.items():
        path = f"provider_options.{key}"
        if not isinstance(key, str) or not _VENDOR_OPTION_RE.fullmatch(key):
            raise ProtocolValidationError(
                "provider_options key 必须使用 vendor.<provider>.<option>",
                path=path,
                details={"path": path},
            )
        declaration = raw_allowlist.get(key)
        if declaration is None:
            raise ProtocolValidationError(
                "provider_options 未获 profile 白名单授权",
                path=path,
                details={"path": path},
            )
        if isinstance(declaration, str):
            target = declaration
        elif isinstance(declaration, Mapping):
            target = declaration.get("target", declaration.get("chat_key"))
        else:
            target = None
        if not isinstance(target, str) or not target or target in _FORMAL_REQUEST_FIELDS:
            raise ProtocolValidationError(
                "provider_options 不得覆盖正式协议字段",
                path=path,
                details={"path": path},
            )
        target_leaf = target.casefold().split(".")[-1].replace("-", "_")
        if target_leaf in _FORBIDDEN_OPTION_NAMES or _option_contains_forbidden_transport_data(value):
            raise ProtocolValidationError(
                "provider_options 不得携带凭据、URL 或 HTTP 传输配置",
                path=path,
                details={"path": path},
            )
        if target in mapped:
            raise ProtocolValidationError(
                "provider_options 映射目标重复",
                path=path,
                details={"path": path},
            )
        mapped[target] = value
    return mapped


def _capability_error(message: str, path: str) -> CapabilityError:
    code = "CAPABILITY_ERROR"
    if path == "stream":
        code = "STREAMING_UNSUPPORTED"
    elif path.startswith("input"):
        code = "UNSUPPORTED_INPUT_MODALITY"
    elif path.startswith("output"):
        code = "UNSUPPORTED_OUTPUT_MODALITY"
    elif path == "tools":
        code = "TOOLS_UNSUPPORTED"
    elif path in {"parallel_tool_calls", "tool_choice.allowed_mode"}:
        code = "PARALLEL_TOOLS_UNSUPPORTED"
    elif path == "reasoning.enabled":
        code = "REASONING_UNSUPPORTED"
    elif path == "reasoning.effort":
        code = "REASONING_EFFORT_UNSUPPORTED"
    elif path.startswith("structured_output"):
        code = "STRUCTURED_OUTPUT_UNSUPPORTED"
    elif path == "prediction":
        code = "PREDICTION_UNSUPPORTED"
    error = CapabilityError(message, path=path, details={"path": path})
    error.code = code
    return error


def validate_request_against_capabilities(
    request: KemoRequest,
    capabilities: ModelCapabilities,
) -> None:
    """Run the side-effect-free L3 request/profile compatibility gate.

    This function deliberately does not rewrite or downgrade ``request``.  A
    chat adapter may build a separate effective request after an explicit
    downgrade policy has run, then call this gate again on that copy.
    """

    if capabilities.model != request.model:
        error = _capability_error("能力声明与请求模型不一致", "model")
        error.code = "CAPABILITIES_MODEL_MISMATCH"
        error.details.update(
            {"request_model": request.model, "capabilities_model": capabilities.model}
        )
        raise error
    if capabilities.task != "llm":
        raise _capability_error(
            "对话请求不能发送到 embedding/rerank 模型", "model.task"
        )
    if request.stream and not capabilities.streaming:
        raise _capability_error("模型不支持流式输出", "stream")

    input_modalities: set[str] = set()
    for index, item in enumerate(request.input):
        if isinstance(item, MessageItem):
            for content_index, block in enumerate(item.content):
                modality = {
                    "text": "text",
                    "image": "image",
                    "audio": "audio",
                    "video": "video",
                    "file": "file",
                }.get(getattr(block, "type", ""))
                if modality:
                    input_modalities.add(modality)
                elif getattr(block, "type", "") == "json":
                    input_modalities.add("text")
        elif isinstance(item, ReasoningItem):
            # Reasoning is a control-plane item, not a user input modality.
            continue
        elif isinstance(item, (ToolCallItem, ToolResultItem)):
            continue
    unsupported = input_modalities - set(capabilities.input_modalities)
    if unsupported:
        modality = sorted(unsupported)[0]
        raise _capability_error(
            f"模型不支持输入模态：{modality}", f"input[{next(i for i, item in enumerate(request.input) if isinstance(item, MessageItem))}].content"
        )
    image_count = sum(
        1
        for item in request.input
        if isinstance(item, MessageItem)
        for block in item.content
        if getattr(block, "type", None) == "image"
    )
    if (
        image_count
        and capabilities.limits.max_images is not None
        and image_count > capabilities.limits.max_images
    ):
        raise _capability_error("输入图片数量超过模型上限", "input")

    requested_outputs = set(request.output.modalities)
    unsupported_outputs = requested_outputs - set(capabilities.output_modalities)
    if unsupported_outputs:
        modality = sorted(unsupported_outputs)[0]
        raise _capability_error(
            f"模型不支持输出模态：{modality}", "output.modalities"
        )

    if request.structured_output is not None:
        structured = capabilities.structured_output
        if not structured.supported:
            raise _capability_error(
                "模型不支持 structured output", "structured_output"
            )
        if request.structured_output.type not in structured.schema_subset:
            raise _capability_error(
                f"模型不支持 structured output 类型：{request.structured_output.type}",
                "structured_output.type",
            )
        if request.structured_output.strict and not structured.strict:
            raise _capability_error(
                "模型不支持 strict structured output", "structured_output.strict"
            )

    if request.generation.n > 1:
        if not capabilities.supports_multiple_choices:
            raise _capability_error("模型不支持多候选输出", "generation.n")
        if request.generation.n > capabilities.limits.max_choices:
            raise _capability_error("候选数超过模型上限", "generation.n")

    if request.reasoning is not None and request.reasoning.enabled:
        reasoning = capabilities.reasoning
        if not reasoning.supported:
            raise _capability_error("模型不支持 reasoning", "reasoning.enabled")
        if request.reasoning.effort not in reasoning.efforts:
            raise _capability_error(
                f"模型不支持 reasoning effort：{request.reasoning.effort}",
                "reasoning.effort",
            )
        if request.reasoning.return_mode not in reasoning.returns:
            raise _capability_error(
                f"模型不支持 reasoning return_mode：{request.reasoning.return_mode}",
                "reasoning.return",
            )
        if request.reasoning.context not in reasoning.contexts:
            raise _capability_error(
                f"模型不支持 reasoning context：{request.reasoning.context}",
                "reasoning.context",
            )
        if request.reasoning.return_mode == "summary" and not reasoning.summary:
            raise _capability_error("模型不支持 reasoning summary", "reasoning.return")
        if request.reasoning.context == "all_turns" and not reasoning.persisted_state:
            raise _capability_error("模型不支持跨轮 reasoning 状态", "reasoning.context")

    if request.tools and request.tool_choice.mode != "none":
        limits = capabilities.limits
        if limits.max_tools is not None and len(request.tools) > limits.max_tools:
            raise _capability_error("工具数量超过模型上限", "tools")
        tools = capabilities.tools
        if not tools.function_calling:
            raise _capability_error("模型不支持工具调用", "tools")
        if request.parallel_tool_calls and not tools.parallel_calls:
            raise _capability_error(
                "模型不支持并行工具调用", "parallel_tool_calls"
            )
        if (
            request.parallel_tool_calls
            and limits.max_parallel_tools is not None
            and limits.max_parallel_tools < 2
        ):
            raise _capability_error(
                "模型不支持请求的并行工具调用规模", "parallel_tool_calls"
            )
        strict_parameters = capabilities.extensions.get("strict_tool_parameters")
        if any(tool.strict for tool in request.tools) and strict_parameters is False:
            raise _capability_error("模型不支持 strict function parameters", "tools")
        mode_value = getattr(request.tool_choice.mode, "value", request.tool_choice.mode)
        declared_modes = {
            getattr(mode, "value", mode) for mode in tools.tool_choice_modes
        }
        if mode_value not in declared_modes:
            raise _capability_error(
                f"模型不支持 tool_choice 模式：{mode_value}",
                "tool_choice.mode",
            )
        if mode_value == "allowed":
            allowed_mode = getattr(
                request.tool_choice.allowed_mode,
                "value",
                request.tool_choice.allowed_mode,
            )
            if allowed_mode == "required" and "required" not in declared_modes:
                raise _capability_error(
                    "模型不支持 allowed(required) 工具选择",
                    "tool_choice.allowed_mode",
                )

    decoding = capabilities.decoding
    decoding_checks = {
        "temperature": request.generation.temperature is not None,
        "top_p": request.generation.top_p is not None,
        "top_k": request.generation.top_k is not None,
        "stop": request.generation.stop is not None,
        "seed": request.generation.seed is not None,
        "logit_bias": request.generation.logit_bias is not None,
        "presence_penalty": request.generation.presence_penalty is not None,
        "frequency_penalty": request.generation.frequency_penalty is not None,
        "verbosity": request.generation.verbosity is not None,
        "logprobs": request.generation.logprobs is not None
        and request.generation.logprobs.enabled,
    }
    for field_name, requested in decoding_checks.items():
        if requested and not getattr(decoding, field_name):
            raise _capability_error(
                f"模型不支持 decoding.{field_name}", f"generation.{field_name}"
            )
    if (
        request.generation.logprobs is not None
        and request.generation.logprobs.enabled
        and request.generation.logprobs.top_k > decoding.top_logprobs_max
    ):
        raise _capability_error(
            "logprobs.top_k 超过模型上限", "generation.logprobs.top_k"
        )
    if request.generation.max_output_tokens is not None:
        maximum = capabilities.limits.max_output_tokens
        if maximum is not None and request.generation.max_output_tokens > maximum:
            raise _capability_error(
                "max_output_tokens 超过模型上限", "generation.max_output_tokens"
            )
    if request.prediction is not None:
        if not capabilities.supports_prediction:
            raise _capability_error("模型不支持 prediction", "prediction")
        if isinstance(request.prediction.content, list):
            declared = capabilities.extensions.get("prediction_content_types")
            if not isinstance(declared, (list, tuple, set, frozenset)):
                raise _capability_error(
                    "模型未显式声明 prediction 内容块类型", "prediction.content"
                )
            requested_types = {getattr(block, "type", None) for block in request.prediction.content}
            supported_types = {str(value) for value in declared}
            unsupported_types = requested_types - supported_types
            if unsupported_types:
                raise _capability_error(
                    f"模型不支持 prediction 内容块：{sorted(unsupported_types)}",
                    "prediction.content",
                )
    provider_states = [
        item.provider_state
        for item in request.input
        if isinstance(item, ReasoningItem) and item.provider_state is not None
    ]
    if provider_states:
        if not capabilities.reasoning.persisted_state:
            raise _capability_error(
                "模型未声明支持 persisted provider state", "input.provider_state"
            )
        now = datetime.now(timezone.utc)
        for state in provider_states:
            if state.provider != capabilities.provider_id:
                raise _capability_error(
                    "provider_state 不得跨 Provider 重解释", "input.provider_state.provider"
                )
            if state.model is not None and state.model != request.model:
                raise _capability_error(
                    "provider_state 与请求模型不一致", "input.provider_state.model"
                )
            if state.expires_at is not None and state.expires_at <= now:
                raise _capability_error(
                    "provider_state 已过期", "input.provider_state.expires_at"
                )
    service_guarantees = capabilities.extensions.get("service")
    if not isinstance(service_guarantees, Mapping):
        service_guarantees = {}
    for field in ("store", "prompt_cache_key", "safety_identifier"):
        if getattr(request.service, field) is not None and service_guarantees.get(field) is not True:
            raise _capability_error(
                f"模型不保证 service.{field} 语义", f"service.{field}"
            )
    if request.service.tier is not None and request.service.tier not in capabilities.service_tiers:
        raise _capability_error("模型不支持 service tier", "service.tier")
