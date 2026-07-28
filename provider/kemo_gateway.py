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

    def capabilities(self, model: str, *, capabilities_url: str | None = None):
        return self._native.capabilities(model, capabilities_url=capabilities_url)

    def models(self, *, task: str | None = None):
        return self._native.models(task=task)

    def embeddings(self, request):
        return self._native.embeddings(request)

    def embed(self, request):
        return self._native.embed(request)

    def rerank(self, request):
        return self._native.rerank(request)

    def get_response(self, response_id: str):
        return self._native.get_response(response_id)

    def cancel(self, response_id: str):
        return self._native.cancel(response_id)

    def upload_asset(self, *args, **kwargs):
        return self._native.upload_asset(*args, **kwargs)

    def get_asset(self, asset_id: str):
        return self._native.get_asset(asset_id)

    def wait_asset_ready(self, *args, **kwargs):
        return self._native.wait_asset_ready(*args, **kwargs)

    def download_asset(self, *args, **kwargs):
        return self._native.download_asset(*args, **kwargs)

    def delete_asset(self, asset_id: str):
        return self._native.delete_asset(asset_id)
