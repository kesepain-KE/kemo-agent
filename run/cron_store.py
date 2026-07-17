"""用户隔离的 cron 任务存储，具有原子写入和版本检查。"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1
TASK_ID_RE = re.compile(r"^cron_[0-9a-f]{8}$")
TASK_STATUSES = frozenset({
    "enabled", "paused", "running", "completed", "failed", "cancelled",
})
SCHEDULE_TYPES = frozenset({"once", "daily", "recurring"})
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")

_STORE_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


def _store_lock(root: Path, user: str) -> threading.RLock:
    key = (str(root.resolve()).casefold(), user)
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


class CronError(RuntimeError):
    pass


class CronNotFoundError(CronError):
    pass


class CronValidationError(CronError):
    pass


class CronConflictError(CronError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_task_id() -> str:
    return f"cron_{uuid.uuid4().hex[:8]}"


def _task_dir(root: Path, user: str) -> Path:
    return root / "users" / user / "task_cron"


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, path)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _validate_schedule(schedule: dict[str, Any]) -> None:
    if not isinstance(schedule, dict):
        raise CronValidationError("schedule 必须是对象")
    stype = schedule.get("type")
    if not isinstance(stype, str) or stype not in SCHEDULE_TYPES:
        raise CronValidationError(f"schedule.type 无效：{stype!r}")
    if stype == "daily":
        time_str = schedule.get("time")
        if not isinstance(time_str, str) or not _TIME_RE.fullmatch(time_str):
            raise CronValidationError(f"daily 任务需要有效的 time（HH:MM）：{time_str!r}")
        hour, minute = (int(x) for x in time_str.split(":"))
        if hour > 23 or minute > 59:
            raise CronValidationError(f"daily 任务 time 超出有效范围：{time_str!r}")
        tz = schedule.get("timezone")
        if not isinstance(tz, str) or not tz.strip():
            raise CronValidationError("daily 任务需要 timezone")
    elif stype == "once":
        start_at = schedule.get("start_at")
        if not isinstance(start_at, str) or not start_at.strip():
            raise CronValidationError("once 任务需要 start_at（UTC ISO）")
    elif stype == "recurring":
        interval = schedule.get("interval_seconds")
        if not isinstance(interval, int) or interval < 60:
            raise CronValidationError("recurring 任务需要 interval_seconds >= 60")


def _validate_task(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise CronValidationError("任务必须是 JSON 对象")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CronValidationError(f"schema_version 必须为 {SCHEMA_VERSION}")
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise CronValidationError(f"task_id 无效：{task_id!r}")
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        raise CronValidationError("title 不能为空")
    if not isinstance(data.get("prompt"), str) or not data["prompt"].strip():
        raise CronValidationError("prompt 不能为空")
    if not isinstance(data.get("user"), str) or not data["user"].strip():
        raise CronValidationError("user 不能为空")
    status = data.get("status")
    if not isinstance(status, str) or status not in TASK_STATUSES:
        raise CronValidationError(f"任务状态无效：{status!r}")
    _validate_schedule(data.get("schedule") or {})
    if not isinstance(data.get("next_run_at"), str):
        raise CronValidationError("next_run_at 必须是字符串")
    if not isinstance(data.get("revision"), int) or data["revision"] < 1:
        raise CronValidationError("revision 必须是正整数")
    run_count = data.get("run_count")
    if not isinstance(run_count, int) or run_count < 0:
        raise CronValidationError("run_count 必须是非负整数")


def normalize_task(
    *,
    task_id: str | None = None,
    title: str,
    prompt: str,
    user: str,
    schedule: dict[str, Any],
    source: str = "cli",
    session_id: str = "cron",
    next_run_at: str = "",
    status: str = "enabled",
) -> dict[str, Any]:
    """Build a fully validated cron task dict ready for storage."""
    tid = task_id or _generate_task_id()
    now = _now()
    task = {
        "schema_version": SCHEMA_VERSION,
        "task_id": tid,
        "title": title,
        "prompt": prompt,
        "user": user,
        "source": source,
        "session_id": session_id,
        "schedule": dict(schedule),
        "status": status,
        "next_run_at": next_run_at or now,
        "last_run_at": "",
        "last_result": None,
        "last_error": None,
        "run_count": 0,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    }
    _validate_task(task)
    return task


class CronStore:
    """Disk-authoritative cron task storage with per-user locking."""

    def __init__(self, root: Path, user: str) -> None:
        self.root = root.resolve()
        self.user = user
        self._dir = _task_dir(self.root, self.user)
        self._lock = _store_lock(self.root, self.user)

    def _path(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.json"

    def create(self, task: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            task_id = task["task_id"]
            path = self._path(task_id)
            if path.exists():
                raise CronConflictError(f"定时任务已存在：{task_id}")
            _atomic_write(path, task)
            return task

    def read(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._path(task_id)
            if not path.exists():
                raise CronNotFoundError(f"定时任务不存在：{task_id}")
            try:
                data = json.loads(path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CronError(f"任务文件损坏：{task_id}（{exc}）") from exc
            return data

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._dir.is_dir():
                return []
            tasks: list[dict[str, Any]] = []
            for path in sorted(self._dir.glob("cron_*.json"), key=lambda p: p.name):
                try:
                    data = json.loads(path.read_text("utf-8"))
                    if isinstance(data, dict) and "task_id" in data:
                        tasks.append(data)
                except (OSError, json.JSONDecodeError):
                    continue
            return tasks

    def update(self, task_id: str, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Atomically read, mutate, validate and persist a task."""
        with self._lock:
            path = self._path(task_id)
            if not path.exists():
                raise CronNotFoundError(f"定时任务不存在：{task_id}")
            try:
                current = json.loads(path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CronError(f"任务文件损坏：{task_id}（{exc}）") from exc
            updated = mutator(current)
            if not isinstance(updated, dict):
                raise CronError("mutator 必须返回 dict")
            updated["revision"] = int(current.get("revision", 1)) + 1
            updated["updated_at"] = _now()
            _validate_task(updated)
            _atomic_write(path, updated)
            return updated

    def delete(self, task_id: str) -> bool:
        with self._lock:
            path = self._path(task_id)
            if not path.exists():
                return False
            path.unlink()
            return True

    def recover_interrupted(self) -> list[str]:
        """On startup, find running tasks and reset them to enabled."""
        recovered: list[str] = []
        with self._lock:
            if not self._dir.is_dir():
                return recovered
            for path in sorted(self._dir.glob("cron_*.json"), key=lambda p: p.name):
                try:
                    data = json.loads(path.read_text("utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("status") == "running":
                    data["status"] = "enabled"
                    data["next_run_at"] = _now()
                    data["revision"] = int(data.get("revision", 1)) + 1
                    data["updated_at"] = _now()
                    _atomic_write(path, data)
                    recovered.append(data.get("task_id", path.stem))
        return recovered
