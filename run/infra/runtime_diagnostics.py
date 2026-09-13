"""Bounded, process-local diagnostic summaries and short-lived log read cache.

Never accept command arguments, output, prompts, message bodies or tracebacks.
These summaries are not a durable audit log.
"""
from __future__ import annotations

from collections import deque
import copy
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import threading
import time
from typing import Any, Callable
import uuid
from run.infra.read_cache import BoundedReadCache

MAX_EVENTS = 2048
EVENT_TTL_SECONDS = 3600.0
CACHE_TTL_SECONDS = 5.0
MAX_CACHE_ENTRIES = 16
_events: deque[tuple[float, str, str, dict[str, Any]]] = deque(maxlen=MAX_EVENTS)
_event_lock = threading.Lock()
_cache = BoundedReadCache(max_entries=MAX_CACHE_ENTRIES, max_bytes=8 * 1024 * 1024, per_owner=1)


def _root_key(root: Path) -> str:
    return os.path.normcase(str(root.resolve()))


def diagnostic_identifier(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value or ""))[:80]


def record_runtime_event(
    root: Path, user: str, *, category: str, name: str, status: str,
    duration_ms: int = 0, error_type: str = "", exit_code: int | None = None,
) -> None:
    """Best-effort observation must never change a tool's execution outcome."""
    try:
        if not user or category not in {"backend", "terminal"}:
            return
        entry = {
            "id": "live:" + uuid.uuid4().hex,
            "category": category, "title": diagnostic_identifier(name),
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "status": status if status in {"success", "failed", "cancelled", "running", "stopped", "starting", "stopping"} else "recorded",
            "duration_ms": min(86_400_000, max(0, int(duration_ms))),
            "detail": ("错误类型：" + diagnostic_identifier(error_type)) if error_type else "",
            "source": "memory", "exit_code": exit_code if type(exit_code) is int else None,
        }
        with _event_lock:
            _events.append((time.monotonic(), _root_key(root), user, entry))
    except Exception:
        pass


def runtime_event_snapshot(root: Path, user: str) -> list[dict[str, Any]]:
    key = _root_key(root)
    cutoff = time.monotonic() - EVENT_TTL_SECONDS
    with _event_lock:
        while _events and _events[0][0] < cutoff:
            _events.popleft()
        return [copy.deepcopy(item) for _, event_root, owner, item in _events
                if event_root == key and owner in {user, "__system__"}]


def invalidate_runtime_log_cache(root: Path) -> None:
    key = _root_key(root)
    _cache.invalidate(lambda candidate: candidate[0] == key)


def cached_runtime_log_records(
    root: Path, user: str, loader: Callable[[], list[dict[str, Any]]], *, refresh: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    key = (_root_key(root), user)
    if refresh:
        _cache.invalidate(lambda candidate: candidate == key)
    return _cache.get_or_load(key, loader, owner=key, ttl=CACHE_TTL_SECONDS)
