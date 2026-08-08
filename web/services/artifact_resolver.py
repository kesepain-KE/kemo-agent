"""Bounded lookup for generated download artifacts that may have been moved."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from web.errors import InvalidRequestError, NotFoundError, WebServiceError
from web.services._paths import _reject_link_path, _safe_relative_target


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    relative_path: str = ""
    mtime_ns: int | None = None
    expires_at: float = 0.0
    failure: str = ""


class DownloadArtifactResolver:
    """Resolve checksum-addressed artifacts with bounded cache and fallback work."""

    def __init__(
        self,
        *,
        max_cache_entries: int = 256,
        negative_ttl_seconds: float = 5.0,
        max_scanned_files: int = 20_000,
        max_hash_candidates: int = 128,
    ) -> None:
        self.max_cache_entries = max(1, int(max_cache_entries))
        self.negative_ttl_seconds = max(0.1, float(negative_ttl_seconds))
        self.max_scanned_files = max(1, int(max_scanned_files))
        self.max_hash_candidates = max(1, int(max_hash_candidates))
        self._cache: OrderedDict[tuple[str, str, int], _CacheEntry] = OrderedDict()
        self._cache_lock = threading.RLock()
        self._scan_lock = threading.Lock()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _cached(self, key: tuple[str, str, int]) -> _CacheEntry | None:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            self._cache.move_to_end(key)
            if entry.failure and entry.expires_at <= time.monotonic():
                self._cache.pop(key, None)
                return None
            return entry

    def _remember(self, key: tuple[str, str, int], entry: _CacheEntry) -> None:
        with self._cache_lock:
            self._cache[key] = entry
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_cache_entries:
                self._cache.popitem(last=False)

    def _remember_failure(self, key: tuple[str, str, int], failure: str) -> None:
        self._remember(
            key,
            _CacheEntry(
                expires_at=time.monotonic() + self.negative_ttl_seconds,
                failure=failure,
            ),
        )

    @staticmethod
    def _raise_failure(failure: str) -> None:
        if failure == "scan_limit":
            raise WebServiceError(
                "生成产物检索范围过大，已停止递归扫盘；请提供当前相对路径后重试"
            )
        raise NotFoundError("生成产物不存在、已被删除或内容已经改变")

    def resolve(
        self,
        directory: Path,
        checksum: str,
        *,
        path: Any,
        expected_size: int,
    ) -> Path:
        directory = directory.resolve()
        key = (str(directory), checksum, expected_size)
        cached = self._cached(key)
        raw_path = str(path or "").replace("\\", "/").strip("/")
        hints: list[tuple[str, int | None]] = []
        if raw_path:
            hints.append((raw_path, None))
        if cached and cached.relative_path and cached.relative_path != raw_path:
            hints.append((cached.relative_path, cached.mtime_ns))

        checked: set[Path] = set()

        def matches(candidate: Path, *, cached_mtime_ns: int | None = None) -> bool:
            try:
                resolved = candidate.resolve()
                if resolved in checked:
                    return False
                checked.add(resolved)
                _reject_link_path(directory, candidate)
                if candidate.is_symlink() or not candidate.is_file():
                    return False
                stat = candidate.stat()
                if stat.st_size != expected_size:
                    return False
                if cached_mtime_ns is not None and stat.st_mtime_ns == cached_mtime_ns:
                    return True
                return self._file_sha256(candidate) == checksum
            except (InvalidRequestError, OSError, ValueError):
                return False

        for hint, cached_mtime_ns in hints:
            try:
                relative, candidate = _safe_relative_target(directory, hint)
            except InvalidRequestError:
                continue
            if matches(candidate, cached_mtime_ns=cached_mtime_ns):
                self._remember(
                    key,
                    _CacheEntry(
                        relative_path=relative,
                        mtime_ns=candidate.stat().st_mtime_ns,
                    ),
                )
                return candidate

        if cached and cached.failure:
            self._raise_failure(cached.failure)

        with self._scan_lock:
            cached = self._cached(key)
            if cached and cached.failure:
                self._raise_failure(cached.failure)
            if cached and cached.relative_path:
                try:
                    _, candidate = _safe_relative_target(
                        directory, cached.relative_path
                    )
                except InvalidRequestError:
                    candidate = None
                if candidate is not None and matches(
                    candidate, cached_mtime_ns=cached.mtime_ns
                ):
                    return candidate

            scanned_files = 0
            hash_candidates = 0
            if directory.is_dir():
                for current_root, directories, filenames in os.walk(
                    directory, followlinks=False
                ):
                    current = Path(current_root)
                    directories[:] = [
                        child
                        for child in directories
                        if not (current / child).is_symlink()
                        and not getattr(
                            current / child, "is_junction", lambda: False
                        )()
                    ]
                    for filename in filenames:
                        scanned_files += 1
                        if scanned_files > self.max_scanned_files:
                            self._remember_failure(key, "scan_limit")
                            self._raise_failure("scan_limit")
                        candidate = current / filename
                        try:
                            if candidate.stat().st_size != expected_size:
                                continue
                        except OSError:
                            continue
                        hash_candidates += 1
                        if hash_candidates > self.max_hash_candidates:
                            self._remember_failure(key, "scan_limit")
                            self._raise_failure("scan_limit")
                        if not matches(candidate):
                            continue
                        relative_path = candidate.relative_to(directory).as_posix()
                        self._remember(
                            key,
                            _CacheEntry(
                                relative_path=relative_path,
                                mtime_ns=candidate.stat().st_mtime_ns,
                            ),
                        )
                        return candidate

            self._remember_failure(key, "missing")
            self._raise_failure("missing")

