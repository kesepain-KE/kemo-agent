"""Dual-surface Kemo provider: native protocol plus legacy chat fallback."""

from __future__ import annotations

from typing import Any

from provider.adapters.gateway import KemoGatewayAdapter
from provider.openai_chat import OpenAIChatProvider


class KemoGatewayProvider:
    def __init__(self, config: dict[str, Any]) -> None:
        self.mode = "kemo"
        self._native = KemoGatewayAdapter(config)
        self._legacy = OpenAIChatProvider(config=config, mode="kemo")

    # Legacy methods stay available for existing subagents and compatibility
    # callers.  The main Run pipeline uses create/stream below.
    def chat(self, request):
        return self._legacy.chat(request)

    def chat_stream(self, request):
        return self._legacy.chat_stream(request)

    def create(self, request):
        return self._native.create(request)

    def stream(self, request):
        return self._native.stream(request)

    def validate(self, request):
        return self._native.validate(request)

    def capabilities(self, model: str):
        return self._native.capabilities(model)

    def get_response(self, response_id: str):
        return self._native.get_response(response_id)

    def cancel(self, response_id: str):
        return self._native.cancel(response_id)
