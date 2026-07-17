"""Process-local scheduler registry for background sub-agent jobs."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from events import RunEvent
from provider.factory import create_provider
from run.agent_queue import AgentScheduler
from run.agent_runner import AgentRunner


_lock = threading.RLock()
_schedulers: dict[tuple[str, str], AgentScheduler] = {}


def get_agent_scheduler(
    root: Path,
    user: str,
    *,
    config: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    event_callback: Callable[[RunEvent], None] | None = None,
) -> AgentScheduler:
    key = (str(root.resolve()).casefold(), user)
    with _lock:
        scheduler = _schedulers.get(key)
        if scheduler is None:
            runner = AgentRunner(
                root,
                user,
                config=config,
                provider_factory=provider_factory,
            )
            scheduler = AgentScheduler.from_runner(runner, event_callback=event_callback)
            _schedulers[key] = scheduler
        return scheduler


def close_agent_schedulers(*, wait: bool = True, cancel_pending: bool = False) -> None:
    with _lock:
        schedulers = list(_schedulers.values())
        _schedulers.clear()
    for scheduler in schedulers:
        scheduler.close(wait=wait, cancel_pending=cancel_pending)
