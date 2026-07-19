"""Native Kemo Provider transport with no Chat Completions fallback."""

from __future__ import annotations

from typing import Any

from provider.adapters.gateway import KemoGatewayAdapter


class KemoGatewayProvider:
    mode = "kemo"

    def __init__(self, config: dict[str, Any]) -> None:
        self._native = KemoGatewayAdapter(config)

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
