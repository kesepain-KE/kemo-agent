"""Bounded disposable read snapshots; never use for locks or dirty state."""
from __future__ import annotations

from collections import OrderedDict
import copy
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Hashable


def _weight(value: Any, seen: set[int] | None = None) -> int:
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_weight(k, seen) + _weight(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list, set, frozenset)):
        size += sum(_weight(v, seen) for v in value)
    return size


@dataclass
class _Flight:
    event: threading.Event
    thread: int
    invalid: bool = False


class BoundedReadCache:
    """TTL/LRU snapshots with owner quotas and per-key single-flight loading.

    Namespace is the cache instance; callers supply root/user-scoped keys and
    owners. Loaders/copies run outside the bookkeeping lock. Invalidation fences
    in-flight insertion without retaining permanent generation tombstones.
    Statistics expose counts only, never keys, paths or cached data.
    """

    def __init__(self, *, max_entries: int = 128, max_bytes: int = 8 * 1024 * 1024,
                 per_owner: int = 32, max_flights: int = 128, per_owner_bytes: int | None = None):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.per_owner = per_owner
        self.per_owner_bytes = max_bytes // 4 if per_owner_bytes is None else per_owner_bytes
        self.max_flights = max_flights
        self._entries: OrderedDict = OrderedDict()
        self._flights: dict[Hashable, _Flight] = {}
        self._lock = threading.RLock()
        self._bytes = 0
        self._counts = dict(hits=0, misses=0, waits=0, evictions=0, bypasses=0)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _drop(self, key: Hashable) -> None:
        item = self._entries.pop(key, None)
        if item is not None:
            self._bytes -= item[4]

    def invalidate(self, predicate: Callable[[Hashable], bool]) -> None:
        with self._lock:
            for key in list(self._entries):
                if predicate(key):
                    self._drop(key)
            for key, flight in self._flights.items():
                if predicate(key):
                    flight.invalid = True

    def clear(self) -> None:
        self.invalidate(lambda _: True)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {**self._counts, "entries": len(self._entries),
                    "estimated_bytes": self._bytes, "in_flight": len(self._flights)}

    def get_or_load(self, key: Hashable, loader: Callable[[], Any], *,
                    owner: Hashable, ttl: float, version: Any = None) -> tuple[Any, bool]:
        while True:
            cached = None
            hit = False
            bypass = False
            with self._lock:
                now = time.monotonic()
                for candidate, item in list(self._entries.items()):
                    if item[0] <= now:
                        self._drop(candidate)
                item = self._entries.get(key)
                if item is not None and item[1] == version:
                    self._entries.move_to_end(key)
                    self._counts["hits"] += 1
                    cached, hit = item[3], True
                else:
                    self._drop(key)
                    flight = self._flights.get(key)
                    if flight is not None and flight.thread != threading.get_ident():
                        self._counts["waits"] += 1
                        wait = flight.event
                    elif (flight is not None or len(self._flights) >= self.max_flights
                          or min(self.max_entries, self.max_bytes, self.per_owner) <= 0 or ttl <= 0):
                        self._counts["bypasses"] += 1
                        bypass = True
                    else:
                        flight = _Flight(threading.Event(), threading.get_ident())
                        self._flights[key] = flight
                        self._counts["misses"] += 1
                        break
            if hit:
                return copy.deepcopy(cached), True
            if bypass:
                return loader(), False
            wait.wait()
        try:
            result = loader()
            snapshot = copy.deepcopy(result)
            size = _weight((key, version, owner, snapshot))
            with self._lock:
                if not flight.invalid and size <= min(self.max_bytes, self.per_owner_bytes):
                    owned = [k for k, v in self._entries.items() if v[2] == owner]
                    owner_bytes = sum(self._entries[k][4] for k in owned)
                    while owned and (len(owned) >= self.per_owner or owner_bytes + size > self.per_owner_bytes):
                        oldest = owned.pop(0)
                        owner_bytes -= self._entries[oldest][4]
                        self._drop(oldest)
                        self._counts["evictions"] += 1
                    self._entries[key] = (time.monotonic() + ttl, version, owner, snapshot, size)
                    self._bytes += size
                    while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                        self._drop(next(iter(self._entries)))
                        self._counts["evictions"] += 1
                else:
                    self._counts["bypasses"] += 1
            return result, False
        finally:
            with self._lock:
                self._flights.pop(key, None)
                flight.event.set()


_source_cache = BoundedReadCache(max_entries=512, max_bytes=16 * 1024 * 1024, per_owner=64)
_MAX_SOURCE_BYTES = 512 * 1024


def _source_owner(path: Path) -> str:
    # User-owned files share a quota across all their source directories.
    for parent in path.parents:
        if parent.parent.name == "users":
            return os.path.normcase(str(parent))
    return os.path.normcase(str(path.parent))


def _stamp(path: Path) -> tuple[int, ...]:
    s = path.stat()
    return (s.st_mtime_ns, s.st_ctime_ns, s.st_size, s.st_ino, s.st_dev, s.st_mode)


class _SourceChanged(Exception):
    pass


def cached_read_text(path: Path, encoding: str = "utf-8") -> str:
    """Stat on every call; cache only small regular source files for <=5 seconds.

    Existence/authorization/path containment remain the caller's responsibility.
    Missing files and read failures are never negatively cached. Environment
    files deliberately bypass this cache. Continuous edits fall back to reading.
    """
    path = Path(os.path.abspath(path))
    key = (os.path.normcase(str(path)), encoding)
    if path.name == ".env":
        return path.read_text(encoding)
    for _ in range(2):
        try:
            stamp = _stamp(path)
        except OSError:
            _source_cache.invalidate(lambda k: k[0] == key[0])
            raise
        if stamp[2] > _MAX_SOURCE_BYTES or not path.is_file():
            _source_cache.invalidate(lambda k: k[0] == key[0])
            return path.read_text(encoding)

        def load() -> str:
            value = path.read_text(encoding)
            if _stamp(path) != stamp:
                raise _SourceChanged()
            return value

        try:
            value, _ = _source_cache.get_or_load(key, load, owner=_source_owner(path), ttl=5, version=stamp)
            if _stamp(path) == stamp:
                return value
        except _SourceChanged:
            continue
    return path.read_text(encoding)


def invalidate_source_cache(path: Path) -> None:
    key = os.path.normcase(os.path.abspath(path))
    _source_cache.invalidate(lambda k: k[0] == key)
