"""Background cron scheduler: scans user tasks, claims and executes due tasks."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.config import load_config
from run.cron_store import CronError, CronStore
from run.tools import ToolRegistry, discover_tools
from cron.executor import execute_cron_task
from cron.schedule import is_due


class CronScheduler:
    """Background thread that periodically scans and executes due cron tasks.

    The scheduler is disk-authoritative: it reads task files from disk on each
    scan cycle and uses atomic file-level claiming to prevent double execution.
    """

    def __init__(
        self,
        root: Path,
        *,
        poll_interval: float = 30.0,
        provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
        tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
        on_task_executed: Callable[[str, str, dict[str, Any]], None] | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.poll_interval = poll_interval
        self.provider_factory = provider_factory
        self.tool_registry_factory = tool_registry_factory
        self.on_task_executed = on_task_executed
        self.on_error = on_error

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="cron-scheduler",
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
            self._thread = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def scan_once(self) -> int:
        """Run one scan cycle. Returns the number of tasks executed."""
        executed = 0
        from run.users import list_users

        users = list_users(self.root)
        now = datetime.now(timezone.utc)

        for user in users:
            if self._stop_event.is_set():
                break
            try:
                executed += self._scan_user(user, now)
            except Exception as exc:
                if self.on_error:
                    self.on_error(user, exc)

        return executed

    def _scan_user(self, user: str, now: datetime) -> int:
        store = CronStore(self.root, user)
        tasks = store.list_tasks()
        executed = 0

        for task in tasks:
            if self._stop_event.is_set():
                break

            task_id = task.get("task_id", "")
            status = task.get("status", "")
            next_run = task.get("next_run_at", "")

            if status not in ("enabled", "failed"):
                continue

            # failed tasks are retried on next scan (not just when due)
            check_time = now
            if status == "failed":
                check_time = now  # failed tasks get retried immediately
            else:
                if not is_due(next_run, now=now):
                    continue

            # Attempt to claim and execute
            try:
                result = execute_cron_task(
                    root=self.root,
                    user=user,
                    task_id=task_id,
                    provider_factory=self.provider_factory,
                    tool_registry_factory=self.tool_registry_factory,
                    cancel_event=self._stop_event,
                )
                executed += 1
                if self.on_task_executed:
                    self.on_task_executed(user, task_id, result)
            except Exception as exc:
                if self.on_error:
                    self.on_error(user, exc)

        return executed

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception:
                pass
            self._stop_event.wait(self.poll_interval)


def recover_all(root: Path) -> list[str]:
    """Recover interrupted tasks for all users on startup."""
    from run.users import list_users

    recovered: list[str] = []
    for user in list_users(root):
        store = CronStore(root, user)
        try:
            recovered.extend(store.recover_interrupted())
        except Exception:
            pass
    return recovered
