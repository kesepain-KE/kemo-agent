"""Formal Kemo Provider adapter backed by OpenAI Chat Completions."""

from __future__ import annotations

from typing import Literal

from collections.abc import Iterable
from typing import Any
import copy

from provider.adapters.compat import (
    chat_response_to_kemo,
    kemo_request_to_chat,
    chat_stream_to_protocol,
)
from provider.adapters.image_content import validate_chat_request
from provider.chat_profiles import resolve_chat_profile
from provider.openai_chat import (
    OpenAIChatTransport,
    deepseek_reasoning_effort,
    is_deepseek_chat,
)
from provider.protocol.models import (
    KemoRequest,
    KemoResponse,
    ModelCapabilities,
    ToolResultItem,
)
from provider.protocol.errors import CapabilityError
from provider.protocol.streaming import ProviderStreamEvent
from provider.protocol.validation import (
    validate_provider_options,
    validate_request_against_capabilities,
)


class ChatBridgeProvider:
    """Expose the Kemo contract over a standard ``/chat/completions`` API.

    This is a selected transport mode, not a fallback for the native Kemo
    gateway. Maintained local profiles and a labelled basic compatibility
    policy make Chat usable without requiring user-written capability JSON.
    """

    mode: Literal["chat"] = "chat"

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = copy.deepcopy(config)
        selected = resolve_chat_profile(self._config, str(config["model"]))
        self._capability_profile = selected.profile
        effective_config = {**self._config, "capabilities": selected.profile}
        self._transport = OpenAIChatTransport(effective_config)
        self._is_deepseek = is_deepseek_chat(effective_config)

    def _model_context(self, model: str) -> tuple[dict[str, Any], OpenAIChatTransport, bool]:
        if model == self._transport.model:
            return self._capability_profile, self._transport, self._is_deepseek
        selected = resolve_chat_profile(self._config, model)
        config = {**self._config, "model": model, "capabilities": selected.profile}
        return selected.profile, OpenAIChatTransport(config), is_deepseek_chat(config)

    def validate(self, request: KemoRequest) -> None:
        profile, _, deepseek = self._model_context(request.model)
        validate_chat_request(request)
        # Empty options are valid without a profile; non-empty options require
        # an explicit allow-list and are never forwarded based on a vendor
        # prefix alone.
        validate_provider_options(request.provider_options, profile)
        for index, item in enumerate(request.input):
            if isinstance(item, ToolResultItem) and any(
                block.type not in {"text", "json", "reference"} for block in item.content
            ):
                raise CapabilityError("Chat 工具结果不支持原生媒体；请通过媒体输入队列传入",
                                      path=f"input[{index}].content")
        capability_request = request
        if deepseek:
            request_updates: dict[str, Any] = {}
            if request.reasoning is not None and request.reasoning.enabled:
                request_updates["reasoning"] = request.reasoning.model_copy(
                    update={
                        "effort": deepseek_reasoning_effort(
                            request.reasoning.effort
                        )
                    }
                )
            # Thinking-mode DeepSeek ignores these generation controls;
            # validate the effective adapter request rather than rejecting
            # an option that this branch will intentionally clean up.
            thinking_enabled = request.reasoning is not None and request.reasoning.enabled
            if thinking_enabled and (
                request.generation.presence_penalty is not None
                or request.generation.frequency_penalty is not None
            ):
                request_updates["generation"] = request.generation.model_copy(
                    update={
                        "presence_penalty": None,
                        "frequency_penalty": None,
                    }
                )
            if request_updates:
                capability_request = request.model_copy(update=request_updates)
        validate_request_against_capabilities(
            capability_request,
            self.capabilities(request.model),
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        return resolve_chat_profile(self._config, model).capabilities

    def create(self, request: KemoRequest) -> KemoResponse:
        self.validate(request)
        profile, transport, deepseek = self._model_context(request.model)
        payload = request.model_copy(update={"stream": False})
        chat_request = kemo_request_to_chat(
            payload,
            provider_profile=profile,
            deepseek=deepseek,
        )
        return chat_response_to_kemo(transport.chat(chat_request), request)

    def stream(self, request: KemoRequest) -> Iterable[ProviderStreamEvent]:
        self.validate(request)
        profile, transport, deepseek = self._model_context(request.model)
        payload = request.model_copy(update={"stream": True})
        chat_request = kemo_request_to_chat(
            payload,
            provider_profile=profile,
            deepseek=deepseek,
        )
        return chat_stream_to_protocol(
            transport.chat_stream(chat_request),
            request,
            capabilities=self.capabilities(request.model),
        )
