"""Stable entry point for one complete conversation request loop."""

from __future__ import annotations

from typing import Any, Callable, Iterator
from pathlib import Path
import threading

from events import RunEvent

def iter_request_events_impl(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] | None = None,
    tool_registry_factory: Callable[[Path, str], Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    from datetime import datetime, timezone
    from run.conversation import runtime as runtime
    from run.conversation import main_orchestration
    from run.conversation.request_setup import build_request_context
    from run.conversation.media_routing import prepare_provider_request
    from run.conversation.compression import prepare_compression
    from run.conversation.runtime_state import RoundRuntime
    from run.conversation.provider_loop import ProviderLoopState, run_provider_loop
    from run.conversation.loop_cleanup import cleanup_run_registration, terminal_failure_events

    if provider_factory is None:
        provider_factory = runtime.create_provider
    if tool_registry_factory is None:
        tool_registry_factory = runtime.discover_tools
    dependencies = dict(vars(runtime))
    dependencies.update({
        "datetime": datetime,
        "timezone": timezone,
        "_build_request_context": build_request_context,
        "_prepare_provider_request": prepare_provider_request,
        "_prepare_compression": prepare_compression,
        "RoundRuntime": RoundRuntime,
        "ProviderLoopState": ProviderLoopState,
        "_run_provider_loop": run_provider_loop,
        "cleanup_run_registration": cleanup_run_registration,
        "terminal_failure_events": terminal_failure_events,
    })
    yield from main_orchestration.run_conversation(
        request, runtime_bindings=dependencies, root=root, provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory, cancel_event=cancel_event,
    )
