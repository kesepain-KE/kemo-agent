"""Public conversation entry points. CardStream is the only execution path."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from typing import Any, AsyncGenerator, Callable, Generator
from uuid import uuid4

from events import RunEvent, error_event
from provider.factory import create_provider
from provider.request_scope import provider_priority, provider_request_scope
from run.config import project_root
from run.infra import EngineError
from run.tools import ToolRegistry, discover_tools
from run.conversation.request_input import required_text
from run.conversation.session_runtime import session_lock


def iter_request_events(
    request: dict[str, Any], *, root: Path | None = None,
    provider_factory: Callable = create_provider,
    tool_registry_factory: Callable = discover_tools,
    cancel_event: threading.Event | None = None,
) -> Generator[RunEvent, None, None]:
    from run.conversation.main_orchestration import run_conversation
    from run.agents import SessionExecutionBusyError

    request = dict(request)
    request.setdefault("_agent_root_task_id", f"main-task-{uuid4().hex}")
    try:
        base = (root or project_root()).resolve()
        user, source, session_id = (required_text(request, key) for key in ("user", "source", "session_id"))
        lock = session_lock(base, user, source, session_id)
        lock.acquire()
    except Exception as error:
        event = error_event(error, phase="request")
        event.metadata.update(committed=False, retryable=False)
        if isinstance(error, SessionExecutionBusyError):
            event.metadata["status"] = "busy"
        yield event
        return

    def scope():
        return provider_request_scope(base, user, source, session_id)

    iterator = run_conversation(
        request, root=base, provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=cancel_event or threading.Event(),
    )
    sequence = 0
    try:
        while True:
            # No ContextVar remains set across yield; interleaved runs do not
            # inherit another conversation's identity or scheduling priority.
            with scope(), provider_priority(0):
                event = next(iterator, None)
            if event is None:
                return
            event.event_id = event.event_id or f"run_evt_{uuid4().hex}"
            event.run_sequence = sequence
            if event.sequence is None:
                event.sequence = sequence
            event.metadata.setdefault("run_id", str(request.get("run_id") or ""))
            sequence += 1
            yield event
    finally:
        try:
            with scope(), provider_priority(0):
                iterator.close()
        finally:
            lock.release()


async def stream_request(
    request: dict[str, Any], *, root: Path | None = None,
    provider_factory: Callable = create_provider,
    tool_registry_factory: Callable = discover_tools,
    cancel_event: threading.Event | None = None,
) -> AsyncGenerator[RunEvent, None]:
    stopped = cancel_event or threading.Event()
    iterator = iter_request_events(
        request, root=root, provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory, cancel_event=stopped,
    )
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="conversation-stream")
    loop = asyncio.get_running_loop()
    try:
        while True:
            event = await loop.run_in_executor(executor, next, iterator, None)
            if event is None:
                break
            yield event
    finally:
        stopped.set()
        try:
            await asyncio.shield(loop.run_in_executor(executor, iterator.close))
        finally:
            executor.shutdown(wait=False)


def handle_request(request: dict[str, Any], **kwargs) -> dict[str, Any]:
    final = None
    for event in iter_request_events(request, **kwargs):
        if event.type == "error":
            raise EngineError(str((event.error or {}).get("message") or "运行失败"))
        if event.type == "done":
            final = event
    if final is None:
        raise EngineError("运行在完成前结束")
    return dict(final.metadata)


def compress_context(request: dict[str, Any], **kwargs) -> dict[str, Any]:
    return handle_request({**request, "prompt": "", "compress_only": True, "compress": True}, **kwargs)
