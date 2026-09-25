"""Provider/tool exchange stage for one conversation round."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(slots=True)
class ProviderLoopState:
    """Mutable namespace shared with the conversation orchestration entry point."""
    values: dict[str, Any]
    dependencies: dict[str, Any]
    stop_main: bool = True

def run_provider_loop(state: ProviderLoopState):
    from run.conversation import provider_exchange
    yield from provider_exchange.run_provider_exchange(state)
