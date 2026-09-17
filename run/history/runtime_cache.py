"""Bounded in-process cache for derived runtime conversation windows.

The archive window remains authoritative.  Runtime messages are the archive
tail and runtime think/tool/item partitions are reconstructed from the same
archive round rows, so the hot workspace may live in memory without becoming
another durable source of truth.  Every entry is fenced by the archive's
``updated_at`` and absolute ``rounds`` values to reject cross-process staleness.
"""

from __future__ import annotations

from collections import OrderedDict
import copy
import json
import os
from pathlib import Path
import threading
from typing import Any


MAX_ENTRIES = 64
MAX_ENTRY_BYTES = 8 * 1024 * 1024

_LOCK = threading.RLock()
_CACHE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def cache_key(directory: Path) -> str:
    return os.path.normcase(str(directory.resolve()))


def archive_version(value: dict[str, Any] | None) -> str:
    data = value.get("data") if isinstance(value, dict) and isinstance(value.get("data"), dict) else value
    if not isinstance(data, dict):
        return "|0"
    try:
        rounds = max(0, int(data.get("rounds") or 0))
    except (TypeError, ValueError):
        rounds = 0
    return f"{str(data.get('updated_at') or '')}|{rounds}"


def _size_bytes(window: dict[str, Any]) -> int:
    try:
        return len(
            json.dumps(
                window,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
    except Exception:
        return MAX_ENTRY_BYTES + 1


def load(directory: Path, *, version: str) -> dict[str, Any] | None:
    key = cache_key(directory)
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        if str(entry.get("version") or "") != str(version or ""):
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return copy.deepcopy(entry["window"])


def store(directory: Path, window: dict[str, Any], *, version: str) -> bool:
    rendered = copy.deepcopy(window)
    size = _size_bytes(rendered)
    key = cache_key(directory)
    with _LOCK:
        if size > MAX_ENTRY_BYTES:
            _CACHE.pop(key, None)
            return False
        _CACHE[key] = {
            "version": str(version or ""),
            "window": rendered,
            "size": size,
        }
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return True


def drop(directory: Path) -> None:
    with _LOCK:
        _CACHE.pop(cache_key(directory), None)


def drop_under(directory: Path) -> int:
    prefix = cache_key(directory).rstrip("\\/") + os.sep
    removed = 0
    with _LOCK:
        for key in list(_CACHE):
            if key == prefix[:-1] or key.startswith(prefix):
                _CACHE.pop(key, None)
                removed += 1
    return removed


def drop_all(root: Path) -> int:
    """Drop every cached runtime workspace below one project root."""

    return drop_under(root)


def drop_session(root: Path, user: str, source: str, session_id: str) -> int:
    removed = 0
    with _LOCK:
        for key, entry in list(_CACHE.items()):
            window = entry.get("window")
            data = window.get("data") if isinstance(window, dict) else None
            if (
                isinstance(data, dict)
                and str(data.get("user") or user) == user
                and str(data.get("source") or "") == source
                and str(data.get("session_id") or "") == session_id
                and key.startswith(cache_key(root).rstrip("\\/") + os.sep)
            ):
                _CACHE.pop(key, None)
                removed += 1
    return removed


def drop_source(root: Path, user: str, source: str) -> int:
    removed = 0
    with _LOCK:
        for key, entry in list(_CACHE.items()):
            window = entry.get("window")
            data = window.get("data") if isinstance(window, dict) else None
            if (
                isinstance(data, dict)
                and str(data.get("user") or user) == user
                and str(data.get("source") or "") == source
                and key.startswith(cache_key(root).rstrip("\\/") + os.sep)
            ):
                _CACHE.pop(key, None)
                removed += 1
    return removed


def clear() -> None:
    with _LOCK:
        _CACHE.clear()


def stats() -> dict[str, int]:
    with _LOCK:
        return {
            "entries": len(_CACHE),
            "bytes": sum(int(entry.get("size") or 0) for entry in _CACHE.values()),
        }


__all__ = [
    "MAX_ENTRIES",
    "MAX_ENTRY_BYTES",
    "archive_version",
    "cache_key",
    "clear",
    "drop",
    "drop_all",
    "drop_session",
    "drop_source",
    "drop_under",
    "load",
    "stats",
    "store",
]
