"""Background maintenance for memory review and context compaction."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from provider.factory import create_provider
from run.agent_runner import AgentRunner
from run.config import load_config
from run.engine import compress_context, context_status
from run.history import load_window
from run.memory import MemoryStore, contains_sensitive_credential
from run.tools import ToolRegistry, discover_tools
from run.users import list_users


BEIJING = ZoneInfo("Asia/Shanghai")
CONTEXT_REVIEW_INTERVAL = timedelta(hours=1)
IMPORTANT_MEMORY_INPUT_LIMIT = 200


class MaintenanceError(RuntimeError):
    pass


def _parse_daily_time(value: Any) -> tuple[int, int]:
    text = str(value or "02:00").strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise MaintenanceError("agents.daily_memory_review_time 必须是 HH:MM")
    try:
        hour, minute = (int(part) for part in parts)
    except ValueError as exc:
        raise MaintenanceError("agents.daily_memory_review_time 必须是 HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise MaintenanceError("agents.daily_memory_review_time 必须是有效的北京时间")
    return hour, minute


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content.rstrip())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _history_sessions(root: Path, user: str) -> list[dict[str, str]]:
    history = root / "users" / user / "history"
    if not history.is_dir():
        return []
    latest: dict[tuple[str, str], tuple[str, dict[str, str]]] = {}
    for directory in history.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            data = load_window(directory).get("data") or {}
        except Exception:
            continue
        source = str(data.get("source") or "")
        session_id = str(data.get("session_id") or "")
        if not source or not session_id:
            continue
        value = {
            "source": source,
            "session_id": session_id,
            "window": directory.name,
        }
        updated = str(data.get("updated_at") or "")
        key = (source, session_id)
        if key not in latest or updated > latest[key][0]:
            latest[key] = (updated, value)
    return [
        value
        for _, value in sorted(
            latest.values(),
            key=lambda item: item[0],
            reverse=True,
        )
    ]


class MaintenanceScheduler:
    """Run system-owned maintenance alongside, but independently from, cron."""

    def __init__(
        self,
        root: Path,
        *,
        poll_interval: float = 30.0,
        provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
        tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.poll_interval = max(1.0, float(poll_interval))
        self.provider_factory = provider_factory
        self.tool_registry_factory = tool_registry_factory
        self.on_error = on_error
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._next_important_review: dict[str, datetime] = {}
        self._next_context_review: dict[str, datetime] = {}
        self._daily_review_dates: dict[str, str] = {}
        self._last_results: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="system-maintenance",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float = 10.0) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "last_results": json.loads(
                    json.dumps(self._last_results, ensure_ascii=False, default=str)
                ),
            }

    def scan_once(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        results: dict[str, Any] = {}
        for user in list_users(self.root):
            if self._stop_event.is_set():
                break
            try:
                results[user] = self._scan_user(user, current, force=force)
            except Exception as exc:
                self._report_error(f"maintenance:{user}", exc)
                results[user] = {
                    "error": {
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    }
                }
        with self._lock:
            self._last_results = results
        return results

    def _scan_user(
        self,
        user: str,
        current: datetime,
        *,
        force: bool,
    ) -> dict[str, Any]:
        config = load_config(user, self.root)
        store = MemoryStore(self.root, user, config)
        result: dict[str, Any] = {"memory_lifecycle": store.review_due(now=current)}
        agents = config.get("agents") or {}

        review_hours = agents.get("important_memory_review_hours", 3)
        if isinstance(review_hours, bool) or not isinstance(review_hours, (int, float)):
            raise MaintenanceError("agents.important_memory_review_hours 必须是正数")
        review_hours = float(review_hours)
        if review_hours <= 0:
            raise MaintenanceError("agents.important_memory_review_hours 必须是正数")
        next_important = self._next_important_review.setdefault(
            user, current + timedelta(hours=review_hours)
        )
        if force or current >= next_important:
            result["important_memory"] = self._review_important_memory(
                user, config, store
            )
            self._next_important_review[user] = current + timedelta(hours=review_hours)

        local = current.astimezone(BEIJING)
        hour, minute = _parse_daily_time(agents.get("daily_memory_review_time", "02:00"))
        daily_key = local.date().isoformat()
        if (
            (force or (local.hour, local.minute) >= (hour, minute))
            and self._daily_review_dates.get(user) != daily_key
        ):
            result["daily_memory_review"] = store.review_due(now=current)
            self._daily_review_dates[user] = daily_key

        next_context = self._next_context_review.setdefault(
            user, current + CONTEXT_REVIEW_INTERVAL
        )
        if force or current >= next_context:
            result["context"] = self._review_contexts(user)
            self._next_context_review[user] = current + CONTEXT_REVIEW_INTERVAL
        return result

    def _review_important_memory(
        self,
        user: str,
        config: dict[str, Any],
        store: MemoryStore,
    ) -> dict[str, Any]:
        temporary = [
            item
            for tier in ("half_year", "one_month", "seven_days")
            for item in store.load_tier(tier)
        ]
        temporary.sort(
            key=lambda item: (-int(item.get("weight", 0)), str(item.get("filename", "")))
        )
        important_path = self.root / "users" / user / "memory_temporary_important.md"
        try:
            existing = important_path.read_text("utf-8").strip()
        except FileNotFoundError:
            existing = ""
        permanent = store.load_tier("permanent")
        if not temporary and not existing:
            return {"status": "skipped", "reason": "no_temporary_memory"}
        result = AgentRunner(
            self.root,
            user,
            config=config,
            provider_factory=self.provider_factory,
        ).run(
            "memory_temporary_important",
            {
                "temporary_memories": temporary[:IMPORTANT_MEMORY_INPUT_LIMIT],
                "existing_important_memory": existing,
                "permanent_memories": permanent,
            },
            cancel_event=self._stop_event,
        )
        content = result.data.get("content")
        if not isinstance(content, str):
            raise MaintenanceError("memory_temporary_important 输出缺少 content 字符串")
        if contains_sensitive_credential(content):
            raise MaintenanceError("重要记忆审阅结果包含疑似敏感凭据，已拒绝持久化")
        if content.strip():
            _atomic_text(important_path, content)
        else:
            important_path.unlink(missing_ok=True)
        return {
            "status": "completed",
            "items_considered": min(len(temporary), IMPORTANT_MEMORY_INPUT_LIMIT),
            "chars": len(content.strip()),
        }

    def _review_contexts(self, user: str) -> dict[str, Any]:
        reviewed = 0
        compressed: list[str] = []
        for session in _history_sessions(self.root, user):
            if self._stop_event.is_set():
                break
            request = {
                "user": user,
                "source": session["source"],
                "session_id": session["session_id"],
            }
            try:
                status = context_status(
                    request,
                    root=self.root,
                    tool_registry_factory=self.tool_registry_factory,
                )
                reviewed += 1
                context = status.get("context") or {}
                if not (
                    context.get("round_limit_triggered")
                    or context.get("token_limit_triggered")
                ):
                    continue
                compress_context(
                    request,
                    root=self.root,
                    provider_factory=self.provider_factory,
                    tool_registry_factory=self.tool_registry_factory,
                    cancel_event=self._stop_event,
                )
                compressed.append(session["window"])
            except Exception as exc:
                self._report_error(f"maintenance:{user}:context", exc)
        return {"reviewed": reviewed, "compressed": compressed}

    def _report_error(self, component: str, exc: Exception) -> None:
        if self.on_error is not None:
            self.on_error(component, exc)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                self._report_error("maintenance", exc)
            self._stop_event.wait(self.poll_interval)
