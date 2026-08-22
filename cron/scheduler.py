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
from provider.factory import create_provider, provider_semaphore_status
from run.config import system_update_rate
from run.scheduler import CronLogAggregator
from run.scheduler import (
    SystemCronLease,
    mark_cron_runtime_checkpoint,
    pending_cron_runtime,
    runtime_checkpoint_due,
    update_cron_runtime,
)
from run.scheduler import CronStore, CronValidationError, normalize_task
from run.infra import LogStore
from run.tools import ToolRegistry, discover_tools


BEIJING = ZoneInfo("Asia/Shanghai")
MEMORY_PERIODIC_TASK_ID = "memory_periodic_scan"
MEMORY_DAILY_TASK_ID = "memory_daily_consolidate"
MEMORY_PROMOTION_TASK_ID = "memory_promotion"
PERCEPTION_TASK_ID = "perception_update"
EXPAND_TASK_ID = "expand_update"
# 仅保留旧导入名，持久化 schema 已不再包含 system_key。
MEMORY_PERIODIC_SYSTEM_KEY = MEMORY_PERIODIC_TASK_ID
MEMORY_DAILY_SYSTEM_KEY = MEMORY_DAILY_TASK_ID
MEMORY_PROMOTION_SYSTEM_KEY = MEMORY_PROMOTION_TASK_ID

_PERIODIC_TITLE = "临时重要记忆定时巡检"
_DAILY_TITLE = "临时重要记忆每日整理"
_PROMOTION_TITLE = "记忆碎片到期晋升检查"
_PERCEPTION_TITLE = "全局感知模块数据采集"
_EXPAND_TITLE = "拓展模块数据采集"
_SINGLETON_SYSTEM_ACTIONS = frozenset({"perception_update"})
_NO_BACKOFF_SYSTEM_ACTIONS = frozenset({"perception_update", "expand_update"})
_AGGREGATED_SYSTEM_ACTIONS = frozenset(
    {"perception_update", "expand_update", "memory_promotion"}
)
_SUCCESS_SYSTEM_STATUSES = frozenset(
    {"success", "completed", "enabled", "ok", "active", "inactive", "skipped"}
)
_DEFAULT_PERSISTENCE_INTERVAL_SECONDS = 300.0


def _system_result_summary(result: Any) -> dict[str, Any]:
    """Keep system-cron diagnostics useful without persisting prompt bodies."""

    if not isinstance(result, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "status", "action", "category", "scope", "user", "model", "requested", "reason"
    ):
        value = result.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
    for key in (
        "created", "updated", "failed", "forgotten", "rejected", "deleted", "applied"
    ):
        value = result.get(key)
        if isinstance(value, list):
            summary[key] = [str(item) for item in value[:100]]
    errors = result.get("errors")
    if isinstance(errors, list):
        summary["errors"] = [
            {
                name: str(item.get(name))
                for name in ("module", "reason", "exception_type")
                if item.get(name) is not None
            }
            for item in errors[:100]
            if isinstance(item, dict)
        ]
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
    memory_update = result.get("memory_update")
    if isinstance(memory_update, dict):
        featured = memory_update.get("featured")
        reconciled = memory_update.get("reconciled")
        summary["memory_update"] = {
            "featured": (
                [str(item) for item in featured[:100]]
                if isinstance(featured, list)
                else []
            ),
            "reconciled": (
                [
                    {
                        key: str(item.get(key))
                        for key in ("action", "filename", "permanent_filename")
                        if item.get(key) is not None
                    }
                    for item in reconciled[:100]
                    if isinstance(item, dict)
                ]
                if isinstance(reconciled, list)
                else []
            ),
        }
    return summary


def _system_execution_record(
    *,
    user: str,
    task_id: str,
    executed_at: datetime,
    duration_ms: int,
    result: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Build one bounded, user-scoped system-cron execution record."""

    status = "failed" if error is not None else str((result or {}).get("status") or "completed")
    if error is None and status in _SUCCESS_SYSTEM_STATUSES:
        status = "success"
    return {
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
    """Append one bounded execution immediately.

    The scheduler uses an in-memory aggregator for high-frequency successes;
    this direct helper remains the durable path for errors, low-frequency
    tasks and callers outside a running scheduler.
    """

    try:
        LogStore(root).append_cron(
            _system_execution_record(
                user=user,
                task_id=task_id,
                executed_at=executed_at,
                duration_ms=duration_ms,
                result=result,
                error=error,
            )
        )
    except Exception:
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


def _perception_spec(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": PERCEPTION_TASK_ID,
        "title": _PERCEPTION_TITLE,
        "type": "recurring",
        "interval_seconds": system_update_rate(config, "sense_update_rate"),
        "exec_mode": "system",
        "action": "perception_update",
    }


def _expand_spec(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": EXPAND_TASK_ID,
        "title": _EXPAND_TITLE,
        "type": "recurring",
        "interval_seconds": system_update_rate(config, "expand_update_rate"),
        "exec_mode": "system",
        "action": "expand_update",
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


def ensure_perception_task(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Create or recalibrate the global perception data update task."""

    store = CronStore(root, "__system__", system=True)
    tasks = store.list_tasks()
    spec = _perception_spec(config)
    return _reconcile_system_task(
        store,
        next((task for task in tasks if task.get("task_id") == spec["task_id"]), None),
        spec=spec,
        now=datetime.now(BEIJING),
    )


def ensure_expand_task(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Create or recalibrate the global expand data update task."""

    store = CronStore(root, "__system__", system=True)
    tasks = store.list_tasks()
    spec = _expand_spec(config)
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
        config: dict[str, Any] | None = None,
        provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
        tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
        on_task_executed: Callable[[str, str, dict[str, Any]], None] | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
        transport_registry: Any | None = None,
    ) -> None:
        self.root = root.resolve()
        self.poll_interval = poll_interval
        self._config = config or {}
        self.provider_factory = provider_factory
        self.tool_registry_factory = tool_registry_factory
        self.on_task_executed = on_task_executed
        self.on_error = on_error
        self._transport_registry = transport_registry
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        task_config = self._config.get("task_cron_system") or {}
        if not isinstance(task_config, dict):
            task_config = {}
        self._runtime_checkpoint_seconds = self._positive_interval(
            task_config.get("runtime_checkpoint_seconds"),
            _DEFAULT_PERSISTENCE_INTERVAL_SECONDS,
        )
        self._success_log_flush_seconds = self._positive_interval(
            task_config.get("success_log_flush_seconds"),
            _DEFAULT_PERSISTENCE_INTERVAL_SECONDS,
        )
        self._log_aggregator = CronLogAggregator(
            self.root,
            flush_seconds=self._success_log_flush_seconds,
        )
        self._system_lease = SystemCronLease(self.root)

    @staticmethod
    def _positive_interval(value: Any, default: float) -> float:
        if isinstance(value, bool):
            return default
        try:
            rendered = float(value)
        except (TypeError, ValueError):
            return default
        return min(3600.0, max(1.0, rendered))

    def _record_system_execution(
        self,
        *,
        action: str,
        user: str,
        task_id: str,
        executed_at: datetime,
        duration_ms: int,
        result: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        record = _system_execution_record(
            user=user,
            task_id=task_id,
            executed_at=executed_at,
            duration_ms=duration_ms,
            result=result,
            error=error,
        )
        try:
            if action in _AGGREGATED_SYSTEM_ACTIONS and record["status"] == "success":
                self._log_aggregator.record_success(record)
            else:
                self._log_aggregator.record_immediate(record)
        except Exception:
            # Logging remains diagnostic-only and must never stop collection.
            return

    def _persist_runtime_state(self, state: dict[str, Any]) -> None:
        store = CronStore(
            self.root,
            str(state.get("user") or "__system__"),
            system=bool(state.get("system")),
        )

        def checkpoint(current: dict[str, Any]) -> dict[str, Any]:
            current["latest_run_at"] = str(state.get("latest_run_at") or "")
            current["next_run_at"] = str(state.get("next_run_at") or "")
            current["status"] = str(state.get("status") or "enabled")
            return current

        store.update(
            str(state.get("task_id") or ""),
            checkpoint,
            clear_runtime=False,
        )
        mark_cron_runtime_checkpoint(
            self.root,
            str(state.get("user") or "__system__"),
            bool(state.get("system")),
            str(state.get("task_id") or ""),
            expected_updated_monotonic=float(state.get("updated_monotonic") or 0.0),
            expected_latest_run_at=str(state.get("latest_run_at") or ""),
            expected_next_run_at=str(state.get("next_run_at") or ""),
            expected_status=str(state.get("status") or "enabled"),
        )

    def flush_persistence(self) -> None:
        """Persist aggregated successes and volatile system schedule state."""

        try:
            self._log_aggregator.flush()
        except Exception:
            pass
        for state in pending_cron_runtime(self.root):
            try:
                self._persist_runtime_state(state)
            except Exception as exc:
                if self.on_error:
                    self.on_error(str(state.get("user") or "__system__"), exc)

    def _flush_due_success_logs(self) -> None:
        try:
            self._log_aggregator.flush_due()
        except Exception:
            # A failed aggregate remains in memory and will be retried on the
            # next scheduler pass or during normal shutdown.
            pass

    def _flush_runtime_states(self) -> None:
        for state in pending_cron_runtime(self.root):
            try:
                self._persist_runtime_state(state)
            except Exception as exc:
                if self.on_error:
                    self.on_error(str(state.get("user") or "__system__"), exc)

    def _should_backoff(self) -> bool:
        cron_config = self._config.get("cron") or {}
        if not isinstance(cron_config, dict):
            cron_config = {}
        if not bool(cron_config.get("avoid_congestion", True)):
            return False
        raw_ratio = cron_config.get("congestion_threshold_ratio", 0.2)
        if isinstance(raw_ratio, bool):
            raw_ratio = 0.2
        try:
            ratio = float(raw_ratio)
        except (TypeError, ValueError):
            ratio = 0.2
        ratio = min(1.0, max(0.0, ratio))
        status = provider_semaphore_status()
        maximum = int(status.get("max_requests") or 0)
        if maximum < 1:
            return False
        available = int(status.get("available_requests") or 0)
        threshold = max(1, int(maximum * ratio))
        return available < threshold

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
        if thread is not None and thread.is_alive():
            # The worker may still be inside a long-running task.  It must keep
            # leadership until that task returns; releasing now would let a
            # second process execute the same system task concurrently.
            return
        with self._lock:
            self._thread = None
        self.flush_persistence()
        self._system_lease.release()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def scan_once(self) -> int:
        """Run one foreground scan with temporary system-task leadership.

        A foreground caller cannot retain an OS lease between calls, so its
        volatile schedule advances are checkpointed before releasing it.
        """

        acquired_here = False
        if not self._system_lease.owned:
            acquired_here = self._system_lease.try_acquire()
        try:
            return self._scan_once(include_system=self._system_lease.owned)
        finally:
            self._flush_due_success_logs()
            if acquired_here and not self.running:
                self._flush_runtime_states()
                self._system_lease.release()

    def _scan_once(self, *, include_system: bool) -> int:
        from run.config import list_users

        executed = 0
        now = datetime.now(BEIJING)
        if include_system and not self._stop_event.is_set():
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
        from run.config import list_users

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
            action = str(task.get("action") or task_id)
            if action not in _NO_BACKOFF_SYSTEM_ACTIONS and self._should_backoff():
                continue
            users = tuple(list_users(self.root))
            if action == "expand_update":
                execution_users = ("__system__", *users)
            elif action in _SINGLETON_SYSTEM_ACTIONS:
                execution_users = ("__system__",)
            else:
                execution_users = users
            task_had_error = False
            for user in execution_users:
                if self._stop_event.is_set():
                    break
                started_at = datetime.now(BEIJING)
                started = time.monotonic()
                try:
                    execute_kwargs: dict[str, Any] = {
                        "root": self.root,
                        "user": user,
                        "task_id": task_id,
                        "provider_factory": self.provider_factory,
                        "tool_registry_factory": self.tool_registry_factory,
                        "cancel_event": self._stop_event,
                        "system_task": task,
                    }
                    if user == "__system__":
                        execute_kwargs["config"] = self._config
                    result = execute_cron_task(
                        **execute_kwargs,
                    )
                    executed += 1
                    self._record_system_execution(
                        action=action,
                        user=user,
                        task_id=task_id,
                        executed_at=started_at,
                        duration_ms=round((time.monotonic() - started) * 1000),
                        result=result,
                    )
                    if self.on_task_executed:
                        self.on_task_executed(user, task_id, result)
                except Exception as exc:
                    task_had_error = True
                    self._record_system_execution(
                        action=action,
                        user=user,
                        task_id=task_id,
                        executed_at=started_at,
                        duration_ms=round((time.monotonic() - started) * 1000),
                        error=exc,
                    )
                    if self.on_error:
                        self.on_error(user, exc)
            try:
                latest_run_at = now.astimezone(BEIJING).isoformat()
                state = update_cron_runtime(
                    self.root,
                    "__system__",
                    True,
                    task_id,
                    latest_run_at=latest_run_at,
                    next_run_at=compute_next_run(task, after=now),
                    status="enabled",
                )
                if task_had_error or runtime_checkpoint_due(
                    state, self._runtime_checkpoint_seconds
                ):
                    self._persist_runtime_state(state)
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
            if self._should_backoff():
                break
            task_id = str(task.get("task_id") or "")
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
                    transport_registry=self._transport_registry,
                )
                executed += 1
                try:
                    result_status = str(result.get("status") or "completed")
                    LogStore(self.root).append_cron(
                        {
                            "schema_version": 1,
                            "executed_at": started_at.isoformat(),
                            "user": user,
                            "task_id": task_id,
                            "status": (
                                "success"
                                if result_status in {"enabled", "completed"}
                                else result_status
                            ),
                            "duration_ms": round(
                                (time.monotonic() - started) * 1000
                            ),
                            "result": _system_result_summary(result),
                            "error": None,
                        }
                    )
                except Exception:
                    pass
                if self.on_task_executed:
                    self.on_task_executed(user, task["task_id"], result)
            except Exception as exc:
                try:
                    LogStore(self.root).append_cron(
                        {
                            "schema_version": 1,
                            "executed_at": started_at.isoformat(),
                            "user": user,
                            "task_id": task_id,
                            "status": "failed",
                            "duration_ms": round(
                                (time.monotonic() - started) * 1000
                            ),
                            "result": {},
                            "error": {
                                "type": type(exc).__name__,
                                "message": str(exc),
                            },
                        }
                    )
                except Exception:
                    pass
                if self.on_error:
                    self.on_error(user, exc)
        return executed

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    self._system_lease.try_acquire()
                    self._scan_once(include_system=self._system_lease.owned)
                except Exception:
                    pass
                self._flush_due_success_logs()
                self._stop_event.wait(self.poll_interval)
        finally:
            self.flush_persistence()
            self._system_lease.release()
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None


def recover_all(root: Path) -> list[str]:
    from run.config import list_users

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
