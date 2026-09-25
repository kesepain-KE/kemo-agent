"""cron 任务的确定性北京时间计划计算。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from run.scheduler import CronValidationError


BEIJING = ZoneInfo("Asia/Shanghai")
_MAX_CALENDAR_SEARCH_DAYS = 366 * 20


def _as_beijing(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(BEIJING)
    if value.tzinfo is None:
        value = value.replace(tzinfo=BEIJING)
    return value.astimezone(BEIJING)


def _parse_beijing(iso_str: str) -> datetime:
    raw = iso_str.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CronValidationError(f"无法解析时间：{iso_str!r}（{exc}）") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING)


def _parse_date(value: Any, *, field: str) -> date | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise CronValidationError(f"{field} 必须是 YYYY-MM-DD 字符串")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CronValidationError(f"{field} 不是有效日期：{value!r}") from exc


def _parse_clock(value: str) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except (TypeError, ValueError) as exc:
        raise CronValidationError(f"定时任务时间无效：{value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CronValidationError(f"定时任务时间超出范围：{value!r}")
    return time(hour=hour, minute=minute)


def _task_times(task: dict[str, Any]) -> tuple[time, ...]:
    raw_times = task.get("times")
    if raw_times is None:
        raw = task.get("time")
        if not isinstance(raw, str):
            raise CronValidationError(f"{task.get('type')} 任务需要 time 或 times")
        values = [raw]
    else:
        if not isinstance(raw_times, list) or not raw_times:
            raise CronValidationError("times 必须是非空时间数组")
        values = raw_times
    parsed = []
    for value in values:
        if not isinstance(value, str):
            raise CronValidationError("times 中的时间必须是 HH:MM 字符串")
        parsed.append(_parse_clock(value))
    return tuple(sorted(set(parsed)))


def _within_date_range(candidate: datetime, start: date | None, end: date | None) -> bool:
    local_date = candidate.astimezone(BEIJING).date()
    return not ((start and local_date < start) or (end and local_date > end))


def _calendar_day_matches(task: dict[str, Any], value: date) -> bool:
    task_type = task.get("type")
    if task_type == "daily":
        return True
    if task_type == "weekly":
        weekdays = task.get("weekdays")
        return isinstance(weekdays, list) and value.isoweekday() in weekdays
    if task_type == "monthly":
        month_days = task.get("month_days")
        return isinstance(month_days, list) and value.day in month_days
    return False


def compute_next_occurrence(
    task: dict[str, Any],
    *,
    after: datetime | None = None,
) -> datetime | None:
    """计算严格晚于 ``after`` 的下一次运行；计划耗尽时返回 ``None``。"""

    reference = _as_beijing(after)
    task_type = task.get("type")
    start_date = _parse_date(task.get("start_date"), field="start_date")
    end_date = _parse_date(task.get("end_date"), field="end_date")
    if start_date and end_date and start_date > end_date:
        raise CronValidationError("start_date 不能晚于 end_date")

    if task_type == "once":
        next_run_at = task.get("next_run_at")
        if not isinstance(next_run_at, str) or not next_run_at.strip():
            raise CronValidationError("once 任务需要 next_run_at")
        candidate = _parse_beijing(next_run_at)
        return candidate if _within_date_range(candidate, start_date, end_date) else None

    if task_type == "recurring":
        interval = task.get("interval_seconds")
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
            raise CronValidationError("recurring 间隔必须 >= 1 秒")
        candidate = reference + timedelta(seconds=interval)
        if start_date and candidate.date() < start_date:
            candidate = datetime.combine(start_date, time.min, tzinfo=BEIJING)
        if end_date and candidate.date() > end_date:
            return None
        return candidate

    if task_type not in {"daily", "weekly", "monthly"}:
        raise CronValidationError(f"未知任务类型：{task_type!r}")

    times = _task_times(task)
    first_day = max(reference.date(), start_date) if start_date else reference.date()
    for offset in range(_MAX_CALENDAR_SEARCH_DAYS):
        current_day = first_day + timedelta(days=offset)
        if end_date and current_day > end_date:
            return None
        if not _calendar_day_matches(task, current_day):
            continue
        for clock in times:
            candidate = datetime.combine(current_day, clock, tzinfo=BEIJING)
            if candidate > reference:
                return candidate
    raise CronValidationError("无法在 20 年搜索窗口内计算下一次运行时间")


def compute_next_run(
    task: dict[str, Any],
    *,
    after: datetime | None = None,
) -> str:
    """根据扁平任务字段计算下一次北京时间；计划耗尽时返回空串。"""

    candidate = compute_next_occurrence(task, after=after)
    return candidate.isoformat() if candidate is not None else ""


def is_due(next_run_at: str, *, now: datetime | None = None) -> bool:
    """判断 next_run_at 是否已经到达。"""
    if not next_run_at:
        return False
    try:
        target = _parse_beijing(next_run_at)
    except CronValidationError:
        return False
    return target <= _as_beijing(now)
