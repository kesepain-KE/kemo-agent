"""后台 cron 调度器：扫描用户任务并统一交给主智能体执行。"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from cron.executor import execute_cron_task
from cron.schedule import compute_next_run, is_due
from provider.factory import create_provider
from run.cron_store import CronStore, CronValidationError, normalize_task
from run.tools import ToolRegistry, discover_tools


BEIJING = ZoneInfo("Asia/Shanghai")
MEMORY_PERIODIC_TASK_ID = "memory_periodic_scan"
MEMORY_DAILY_TASK_ID = "memory_daily_consolidate"
MEMORY_PROMOTION_TASK_ID = "memory_promotion"
# 仅保留旧导入名，持久化 schema 已不再包含 system_key。
MEMORY_PERIODIC_SYSTEM_KEY = MEMORY_PERIODIC_TASK_ID
MEMORY_DAILY_SYSTEM_KEY = MEMORY_DAILY_TASK_ID
MEMORY_PROMOTION_SYSTEM_KEY = MEMORY_PROMOTION_TASK_ID

_PERIODIC_TITLE = "临时重要记忆定时巡检"
_DAILY_TITLE = "临时重要记忆每日整理"
_PROMOTION_TITLE = "记忆碎片到期晋升检查"


def _system_result_summary(result: Any) -> dict[str, Any]:
    """Keep system-cron diagnostics useful without persisting prompt bodies."""

    if not isinstance(result, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in ("status", "action", "model", "requested"):
        value = result.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
    for key in ("created", "updated", "forgotten", "rejected", "deleted", "applied"):
        value = result.get(key)
        if isinstance(value, list):
            summary[key] = [str(item) for item in value[:100]]
    promotions = result.get("promotions")
    if isinstance(promotions, list):
        summary["promotions"] = [
            {
                name: item.get(name)
                for name in ("from_tier", "to_tier", "filename", "merged_with", "skill_created")
                if name in item
            }
            for item in promotions[:100]
            if isinstance(item, dict)
        ]
    nested = result.get("data")
    if isinstance(nested, dict):
        nested_summary = _system_result_summary(nested)
        if nested_summary:
            summary["data"] = nested_summary
    return summary


def _append_system_execution(
    root: Path,
    *,
    user: str,
    task_id: str,
    executed_at: datetime,
    duration_ms: int,
    result: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> None:
    """Append one bounded, user-scoped system-cron execution record."""

    status = "failed" if error is not None else str((result or {}).get("status") or "completed")
    if status == "completed":
        status = "success"
    record = {
        "schema_version": 1,
        "executed_at": executed_at.astimezone(BEIJING).isoformat(),
        "user": user,
        "task_id": task_id,
        "status": status,
        "duration_ms": max(0, int(duration_ms)),
        "result": _system_result_summary(result),
        "error": (
            {"type": type(error).__name__, "message": str(error)}
            if error is not None
            else None
        ),
    }
    directory = root / "cron" / "task_cron_system" / "log"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{executed_at.astimezone(BEIJING):%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        # Diagnostics persistence must never stop the scheduler itself.
        return


def _memory_task_specs(config: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    agents = config.get("agents") or {}
    review_hours = agents.get("important_memory_review_hours", 3)
    if (
        isinstance(review_hours, bool)
        or not isinstance(review_hours, (int, float))
        or review_hours <= 0
    ):
        raise CronValidationError("agents.important_memory_review_hours 必须是正数")
    interval_seconds = int(float(review_hours) * 3600)
    if interval_seconds < 60:
        raise CronValidationError("agents.important_memory_review_hours 对应间隔不能小于 60 秒")

    daily_time = agents.get("daily_memory_review_time", "02:00")
    if not isinstance(daily_time, str):
        raise CronValidationError("agents.daily_memory_review_time 必须是 HH:MM 字符串")

    return (
        {
            "task_id": MEMORY_PERIODIC_TASK_ID,
            "title": _PERIODIC_TITLE,
            "type": "recurring",
            "interval_seconds": interval_seconds,
            "exec_mode": "system",
            "action": "periodic_scan",
        },
        {
            "task_id": MEMORY_DAILY_TASK_ID,
            "title": _DAILY_TITLE,
            "type": "daily",
            "time": daily_time,
            "exec_mode": "system",
            "action": "daily_consolidate",
        },
    )


def _promotion_spec() -> dict[str, Any]:
    return {
        "task_id": MEMORY_PROMOTION_TASK_ID,
        "title": _PROMOTION_TITLE,
        "type": "recurring",
        "interval_seconds": 30,
        "exec_mode": "system",
        "action": "memory_promotion",
    }


def _same_schedule(task: dict[str, Any], spec: dict[str, Any]) -> bool:
    if task.get("type") != spec["type"]:
        return False
    if spec["type"] == "recurring":
        return task.get("interval_seconds") == spec["interval_seconds"]
    if spec["type"] == "daily":
        return task.get("time") == spec["time"]
    return True


def _reconcile_system_task(
    store: CronStore,
    current: dict[str, Any] | None,
    *,
    spec: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    if current is None:
        draft: dict[str, Any] = {
            "type": spec["type"],
            "next_run_at": "",
        }
        if spec["type"] == "recurring":
            draft["interval_seconds"] = spec["interval_seconds"]
        elif spec["type"] == "daily":
            draft["time"] = spec["time"]
        draft["next_run_at"] = compute_next_run(draft, after=now)
        return store.create(
            normalize_task(
                task_id=spec["task_id"],
                title=spec["title"],
                prompt="",
                user="",
                type=spec["type"],
                interval_seconds=spec.get("interval_seconds"),
                time=spec.get("time"),
                next_run_at=draft["next_run_at"],
                exec_mode=spec["exec_mode"],
                action=spec["action"],
            )
        )

    inactive = current.get("status") not in {"enabled", "failed"}
    schedule_changed = not _same_schedule(current, spec)
    changed = (
        inactive
        or schedule_changed
        or current.get("title") != spec["title"]
        or current.get("exec_mode") != spec["exec_mode"]
        or current.get("action") != spec["action"]
    )
    if not changed:
        return current

    def mutate(task: dict[str, Any]) -> dict[str, Any]:
        task.update({
            "title": spec["title"],
            "prompt": "",
            "user": "",
            "type": spec["type"],
            "exec_mode": spec["exec_mode"],
            "action": spec["action"],
        })
        task.pop("interval_seconds", None)
        task.pop("time", None)
        if spec["type"] == "recurring":
            task["interval_seconds"] = spec["interval_seconds"]
        elif spec["type"] == "daily":
            task["time"] = spec["time"]
        if inactive:
            task["status"] = "enabled"
        if inactive or schedule_changed:
            task["next_run_at"] = compute_next_run(task, after=now)
        return task

    return store.update(current["task_id"], mutate)


def ensure_memory_maintenance_tasks(
    root: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """创建或校准两个临时重要记忆维护任务。"""
    store = CronStore(root, "__system__", system=True)
    tasks = store.list_tasks()
    by_id = {task.get("task_id"): task for task in tasks}
    now = datetime.now(BEIJING)
    return [
        _reconcile_system_task(
            store,
            by_id.get(spec["task_id"]),
            spec=spec,
            now=now,
        )
        for spec in _memory_task_specs(config)
    ]


def ensure_memory_promotion_task(
    root: Path,
) -> dict[str, Any]:
    """创建或校准 30 秒一次的记忆晋升扫描任务。"""
    store = CronStore(root, "__system__", system=True)
    tasks = store.list_tasks()
    spec = _promotion_spec()
    return _reconcile_system_task(
        store,
        next((task for task in tasks if task.get("task_id") == spec["task_id"]), None),
        spec=spec,
        now=datetime.now(BEIJING),
    )


def cleanup_old_system_tasks(root: Path, user: str) -> int:
    """删除旧版散落在用户目录、带有 system_key 的系统任务。"""
    directory = root / "users" / user / "task_cron"
    if not directory.is_dir():
        return 0
    deleted = 0
    for path in directory.glob("cron_*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("system_key"):
            try:
                path.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted


class CronScheduler:
    """后台线程按磁盘状态扫描、领取并执行到期任务。"""

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
        from run.users import list_users

        executed = 0
        now = datetime.now(BEIJING)
        if not self._stop_event.is_set():
            executed += self._scan_system_tasks(now)
        for user in list_users(self.root):
            if self._stop_event.is_set():
                break
            try:
                executed += self._scan_user_tasks(user, now)
            except Exception as exc:
                if self.on_error:
                    self.on_error(user, exc)
        return executed

    def _scan_system_tasks(self, now: datetime) -> int:
        from run.users import list_users

        store = CronStore(self.root, "__system__", system=True)
        executed = 0
        for task in store.list_tasks():
            if self._stop_event.is_set():
                break
            status = task.get("status", "")
            if status not in {"enabled", "failed"}:
                continue
            if status == "enabled" and not is_due(str(task.get("next_run_at") or ""), now=now):
                continue
            task_id = str(task.get("task_id") or "")
            for user in list_users(self.root):
                if self._stop_event.is_set():
                    break
                started_at = datetime.now(BEIJING)
                started = time.monotonic()
                try:
                    result = execute_cron_task(
                        root=self.root,
                        user=user,
                        task_id=task_id,
                        provider_factory=self.provider_factory,
                        tool_registry_factory=self.tool_registry_factory,
                        cancel_event=self._stop_event,
                        system_task=task,
                    )
                    executed += 1
                    _append_system_execution(
                        self.root,
                        user=user,
                        task_id=task_id,
                        executed_at=started_at,
                        duration_ms=round((time.monotonic() - started) * 1000),
                        result=result,
                    )
                    if self.on_task_executed:
                        self.on_task_executed(user, task_id, result)
                except Exception as exc:
                    _append_system_execution(
                        self.root,
                        user=user,
                        task_id=task_id,
                        executed_at=started_at,
                        duration_ms=round((time.monotonic() - started) * 1000),
                        error=exc,
                    )
                    if self.on_error:
                        self.on_error(user, exc)
            try:
                def advance(current: dict[str, Any]) -> dict[str, Any]:
                    current["status"] = "enabled"
                    current["latest_run_at"] = now.astimezone(BEIJING).isoformat()
                    current["next_run_at"] = compute_next_run(current, after=now)
                    return current
                store.update(task_id, advance)
            except Exception as exc:
                if self.on_error:
                    self.on_error("__system__", exc)
        return executed

    def _scan_user_tasks(self, user: str, now: datetime) -> int:
        store = CronStore(self.root, user)
        tasks = sorted(store.list_tasks(), key=lambda task: (str(task.get("next_run_at", "")), str(task.get("task_id", ""))))
        executed = 0
        for task in tasks:
            if self._stop_event.is_set():
                break
            status = task.get("status", "")
            if status not in {"enabled", "failed"}:
                continue
            if status == "enabled" and not is_due(str(task.get("next_run_at") or ""), now=now):
                continue
            try:
                result = execute_cron_task(
                    root=self.root,
                    user=user,
                    task_id=str(task.get("task_id") or ""),
                    provider_factory=self.provider_factory,
                    tool_registry_factory=self.tool_registry_factory,
                    cancel_event=self._stop_event,
                )
                executed += 1
                if self.on_task_executed:
                    self.on_task_executed(user, task["task_id"], result)
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
    from run.users import list_users

    recovered: list[str] = []
    for user in list_users(root):
        try:
            recovered.extend(CronStore(root, user).recover_interrupted())
        except Exception:
            pass
    try:
        recovered.extend(CronStore(root, "__system__", system=True).recover_interrupted())
    except Exception:
        pass
    return recovered
