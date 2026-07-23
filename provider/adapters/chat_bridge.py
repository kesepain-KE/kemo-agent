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
from provider.protocol.models import KemoRequest, KemoResponse, ModelCapabilities
from provider.protocol.streaming import ProviderStreamEvent
from provider.protocol.validation import validate_request


class ChatBridgeProvider:
    """Expose the Kemo contract over a standard ``/chat/completions`` API.

    This is a selected transport mode, not a fallback for the native Kemo
    gateway.  Its portable baseline is text/image input, text output, streaming
    and function tools.  Unsupported Kemo content is rejected by the converter.
    """

    mode = "chat"

    def __init__(self, config: dict[str, Any]) -> None:
        self._transport = OpenAIChatTransport(config)

    def validate(self, request: KemoRequest) -> None:
        validate_request(request)

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            model=model,
            input_modalities=["text", "image"],
            output_modalities=["text"],
            streaming=True,
            reasoning={
                "supported": True,
                "efforts": ["minimal", "low", "medium", "high", "max"],
                "summary": False,
                "persisted_state": False,
            },
            tools={
                "function_calling": True,
                "parallel_calls": False,
                "multimodal_results": False,
            },
        )

    def create(self, request: KemoRequest) -> KemoResponse:
        self.validate(request)
        payload = request.model_copy(update={"stream": False})
        chat_request = kemo_request_to_chat(payload)
        return chat_response_to_kemo(self._transport.chat(chat_request), request)

    def stream(self, request: KemoRequest) -> Iterable[ProviderStreamEvent]:
        self.validate(request)
        payload = request.model_copy(update={"stream": True})
        chat_request = kemo_request_to_chat(payload)
        return chat_stream_to_protocol(
            self._transport.chat_stream(chat_request),
            request,
            capabilities=self.capabilities(request.model),
        )
