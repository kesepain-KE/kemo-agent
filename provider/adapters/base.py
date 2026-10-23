"""Adapter contracts and async facade for unified provider implementations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Literal, Protocol

from provider.protocol.models import KemoRequest, KemoResponse, KemoResponseBatch, ModelCapabilities
from provider.protocol.streaming import ProviderStreamEvent


class ProviderAdapter(Protocol):
    @property
    def mode(self) -> Literal["chat", "kemo"]: ...

    def create(self, request: KemoRequest) -> KemoResponse | KemoResponseBatch: ...

    def stream(self, request: KemoRequest) -> Iterable[ProviderStreamEvent]: ...

    def capabilities(self, model: str) -> ModelCapabilities: ...

    def validate(self, request: KemoRequest) -> None: ...


class AsyncProviderFacade:
    """Expose a synchronous adapter through the async design contract."""

    def __init__(self, adapter: ProviderAdapter) -> None:
        self.adapter = adapter

    async def create(self, request: KemoRequest) -> KemoResponse | KemoResponseBatch:
        return await asyncio.to_thread(self.adapter.create, request)

    async def stream(self, request: KemoRequest) -> AsyncIterator[ProviderStreamEvent]:
        iterator = await asyncio.to_thread(iter, self.adapter.stream(request))
        while True:
            event = await asyncio.to_thread(next, iterator, None)
            if event is None:
                break
            yield event

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.adapter.capabilities(model)

    def validate(self, request: KemoRequest) -> None:
        self.adapter.validate(request)
