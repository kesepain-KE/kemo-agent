"""Formal Kemo Provider adapter backed by OpenAI Chat Completions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from provider.adapters.compat import (
    chat_response_to_kemo,
    kemo_request_to_chat,
    chat_stream_to_protocol,
)
from provider.openai_chat import OpenAIChatTransport
from provider.protocol.models import (
    KemoRequest,
    KemoResponse,
    ModelCapabilities,
    PROTOCOL_VERSION,
)
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

    mode = "chat"

    def __init__(self, config: dict[str, Any]) -> None:
        self._transport = OpenAIChatTransport(config)
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
            validate_request_against_capabilities(
                request,
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
        return ModelCapabilities(
            protocol_version=PROTOCOL_VERSION,
            model=model,
            provider_id="chat-compat",
            provider_model=model,
            input_modalities=["text"],
            output_modalities=["text"],
            streaming=True,
            reasoning={
                "supported": False,
                "efforts": [],
                "summary": False,
                "persisted_state": False,
            },
            tools={
                "function_calling": True,
                "parallel_calls": False,
                "multimodal_results": False,
                "tool_choice_modes": ["auto", "none", "required", "named"],
            },
            decoding={"temperature": True, "top_p": True, "stop": True},
        )

    def create(self, request: KemoRequest) -> KemoResponse:
        self.validate(request)
        payload = request.model_copy(update={"stream": False})
        chat_request = kemo_request_to_chat(payload, provider_profile=self._capability_profile)
        return chat_response_to_kemo(self._transport.chat(chat_request), request)

    def stream(self, request: KemoRequest) -> Iterable[ProviderStreamEvent]:
        self.validate(request)
        payload = request.model_copy(update={"stream": True})
        chat_request = kemo_request_to_chat(payload, provider_profile=self._capability_profile)
        return chat_stream_to_protocol(
            self._transport.chat_stream(chat_request),
            request,
            capabilities=self.capabilities(request.model),
        )
