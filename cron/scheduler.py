"""后台 cron 调度器：扫描用户任务并统一交给主智能体执行。"""

from __future__ import annotations

import json
import threading
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
MEMORY_PERIODIC_SYSTEM_KEY = "memory_temporary_important.periodic_scan"
MEMORY_DAILY_SYSTEM_KEY = "memory_temporary_important.daily_consolidate"
MEMORY_PROMOTION_SYSTEM_KEY = "self_improve.memory_promotion"

_PERIODIC_TITLE = "临时重要记忆定时巡检"
_DAILY_TITLE = "临时重要记忆每日整理"
_PROMOTION_TITLE = "记忆碎片到期晋升检查"


def _subagent_prompt(trigger: str) -> str:
    return json.dumps(
        {
            "subagent": "memory_temporary_important",
            "input": {"trigger": trigger},
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _promotion_prompt() -> str:
    return json.dumps(
        {"function": "cron.review_due.scan_and_promote"},
        ensure_ascii=False,
        sort_keys=True,
    )


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
            "key": MEMORY_PERIODIC_SYSTEM_KEY,
            "title": _PERIODIC_TITLE,
            "prompt": _subagent_prompt("periodic_scan"),
            "type": "recurring",
            "interval_seconds": interval_seconds,
            "exec_mode": "subagent",
        },
        {
            "key": MEMORY_DAILY_SYSTEM_KEY,
            "title": _DAILY_TITLE,
            "prompt": _subagent_prompt("daily_consolidate"),
            "type": "daily",
            "time": daily_time,
            "exec_mode": "subagent",
        },
    )


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
    user: str,
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
                title=spec["title"],
                prompt=spec["prompt"],
                user=user,
                type=spec["type"],
                interval_seconds=spec.get("interval_seconds"),
                time=spec.get("time"),
                next_run_at=draft["next_run_at"],
                exec_mode=spec["exec_mode"],
                system_key=spec["key"],
            )
        )

    inactive = current.get("status") not in {"enabled", "failed"}
    schedule_changed = not _same_schedule(current, spec)
    changed = (
        inactive
        or schedule_changed
        or current.get("title") != spec["title"]
        or current.get("prompt") != spec["prompt"]
        or current.get("user") != user
        or current.get("exec_mode") != spec["exec_mode"]
        or current.get("system_key") != spec["key"]
    )
    if not changed:
        return current

    def mutate(task: dict[str, Any]) -> dict[str, Any]:
        task.update({
            "title": spec["title"],
            "prompt": spec["prompt"],
            "user": user,
            "type": spec["type"],
            "exec_mode": spec["exec_mode"],
            "system_key": spec["key"],
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
    user: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """创建或校准两个临时重要记忆维护任务。"""
    store = CronStore(root, user)
    tasks = store.list_tasks()
    by_key = {
        task.get("system_key"): task
        for task in tasks
        if task.get("system_key")
    }
    by_title = {task.get("title"): task for task in tasks}
    now = datetime.now(BEIJING)
    return [
        _reconcile_system_task(
            store,
            by_key.get(spec["key"]) or by_title.get(spec["title"]),
            user=user,
            spec=spec,
            now=now,
        )
        for spec in _memory_task_specs(config)
    ]


def ensure_memory_promotion_task(
    root: Path,
    user: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """创建或校准 30 秒一次的记忆晋升扫描任务。"""
    del config
    store = CronStore(root, user)
    tasks = store.list_tasks()
    current = next(
        (
            task
            for task in tasks
            if task.get("system_key") == MEMORY_PROMOTION_SYSTEM_KEY
        ),
        None,
    ) or next(
        (task for task in tasks if task.get("title") == _PROMOTION_TITLE),
        None,
    )
    spec = {
        "key": MEMORY_PROMOTION_SYSTEM_KEY,
        "title": _PROMOTION_TITLE,
        "prompt": _promotion_prompt(),
        "type": "recurring",
        "interval_seconds": 30,
        "exec_mode": "function",
    }
    return _reconcile_system_task(
        store,
        current,
        user=user,
        spec=spec,
        now=datetime.now(BEIJING),
    )


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
        for user in list_users(self.root):
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
        tasks = sorted(
            store.list_tasks(),
            key=lambda task: (
                0
                if task.get("system_key") == MEMORY_PERIODIC_SYSTEM_KEY
                or task.get("title") == _PERIODIC_TITLE
                else 1,
                str(task.get("next_run_at", "")),
                str(task.get("task_id", "")),
            ),
        )
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
    return recovered
