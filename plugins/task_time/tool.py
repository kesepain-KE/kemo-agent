"""精简 cron 定时任务管理工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cron.schedule import compute_next_run
from run.cron_store import CronConflictError, CronNotFoundError, CronStore, normalize_task
from run.users import validate_user_name


def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


def _summary(task: dict[str, Any]) -> dict[str, Any]:
    return dict(task)


def _schedule_fields(
    kind: str,
    *,
    time_value: str,
    interval_seconds: int,
    next_run_at: str,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = current or {}
    task_type = kind or str(existing.get("type") or "daily")
    fields: dict[str, Any] = {"type": task_type}
    if task_type == "daily":
        fields["time"] = time_value or str(existing.get("time") or "09:00")
    elif task_type == "recurring":
        interval = interval_seconds or int(existing.get("interval_seconds") or 0)
        if interval < 60:
            raise ValueError("用户 recurring 任务需要 interval_seconds >= 60")
        fields["interval_seconds"] = interval
    elif task_type == "once":
        value = next_run_at or str(existing.get("next_run_at") or "")
        if not value:
            raise ValueError("once 任务需要 next_run_at（北京时间 ISO）")
        fields["next_run_at"] = value
    else:
        raise ValueError(f"未知任务类型: {task_type}，可选: daily / once / recurring")
    fields["next_run_at"] = compute_next_run(fields)
    return fields


def run(
    action: str,
    task_id: str = "",
    title: str = "",
    prompt: str = "",
    type: str = "",
    time: str = "",
    interval_seconds: int = 0,
    next_run_at: str = "",
    status: str = "",
    query: str = "",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(context, dict) or not context.get("root") or not context.get("user"):
        raise ValueError("工具上下文缺少 root 或 user")
    root = Path(str(context["root"])).resolve()
    try:
        user = validate_user_name(str(context["user"]))
    except Exception as exc:
        raise ValueError(str(exc)) from exc
    store = CronStore(root, user)

    if action == "list":
        tasks = [_summary(task) for task in store.list_tasks()]
        if not isinstance(query, str):
            raise ValueError("list query 必须是字符串")
        needle = query.strip().casefold()
        if needle:
            tasks = [
                task
                for task in tasks
                if needle in str(task.get("title") or "").casefold()
            ]
        active = sum(1 for task in tasks if task["status"] in {"enabled", "running"})
        return _result(True, tasks=tasks, total=len(tasks), active=active)

    if action == "get":
        if not task_id:
            raise ValueError("get 需要 task_id")
        try:
            return _result(True, task=_summary(store.read(task_id)))
        except CronNotFoundError:
            return _result(False, error=f"任务不存在: {task_id}")

    if action == "create":
        if not title.strip() or not prompt.strip():
            raise ValueError("title 和 prompt 不能为空")
        fields = _schedule_fields(
            type or "daily",
            time_value=time,
            interval_seconds=interval_seconds,
            next_run_at=next_run_at,
        )
        task = normalize_task(
            title=title,
            prompt=prompt,
            user=user,
            type=fields["type"],
            interval_seconds=fields.get("interval_seconds"),
            time=fields.get("time"),
            next_run_at=fields["next_run_at"],
        )
        try:
            return _result(True, task=_summary(store.create(task)))
        except CronConflictError as exc:
            return _result(False, error=str(exc))

    if action == "update":
        if not task_id:
            raise ValueError("update 需要 task_id")

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            if title.strip():
                current["title"] = title.strip()
            if prompt.strip():
                current["prompt"] = prompt.strip()
            if type or time or interval_seconds or next_run_at:
                fields = _schedule_fields(
                    type,
                    time_value=time,
                    interval_seconds=interval_seconds,
                    next_run_at=next_run_at,
                    current=current,
                )
                current.pop("time", None)
                current.pop("interval_seconds", None)
                current.update(fields)
            if status:
                current["status"] = status
                if status == "enabled" and not (type or time or interval_seconds or next_run_at):
                    current["next_run_at"] = compute_next_run(current)
            return current

        try:
            return _result(True, task=_summary(store.update(task_id, mutate)))
        except CronNotFoundError as exc:
            return _result(False, error=str(exc))

    if action == "delete":
        if not task_id:
            raise ValueError("delete 需要 task_id")
        if not store.delete(task_id):
            return _result(False, error=f"任务不存在: {task_id}")
        return _result(True, task_id=task_id, deleted=True)

    raise ValueError(f"未知 action: {action}，可选: create / list / get / update / delete")
