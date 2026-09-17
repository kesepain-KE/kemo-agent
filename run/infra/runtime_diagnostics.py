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
import sys
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
_terminal_capture_lock = threading.RLock()
_terminal_capture: tuple[Path, Any, Any, Any, Any] | None = None
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_TERMINAL_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|secret|password|authorization|cookie)\b\s*(?:=|:|\s+)\s*)([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)([^\s,;]+)")
_URL_CREDENTIAL_RE = re.compile(r"(://[^\s/:@]+:)([^\s@/]+)(@)")
_BOX_DRAWING_ONLY_RE = re.compile(r"^[\s┌┐└┘├┤─│]+$")
_MAX_TERMINAL_LINE_CHARS = 1000


def _root_key(root: Path) -> str:
    return os.path.normcase(str(root.resolve()))


def diagnostic_identifier(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value or ""))[:80]


def _terminal_text(value: Any) -> str:
    text = _ANSI_ESCAPE_RE.sub("", str(value or "")).replace("\x00", "").strip()
    if not text:
        return ""
    # The startup banner uses box-drawing borders for the physical terminal.
    # They are decorative rather than log content, so the Web terminal omits
    # border-only rows and unwraps the text between vertical border glyphs.
    if _BOX_DRAWING_ONLY_RE.fullmatch(text):
        return ""
    if text.startswith("│") and text.endswith("│"):
        text = text[1:-1].strip()
    text = _BEARER_RE.sub(r"\1***", text)
    text = _TERMINAL_SECRET_RE.sub(r"\1***", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1***\3", text)
    return text[:_MAX_TERMINAL_LINE_CHARS] + ("…" if len(text) > _MAX_TERMINAL_LINE_CHARS else "")


def record_terminal_output(root: Path, text: Any, *, stream: str = "stdout") -> None:
    """Record one safe, visible line from the start_web terminal.

    Terminal output is process-local and shared as ``__system__`` so every
    local user can inspect the same kemo-agent startup console.  It is never
    written to SQLite and deliberately redacts common credential shapes.
    """
    try:
        line = _terminal_text(text)
        if not line:
            return
        normalized_stream = "stderr" if stream == "stderr" else "stdout"
        source = "标准错误" if normalized_stream == "stderr" else "标准输出"
        entry = {
            "id": "console:" + uuid.uuid4().hex,
            "category": "terminal",
            "title": line,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "status": "error" if normalized_stream == "stderr" else "recorded",
            "stream": normalized_stream,
            "duration_ms": None,
            "detail": f"启动终端 · {source}",
            "source": "memory",
            "exit_code": None,
        }
        with _event_lock:
            _events.append((time.monotonic(), _root_key(root), "__system__", entry))
    except Exception:
        pass


class _TerminalOutputTee:
    """Mirror one visible text stream while forwarding it unchanged."""

    def __init__(self, root: Path, stream: str, original: Any):
        self._root = root
        self._stream = stream
        self._original = original
        self._pending = ""
        self._lock = threading.Lock()

    def write(self, value: str) -> int:
        written = self._original.write(value)
        with self._lock:
            self._pending += str(value)
            lines = self._pending.splitlines(keepends=True)
            self._pending = ""
            for line in lines:
                if line.endswith(("\n", "\r")):
                    record_terminal_output(self._root, line.rstrip("\r\n"), stream=self._stream)
                else:
                    self._pending = line
        return written if isinstance(written, int) else len(value)

    def flush(self) -> None:
        with self._lock:
            if self._pending:
                record_terminal_output(self._root, self._pending, stream=self._stream)
                self._pending = ""
        self._original.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def install_terminal_capture(root: Path) -> bool:
    """Capture the visible ``start_web`` terminal without hiding its output."""
    global _terminal_capture
    normalized_root = root.resolve()
    with _terminal_capture_lock:
        if _terminal_capture is not None:
            return _terminal_capture[0] == normalized_root
        stdout, stderr = sys.stdout, sys.stderr
        stdout_tee = _TerminalOutputTee(normalized_root, "stdout", stdout)
        stderr_tee = _TerminalOutputTee(normalized_root, "stderr", stderr)
        sys.stdout, sys.stderr = stdout_tee, stderr_tee
        _terminal_capture = (normalized_root, stdout, stderr, stdout_tee, stderr_tee)
        return True


def restore_terminal_capture() -> None:
    """Restore process streams after the start_web entry returns (mainly tests)."""
    global _terminal_capture
    with _terminal_capture_lock:
        if _terminal_capture is None:
            return
        _, stdout, stderr, stdout_tee, stderr_tee = _terminal_capture
        try:
            stdout_tee.flush()
            stderr_tee.flush()
        finally:
            if sys.stdout is stdout_tee:
                sys.stdout = stdout
            if sys.stderr is stderr_tee:
                sys.stderr = stderr
            _terminal_capture = None


def record_runtime_event(
    root: Path, user: str, *, category: str, name: str, status: str,
    duration_ms: int = 0, error_type: str = "", exit_code: int | None = None,
) -> None:
    """Best-effort observation must never change a tool's execution outcome."""
    try:
        if not user or category != "backend":
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
