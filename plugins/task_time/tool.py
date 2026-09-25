"""精简 cron 定时任务管理工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cron.schedule import compute_next_run
from run.config import validate_user_name
from run.infra import LogStore
from run.scheduler import CronConflictError, CronNotFoundError, CronStore, normalize_task


SCHEDULE_TYPES = frozenset({"daily", "weekly", "monthly", "once", "recurring"})


def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


def _recent_runs(root: Path, user: str, task_id: str, limit: int) -> list[dict[str, Any]]:
    safe_limit = max(1, min(50, int(limit)))
    return LogStore(root).list_cron_for_task(user, task_id, limit=safe_limit)


def _summary(task: dict[str, Any], *, root: Path | None = None, history_limit: int = 0) -> dict[str, Any]:
    result = dict(task)
    result.pop("_execution_error", None)
    if root is not None and history_limit > 0:
        result["recent_runs"] = _recent_runs(root, str(task.get("user") or ""), str(task.get("task_id") or ""), history_limit)
    return result


def _string_list(value: list[str] | None, existing: Any) -> list[str] | None:
    if value:
        return list(value)
    return list(existing) if isinstance(existing, list) else None


def _int_list(value: list[int] | None, existing: Any) -> list[int] | None:
    if value:
        return list(value)
    return list(existing) if isinstance(existing, list) else None


def _schedule_fields(
    kind: str,
    *,
    time_value: str,
    times: list[str] | None,
    weekdays: list[int] | None,
    month_days: list[int] | None,
    interval_seconds: int,
    next_run_at: str,
    start_date: str | None,
    end_date: str | None,
    max_runs: int | None,
    clear_max_runs: bool,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = current or {}
    task_type = kind or str(existing.get("type") or "daily")
    if task_type not in SCHEDULE_TYPES:
        raise ValueError(f"未知任务类型: {task_type}，可选: daily / weekly / monthly / once / recurring")
    fields: dict[str, Any] = {"type": task_type}
    if task_type in {"daily", "weekly", "monthly"}:
        # An explicitly supplied single ``time`` must be able to replace an
        # existing multi-time schedule.  Only inherit ``times`` when neither
        # representation was supplied by the caller.
        selected_times = list(times) if times else (
            None if time_value else _string_list(None, existing.get("times"))
        )
        if selected_times:
            fields["times"] = sorted(set(selected_times))
        else:
            fields["time"] = time_value or str(existing.get("time") or "09:00")
        if task_type == "weekly":
            fields["weekdays"] = sorted(set(_int_list(weekdays, existing.get("weekdays")) or [1]))
        elif task_type == "monthly":
            fields["month_days"] = sorted(set(_int_list(month_days, existing.get("month_days")) or [1]))
    elif task_type == "recurring":
        interval = interval_seconds or int(existing.get("interval_seconds") or 0)
        if interval < 60:
            raise ValueError("用户 recurring 任务需要 interval_seconds >= 60")
        fields["interval_seconds"] = interval
    else:
        value = next_run_at or str(existing.get("next_run_at") or "")
        if not value:
            raise ValueError("once 任务需要 next_run_at（北京时间 ISO）")
        fields["next_run_at"] = value

    selected_start = str(existing.get("start_date") or "") if start_date is None else start_date
    selected_end = str(existing.get("end_date") or "") if end_date is None else end_date
    selected_max = 0 if clear_max_runs else int(existing.get("max_runs") or 0) if max_runs is None else max_runs
    if selected_start:
        fields["start_date"] = selected_start
    if selected_end:
        fields["end_date"] = selected_end
    if selected_max:
        fields["max_runs"] = selected_max
    if task_type != "once":
        fields["next_run_at"] = compute_next_run(fields)
        if not fields["next_run_at"]:
            raise ValueError("调度范围内已经没有可执行时间")
    else:
        checked = compute_next_run(fields)
        if not checked:
            raise ValueError("once 任务时间不在生效区间内")
        fields["next_run_at"] = checked
    return fields


def run(
    action: str,
    task_id: str = "",
    title: str = "",
    prompt: str = "",
    type: str = "",
    time: str = "",
    times: list[str] | None = None,
    weekdays: list[int] | None = None,
    month_days: list[int] | None = None,
    interval_seconds: int = 0,
    next_run_at: str = "",
    start_date: str | None = None,
    end_date: str | None = None,
    max_runs: int | None = None,
    clear_max_runs: bool = False,
    status: str = "",
    query: str = "",
    history_limit: int = 0,
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
            tasks = [task for task in tasks if needle in str(task.get("title") or "").casefold()]
        active = sum(1 for task in tasks if task["status"] in {"enabled", "running"})
        return _result(True, tasks=tasks, total=len(tasks), active=active)

    if action in {"get", "history"}:
        if not task_id:
            raise ValueError(f"{action} 需要 task_id")
        try:
            task = store.read(task_id)
        except CronNotFoundError:
            return _result(False, error=f"任务不存在: {task_id}")
        if action == "history":
            return _result(
                True,
                task_id=task_id,
                runs=_recent_runs(root, user, task_id, history_limit or 10),
            )
        return _result(True, task=_summary(task, root=root, history_limit=history_limit))

    if action == "create":
        if not title.strip() or not prompt.strip():
            raise ValueError("title 和 prompt 不能为空")
        fields = _schedule_fields(
            type or "daily", time_value=time, times=times, weekdays=weekdays,
            month_days=month_days, interval_seconds=interval_seconds,
            next_run_at=next_run_at, start_date=start_date, end_date=end_date,
            max_runs=max_runs, clear_max_runs=clear_max_runs,
        )
        task = normalize_task(
            title=title, prompt=prompt, user=user, type=fields["type"],
            interval_seconds=fields.get("interval_seconds"), time=fields.get("time"),
            times=fields.get("times"), weekdays=fields.get("weekdays"),
            month_days=fields.get("month_days"), start_date=fields.get("start_date", ""),
            end_date=fields.get("end_date", ""), max_runs=fields.get("max_runs"),
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
            schedule_changed = bool(
                type or time or times or weekdays or month_days or interval_seconds
                or next_run_at or start_date is not None or end_date is not None
                or max_runs is not None or clear_max_runs
            )
            if schedule_changed:
                fields = _schedule_fields(
                    type, time_value=time, times=times, weekdays=weekdays,
                    month_days=month_days, interval_seconds=interval_seconds,
                    next_run_at=next_run_at, start_date=start_date, end_date=end_date,
                    max_runs=max_runs, clear_max_runs=clear_max_runs, current=current,
                )
                for field in ("time", "times", "weekdays", "month_days", "interval_seconds", "start_date", "end_date", "max_runs"):
                    current.pop(field, None)
                current.update(fields)
            if status:
                current["status"] = status
                if status == "enabled" and not schedule_changed:
                    next_value = compute_next_run(current)
                    if not next_value:
                        raise ValueError("调度范围内已经没有可执行时间")
                    current["next_run_at"] = next_value
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

    raise ValueError("未知 action，可选: create / list / get / history / update / delete")
