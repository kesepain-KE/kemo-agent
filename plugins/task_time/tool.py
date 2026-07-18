"""定时任务管理工具 — 创建/列出/修改/删除 cron 任务。kemo-agent 原生插件。"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run.cron_store import CronStore, normalize_task, CronConflictError, CronNotFoundError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


def _summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "prompt": task["prompt"],
        "type": task["schedule"]["type"],
        "status": task["status"],
        "next_run_at": task.get("next_run_at", ""),
        "last_run_at": task.get("last_run_at", ""),
        "run_count": task.get("run_count", 0),
        "revision": task.get("revision", 1),
        "created_at": task.get("created_at", ""),
        "updated_at": task.get("updated_at", ""),
    }


def run(
    action: str,
    task_id: str = "",
    title: str = "",
    prompt: str = "",
    type: str = "daily",
    time: str = "09:00",
    interval_seconds: int = 0,
    start_at: str = "",
    timezone: str = "UTC",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    root = Path(context["root"])
    user = str(context["user"])
    store = CronStore(root, user)

    if action == "list":
        tasks = [{"task_id": t["task_id"], "title": t["title"], "type": t["schedule"]["type"],
                   "status": t["status"], "next_run_at": t.get("next_run_at", ""),
                   "run_count": t.get("run_count", 0)} for t in store.list_tasks()]
        active = sum(1 for t in tasks if t["status"] in {"enabled", "running"})
        return _result(True, tasks=tasks, total=len(tasks), active=active)

    if action == "create":
        if not title.strip() or not prompt.strip():
            raise ValueError("title 和 prompt 不能为空")
        if type == "daily":
            schedule: dict[str, Any] = {"type": "daily", "time": time, "timezone": timezone}
        elif type == "once":
            schedule = {"type": "once", "start_at": start_at or _now()}
        elif type == "recurring":
            schedule = {"type": "recurring", "interval_seconds": max(60, interval_seconds or 3600)}
        else:
            raise ValueError(f"未知任务类型: {type}，可选: daily / once / recurring")

        task = normalize_task(
            title=title.strip(),
            prompt=prompt.strip(),
            user=user,
            schedule=schedule,
            source="web",
            session_id="cron",
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
            if type:
                if type == "daily":
                    current["schedule"] = {"type": "daily", "time": time, "timezone": timezone}
                elif type == "once":
                    current["schedule"] = {"type": "once", "start_at": start_at or _now()}
                elif type == "recurring":
                    current["schedule"] = {"type": "recurring", "interval_seconds": max(60, interval_seconds or 3600)}
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
