"""定时任务管理工具 — 创建/列出/修改/删除 cron 任务。kemo-agent 原生插件。"""

from pathlib import Path
from typing import Any

from cron.schedule import compute_next_run
from run.cron_store import CronStore, normalize_task, CronConflictError, CronNotFoundError

def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


def _summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "prompt": task["prompt"],
        "type": task["schedule"]["type"],
        "schedule": dict(task["schedule"]),
        "status": task["status"],
        "next_run_at": task.get("next_run_at", ""),
        "last_run_at": task.get("last_run_at", ""),
        "run_count": task.get("run_count", 0),
        "revision": task.get("revision", 1),
        "created_at": task.get("created_at", ""),
        "updated_at": task.get("updated_at", ""),
    }


def _schedule(
    kind: str,
    *,
    time_value: str,
    interval_seconds: int,
    start_at: str,
    timezone_name: str,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = dict(current or {})
    schedule_type = kind or str(existing.get("type") or "daily")
    if schedule_type == "daily":
        return {
            "type": "daily",
            "time": time_value or str(existing.get("time") or "09:00"),
            "timezone": timezone_name or str(existing.get("timezone") or "UTC"),
        }
    if schedule_type == "once":
        value = start_at or str(existing.get("start_at") or "")
        if not value:
            raise ValueError("once 任务需要 start_at")
        return {"type": "once", "start_at": value}
    if schedule_type == "recurring":
        interval = interval_seconds or int(existing.get("interval_seconds") or 0)
        if interval < 60:
            raise ValueError("recurring 任务需要 interval_seconds >= 60")
        return {"type": "recurring", "interval_seconds": interval}
    raise ValueError(f"未知任务类型: {schedule_type}，可选: daily / once / recurring")


def run(
    action: str,
    task_id: str = "",
    title: str = "",
    prompt: str = "",
    type: str = "",
    time: str = "",
    interval_seconds: int = 0,
    start_at: str = "",
    timezone: str = "",
    status: str = "",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    root = Path(context["root"])
    user = str(context["user"])
    store = CronStore(root, user)

    if action == "list":
        tasks = [_summary(task) for task in store.list_tasks()]
        active = sum(1 for t in tasks if t["status"] in {"enabled", "running"})
        return _result(True, tasks=tasks, total=len(tasks), active=active)

    if action == "create":
        if not title.strip() or not prompt.strip():
            raise ValueError("title 和 prompt 不能为空")
        schedule = _schedule(
            type or "daily",
            time_value=time,
            interval_seconds=interval_seconds,
            start_at=start_at,
            timezone_name=timezone,
        )
        task = normalize_task(
            title=title.strip(),
            prompt=prompt.strip(),
            user=user,
            schedule=schedule,
            source=str(context.get("source") or "tool"),
            session_id=str(context.get("session_id") or "cron"),
            next_run_at=compute_next_run(schedule),
        )
        try:
            created = store.create(task)
        except CronConflictError as exc:
            return _result(False, error=str(exc))
        return _result(True, task=_summary(created))

    if action == "update":
        if not task_id:
            raise ValueError("update 需要 task_id")
        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            if title.strip():
                current["title"] = title.strip()
            if prompt.strip():
                current["prompt"] = prompt.strip()
            if type or time or interval_seconds or start_at or timezone:
                schedule = _schedule(
                    type,
                    time_value=time,
                    interval_seconds=interval_seconds,
                    start_at=start_at,
                    timezone_name=timezone,
                    current=current.get("schedule") or {},
                )
                current["schedule"] = schedule
                current["next_run_at"] = compute_next_run(schedule)
            if status:
                current["status"] = status
            return current
        try:
            updated = store.update(task_id, mutate)
        except CronNotFoundError as exc:
            return _result(False, error=str(exc))
        return _result(True, task=_summary(updated))

    if action == "delete":
        if not task_id:
            raise ValueError("delete 需要 task_id")
        deleted = store.delete(task_id)
        if not deleted:
            return _result(False, error=f"任务不存在: {task_id}")
        return _result(True, task_id=task_id, deleted=True)

    raise ValueError(f"未知 action: {action}，可选: create / list / update / delete")
