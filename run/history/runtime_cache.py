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
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_OWNER_ENTRIES = 16
MAX_OWNER_BYTES = 16 * 1024 * 1024

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


def _owner(window: dict[str, Any], key: str) -> str:
    data = window.get("data") if isinstance(window, dict) else None
    user = str(data.get("user") or "").strip() if isinstance(data, dict) else ""
    if user:
        for parent in Path(key).parents:
            if parent.name == user and parent.parent.name.casefold() == "users":
                return os.path.normcase(str(parent))
    return key


def _cached_bytes() -> int:
    return sum(int(entry.get("size") or 0) for entry in _CACHE.values())


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
    owner = _owner(rendered, key)
    with _LOCK:
        if size > min(MAX_ENTRY_BYTES, MAX_TOTAL_BYTES, MAX_OWNER_BYTES):
            _CACHE.pop(key, None)
            return False
        _CACHE.pop(key, None)
        _CACHE[key] = {
            "version": str(version or ""),
            "window": rendered,
            "size": size,
            "owner": owner,
        }
        _CACHE.move_to_end(key)
        owned = [
            candidate
            for candidate, entry in _CACHE.items()
            if str(entry.get("owner") or candidate) == owner
        ]
        owner_bytes = sum(int(_CACHE[candidate].get("size") or 0) for candidate in owned)
        while owned and (
            len(owned) > MAX_OWNER_ENTRIES or owner_bytes > MAX_OWNER_BYTES
        ):
            oldest = owned.pop(0)
            owner_bytes -= int(_CACHE[oldest].get("size") or 0)
            _CACHE.pop(oldest, None)
        while len(_CACHE) > MAX_ENTRIES or _cached_bytes() > MAX_TOTAL_BYTES:
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
            "bytes": _cached_bytes(),
        }


__all__ = [
    "MAX_ENTRIES",
    "MAX_ENTRY_BYTES",
    "MAX_TOTAL_BYTES",
    "MAX_OWNER_ENTRIES",
    "MAX_OWNER_BYTES",
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
