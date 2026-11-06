"""Trusted conversation identity, independent of browser tabs and task IDs."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import os
from typing import Iterator

RequestScope = tuple[str, ...]
UNSCOPED: RequestScope = ("system",)
_scope: ContextVar[RequestScope] = ContextVar("provider_request_scope", default=UNSCOPED)
_priority: ContextVar[int] = ContextVar("provider_request_priority", default=2)


def current_request_scope() -> RequestScope:
    return _scope.get()


def current_request_priority() -> int:
    return _priority.get()


@contextmanager
def provider_priority(priority: int) -> Iterator[None]:
    """Framework role only: P0 main, P1 maintenance, P2 general workers."""
    if type(priority) is not int or priority not in {0, 1, 2}:
        raise ValueError("Provider priority must be P0, P1 or P2")
    token = _priority.set(priority)
    try:
        yield
    finally:
        _priority.reset(token)


@contextmanager
def provider_request_scope(
    root: str | Path | None, user: str, source: str = "", session_id: str = "",
) -> Iterator[None]:
    """Only framework-owned identity enters the pool; never a model argument."""
    if root is None or not user:
        yield
        return
    key = (os.path.normcase(str(Path(root).resolve())), user, source, session_id)
    token = _scope.set(key)
    try:
        yield
    finally:
        _scope.reset(token)
