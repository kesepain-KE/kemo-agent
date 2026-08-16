"""用户与系统隔离的精简 cron 任务存储，支持旧格式自动迁移。"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from run.cron_runtime_state import clear_cron_runtime, overlay_cron_runtime


BEIJING = ZoneInfo("Asia/Shanghai")
TASK_ID_RE = re.compile(r"^cron_[0-9a-f]{8}$")
TASK_STATUSES = frozenset({
    "enabled", "paused", "running", "completed", "failed", "cancelled",
})
TASK_TYPES = frozenset({"once", "daily", "recurring"})
SYSTEM_EXEC_MODE = "system"
EXEC_MODES = frozenset({"agent", "subagent", "function", SYSTEM_EXEC_MODE})
SYSTEM_TASK_IDS = frozenset({
    "memory_promotion",
    "memory_periodic_scan",
    "memory_daily_consolidate",
})
SYSTEM_TASK_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")

_STORE_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()
_LIST_CACHE: dict[
    tuple[str, str],
    tuple[tuple[tuple[str, int, int], ...], tuple[dict[str, Any], ...]],
] = {}
_LIST_CACHE_GUARD = threading.Lock()


def _store_lock(root: Path, user: str) -> threading.RLock:
    key = (str(root.resolve()).casefold(), user)
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


def _task_signature(paths: list[Path]) -> tuple[tuple[str, int, int], ...]:
    signature: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


class CronError(RuntimeError):
    pass


class CronNotFoundError(CronError):
    pass


class CronValidationError(CronError):
    pass


class CronConflictError(CronError):
    pass


def now_beijing() -> str:
    return datetime.now(BEIJING).isoformat()


def _generate_task_id() -> str:
    return f"cron_{uuid.uuid4().hex[:8]}"


def _task_dir(root: Path, user: str, *, system: bool = False) -> Path:
    if system:
        return root / "cron" / "task_cron_system"
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


def _beijing_iso(value: Any, *, field: str, allow_empty: bool = False) -> str:
    if value in (None, ""):
        if allow_empty:
            return ""
        raise CronValidationError(f"{field} 不能为空")
    if not isinstance(value, str):
        raise CronValidationError(f"{field} 必须是 ISO 时间字符串")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CronValidationError(f"{field} 不是有效 ISO 时间：{value!r}") from exc
    # 旧数据中的无时区时间按 UTC 解释；新写入始终转为北京时间。
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BEIJING).isoformat()


def _validate_task(data: dict[str, Any], *, system: bool | None = None) -> None:
    if not isinstance(data, dict):
        raise CronValidationError("任务必须是 JSON 对象")
    system_mode = bool(system) if system is not None else data.get("exec_mode") == SYSTEM_EXEC_MODE
    task_id = data.get("task_id")
    valid_task_id = (
        isinstance(task_id, str)
        and (TASK_ID_RE.fullmatch(task_id) or (system_mode and SYSTEM_TASK_ID_RE.fullmatch(task_id)))
    )
    if not valid_task_id:
        raise CronValidationError(f"task_id 无效：{task_id!r}")
    required_fields = ("title",) if system_mode else ("title", "prompt", "user")
    for field in required_fields:
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise CronValidationError(f"{field} 不能为空")
    task_type = data.get("type")
    if task_type not in TASK_TYPES:
        raise CronValidationError(f"type 无效：{task_type!r}")
    status = data.get("status")
    if status not in TASK_STATUSES:
        raise CronValidationError(f"任务状态无效：{status!r}")
    exec_mode = data.get("exec_mode")
    if exec_mode not in (EXEC_MODES | {SYSTEM_EXEC_MODE}):
        raise CronValidationError(f"exec_mode 无效：{exec_mode!r}")
    if system_mode and exec_mode != SYSTEM_EXEC_MODE:
        raise CronValidationError("系统任务必须使用 system 执行模式")
    if system_mode:
        action = data.get("action")
        if not isinstance(action, str) or not action.strip():
            raise CronValidationError("系统任务需要非空 action")
    elif exec_mode == SYSTEM_EXEC_MODE:
        raise CronValidationError("system 执行模式只能用于系统任务")
    if task_type == "recurring":
        interval = data.get("interval_seconds")
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
            raise CronValidationError("recurring 任务需要 interval_seconds >= 1")
        if "time" in data:
            raise CronValidationError("recurring 任务不能包含 time")
    elif task_type == "daily":
        time_str = data.get("time")
        if not isinstance(time_str, str) or not _TIME_RE.fullmatch(time_str):
            raise CronValidationError(f"daily 任务需要有效的 time（HH:MM）：{time_str!r}")
        hour, minute = (int(part) for part in time_str.split(":"))
        if hour > 23 or minute > 59:
            raise CronValidationError(f"daily 任务 time 超出有效范围：{time_str!r}")
        if "interval_seconds" in data:
            raise CronValidationError("daily 任务不能包含 interval_seconds")
    elif "interval_seconds" in data or "time" in data:
        raise CronValidationError("once 任务不能包含 interval_seconds 或 time")

    next_run = data.get("next_run_at")
    allow_empty_next = status in {"completed", "cancelled"}
    normalized_next = _beijing_iso(
        next_run,
        field="next_run_at",
        allow_empty=allow_empty_next,
    )
    if next_run != normalized_next:
        raise CronValidationError("next_run_at 必须使用北京时间（+08:00）")
    latest = data.get("latest_run_at", "")
    if latest != _beijing_iso(latest, field="latest_run_at", allow_empty=True):
        raise CronValidationError("latest_run_at 必须使用北京时间（+08:00）")
    created = data.get("created_at")
    if created != _beijing_iso(created, field="created_at"):
        raise CronValidationError("created_at 必须使用北京时间（+08:00）")

    allowed = {
        "task_id", "title", "prompt", "user", "type", "next_run_at",
        "latest_run_at", "status", "created_at",
        "exec_mode",
    }
    if system_mode:
        allowed.add("action")
    if task_type == "recurring":
        allowed.add("interval_seconds")
    elif task_type == "daily":
        allowed.add("time")
    unknown = set(data) - allowed
    if unknown:
        raise CronValidationError(f"任务包含已废弃或未知字段：{', '.join(sorted(unknown))}")


def normalize_task(
    *,
    task_id: str | None = None,
    title: str,
    prompt: str,
    user: str,
    type: str,
    interval_seconds: int | None = None,
    time: str | None = None,
    next_run_at: str = "",
    latest_run_at: str = "",
    status: str = "enabled",
    created_at: str = "",
    exec_mode: str = "agent",
    action: str | None = None,
) -> dict[str, Any]:
    """构造仅包含精简 schema 字段的任务。"""
    now = now_beijing()
    task: dict[str, Any] = {
        "task_id": task_id or _generate_task_id(),
        "title": title.strip() if isinstance(title, str) else title,
        "prompt": prompt.strip() if isinstance(prompt, str) else prompt,
        "user": user.strip() if isinstance(user, str) else user,
        "type": type,
        "next_run_at": _beijing_iso(
            next_run_at or ("" if status in {"completed", "cancelled"} else now),
            field="next_run_at",
            allow_empty=status in {"completed", "cancelled"},
        ),
        "latest_run_at": _beijing_iso(
            latest_run_at,
            field="latest_run_at",
            allow_empty=True,
        ),
        "status": status,
        "created_at": _beijing_iso(created_at or now, field="created_at"),
        "exec_mode": exec_mode,
    }
    if exec_mode == SYSTEM_EXEC_MODE:
        task["action"] = action or ""
    if type == "recurring":
        task["interval_seconds"] = 60 if interval_seconds is None else interval_seconds
    elif type == "daily":
        task["time"] = time or "00:00"
    _validate_task(task)
    return task


def _migrate_task(data: dict[str, Any], *, fallback_user: str) -> dict[str, Any]:
    """将旧嵌套 schema 或不规范时间转换为当前精简 schema。"""
    schedule = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}
    task_type = str(data.get("type") or schedule.get("type") or "recurring")
    status = str(data.get("status") or "enabled")
    next_run_at = data.get("next_run_at") or schedule.get("start_at") or now_beijing()
    latest_run_at = data.get("latest_run_at") or data.get("last_run_at") or ""
    created_at = data.get("created_at") or now_beijing()
    interval = data.get("interval_seconds", schedule.get("interval_seconds"))
    time_value = data.get("time", schedule.get("time"))
    old_system = bool(data.get("system_key")) or data.get("exec_mode") == SYSTEM_EXEC_MODE
    return normalize_task(
        task_id=data.get("task_id"),
        title=str(data.get("title") or "未命名定时任务"),
        prompt="" if old_system else str(data.get("prompt") or "执行定时任务"),
        user="" if old_system else str(data.get("user") or fallback_user),
        type=task_type,
        interval_seconds=interval,
        time=time_value,
        next_run_at=str(next_run_at),
        latest_run_at=str(latest_run_at),
        status=status,
        created_at=str(created_at),
        exec_mode=SYSTEM_EXEC_MODE if old_system else str(data.get("exec_mode") or "agent"),
        action=str(data.get("action") or _legacy_system_action(data.get("system_key"), data.get("task_id"))) if old_system else None,
    )


def _legacy_system_action(system_key: Any, task_id: Any) -> str:
    key = str(system_key or "")
    if key.endswith("periodic_scan"):
        return "periodic_scan"
    if key.endswith("daily_consolidate"):
        return "daily_consolidate"
    if key.endswith("memory_promotion"):
        return "memory_promotion"
    return str(task_id or "")


class CronStore:
    """按用户隔离的 cron 定义存储，读取时叠加进程内运行态。"""

    def __init__(self, root: Path, user: str, *, system: bool = False) -> None:
        self.root = root.resolve()
        self.user = user
        self._system = system
        self._dir = _task_dir(self.root, self.user, system=self._system)
        lock_user = f"system:{self.user}" if self._system else self.user
        self._lock = _store_lock(self.root, lock_user)

    def _path(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.json"

    def _with_runtime(self, task: dict[str, Any]) -> dict[str, Any]:
        return overlay_cron_runtime(
            self.root,
            self.user,
            self._system,
            task,
        )

    def _load(self, path: Path, *, migrate: bool = True) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CronError(f"任务文件损坏：{path.stem}（{exc}）") from exc
        if not isinstance(data, dict):
            raise CronError(f"任务文件损坏：{path.stem}（根节点不是对象）")
        try:
            _validate_task(data, system=self._system)
            return self._with_runtime(data)
        except CronValidationError:
            if not migrate:
                raise
        migrated = _migrate_task(data, fallback_user=self.user)
        _validate_task(migrated, system=self._system)
        _atomic_write(path, migrated)
        return self._with_runtime(migrated)

    def create(self, task: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            _validate_task(task, system=self._system)
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._path(task["task_id"])
            if path.exists():
                raise CronConflictError(f"定时任务已存在：{task['task_id']}")
            clear_cron_runtime(
                self.root, self.user, self._system, str(task["task_id"])
            )
            _atomic_write(path, task)
            return dict(task)

    def read(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._path(task_id)
            if not path.exists():
                raise CronNotFoundError(f"定时任务不存在：{task_id}")
            return self._load(path)

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._dir.is_dir():
                return []
            pattern = "*.json" if self._system else "cron_*.json"
            paths = sorted(self._dir.glob(pattern), key=lambda item: item.name)
            signature = _task_signature(paths)
            cache_key = (str(self._dir.resolve()).casefold(), pattern)
            with _LIST_CACHE_GUARD:
                cached = _LIST_CACHE.get(cache_key)
                if cached is not None and cached[0] == signature:
                    return [self._with_runtime(item) for item in cached[1]]
            tasks: list[dict[str, Any]] = []
            for path in paths:
                try:
                    tasks.append(self._load(path))
                except (CronError, CronValidationError):
                    continue
            with _LIST_CACHE_GUARD:
                _LIST_CACHE[cache_key] = (
                    _task_signature(paths),
                    tuple(copy.deepcopy(tasks)),
                )
            return [self._with_runtime(item) for item in tasks]

    def update(
        self,
        task_id: str,
        mutator: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        clear_runtime: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            path = self._path(task_id)
            if not path.exists():
                raise CronNotFoundError(f"定时任务不存在：{task_id}")
            current = self._load(path)
            updated = mutator(dict(current))
            if not isinstance(updated, dict):
                raise CronError("mutator 必须返回 dict")
            _validate_task(updated, system=self._system)
            _atomic_write(path, updated)
            if clear_runtime:
                clear_cron_runtime(self.root, self.user, self._system, task_id)
            return updated

    def delete(self, task_id: str) -> bool:
        with self._lock:
            path = self._path(task_id)
            if not path.exists():
                return False
            path.unlink()
            clear_cron_runtime(self.root, self.user, self._system, task_id)
            return True

    def recover_interrupted(self) -> list[str]:
        """启动时将中断的 running 任务恢复为 enabled。"""
        recovered: list[str] = []
        with self._lock:
            if not self._dir.is_dir():
                return recovered
            pattern = "*.json" if self._system else "cron_*.json"
            for path in sorted(self._dir.glob(pattern), key=lambda item: item.name):
                try:
                    data = self._load(path)
                except (CronError, CronValidationError):
                    continue
                if data.get("status") != "running":
                    continue
                data["status"] = "enabled"
                data["next_run_at"] = now_beijing()
                _validate_task(data, system=self._system)
                _atomic_write(path, data)
                clear_cron_runtime(self.root, self.user, self._system, str(data["task_id"]))
                recovered.append(str(data.get("task_id") or path.stem))
        return recovered
