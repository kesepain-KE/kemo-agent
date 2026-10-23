"""Formal Kemo Provider adapter backed by OpenAI Chat Completions."""

from __future__ import annotations

from typing import Literal

from collections.abc import Iterable
from typing import Any

from provider.adapters.compat import (
    chat_response_to_kemo,
    kemo_request_to_chat,
    chat_stream_to_protocol,
)
from provider.openai_chat import (
    OpenAIChatTransport,
    deepseek_reasoning_effort,
    is_deepseek_chat,
)
from provider.protocol.models import (
    DecodingCapabilities,
    KemoRequest,
    KemoResponse,
    ModelCapabilities,
    PROTOCOL_VERSION,
    ReasoningCapabilities,
    ToolCapabilities,
)
from provider.protocol.enums import ToolChoiceMode
from provider.protocol.streaming import ProviderStreamEvent
from provider.protocol.validation import (
    validate_provider_options,
    validate_request,
    validate_request_against_capabilities,
)


class ChatBridgeProvider:
    """Expose the Kemo contract over a standard ``/chat/completions`` API.

    This is a selected transport mode, not a fallback for the native Kemo
    gateway. Image support depends on the selected model and must be declared
    by the caller; the bridge itself does not infer model capability.
    """

    mode: Literal["chat"] = "chat"

    def __init__(self, config: dict[str, Any]) -> None:
        self._transport = OpenAIChatTransport(config)
        self._is_deepseek = is_deepseek_chat(config)
        # Capability declarations are profile data, not inferred from a
        # request.  A profile-less bridge may still serve requests which do
        # not use optional vendor options, but it must fail closed for them.
        raw_profile = config.get("capabilities")
        if isinstance(raw_profile, dict) and isinstance(
            raw_profile.get("capabilities"), dict
        ):
            # D62's profile is a wrapper around the wire ModelCapabilities;
            # normalize it once while retaining adapter-only transport and
            # degradation sections for request mapping.
            self._capability_profile = dict(raw_profile["capabilities"])
            for key in ("transport", "degradation_policy", "provider_option_map"):
                if key in raw_profile:
                    self._capability_profile[key] = raw_profile[key]
        else:
            self._capability_profile = raw_profile

    def validate(self, request: KemoRequest) -> None:
        validate_request(request)
        # Empty options are valid without a profile; non-empty options require
        # an explicit allow-list and are never forwarded based on a vendor
        # prefix alone.
        vendor_options = {
            key: value
            for key, value in request.provider_options.items()
            if isinstance(key, str) and key.startswith("vendor.")
        }
        validate_provider_options(vendor_options, self._capability_profile)
        if isinstance(self._capability_profile, dict):
            capability_request = request
            if self._is_deepseek:
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
                thinking_enabled = (
                    request.reasoning is None
                    or request.reasoning.enabled
                )
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
        if isinstance(self._capability_profile, dict):
            profile = dict(self._capability_profile)
            # The transport allow-list is adapter configuration, not a
            # ModelCapabilities wire field.  Keep it beside the profile while
            # parsing the protocol capability object.
            profile.pop("provider_options", None)
            profile.pop("provider_options_allowlist", None)
            profile.pop("provider_option_map", None)
            profile.pop("transport", None)
            profile.pop("degradation_policy", None)
            profile.setdefault("protocol_version", PROTOCOL_VERSION)
            profile.setdefault("model", model)
            profile.setdefault("provider_id", "chat-compat")
            profile.setdefault("provider_model", model)
            return ModelCapabilities.model_validate(profile)
        if self._is_deepseek:
            return ModelCapabilities(
                protocol_version=PROTOCOL_VERSION,
                model=model,
                provider_id="chat-compat",
                provider_model=model,
                input_modalities=["text"],
                output_modalities=["text"],
                streaming=True,
                reasoning=ReasoningCapabilities(
                    supported=True,
                    efforts=["low", "high", "max"],
                    summary=False,
                    returns=["none", "content", "auto"],
                    persisted_state=False,
                ),
                tools=ToolCapabilities(
                    function_calling=True,
                    parallel_calls=True,
                    multimodal_results=False,
                    tool_choice_modes=[ToolChoiceMode.AUTO, ToolChoiceMode.NONE],
                ),
                decoding=DecodingCapabilities(
                    temperature=True,
                    top_p=True,
                    stop=True,
                    presence_penalty=False,
                    frequency_penalty=False,
                ),
            )
        return ModelCapabilities(
            protocol_version=PROTOCOL_VERSION,
            model=model,
            provider_id="chat-compat",
            provider_model=model,
            input_modalities=["text"],
            output_modalities=["text"],
            streaming=True,
            reasoning=ReasoningCapabilities(
                supported=True,
                efforts=["minimal", "low", "medium", "high", "max"],
                summary=True,
                returns=["none", "summary", "content", "auto"],
                persisted_state=False,
            ),
            tools=ToolCapabilities(
                function_calling=True,
                parallel_calls=False,
                multimodal_results=False,
                tool_choice_modes=[
                    ToolChoiceMode.AUTO,
                    ToolChoiceMode.NONE,
                    ToolChoiceMode.REQUIRED,
                    ToolChoiceMode.NAMED,
                ],
            ),
            decoding=DecodingCapabilities(temperature=True, top_p=True, stop=True),
        )

    def create(self, request: KemoRequest) -> KemoResponse:
        self.validate(request)
        payload = request.model_copy(update={"stream": False})
        chat_request = kemo_request_to_chat(
            payload,
            provider_profile=self._capability_profile,
            deepseek=self._is_deepseek,
        )
        return chat_response_to_kemo(self._transport.chat(chat_request), request)

    def stream(self, request: KemoRequest) -> Iterable[ProviderStreamEvent]:
        self.validate(request)
        payload = request.model_copy(update={"stream": True})
        chat_request = kemo_request_to_chat(
            payload,
            provider_profile=self._capability_profile,
            deepseek=self._is_deepseek,
        )
        return chat_stream_to_protocol(
            self._transport.chat_stream(chat_request),
            request,
            capabilities=self.capabilities(request.model),
        )
