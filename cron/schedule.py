"""cron 任务的确定性北京时间计划计算。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from run.cron_store import CronValidationError


BEIJING = ZoneInfo("Asia/Shanghai")


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
    # 兼容旧 once 数据；无时区时间按 UTC 迁移后再转北京。
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING)


def compute_next_run(
    task: dict[str, Any],
    *,
    after: datetime | None = None,
) -> str:
    """根据扁平任务字段计算下一次北京时间。"""
    reference = _as_beijing(after)
    task_type = task.get("type")

    if task_type == "once":
        next_run_at = task.get("next_run_at")
        if not isinstance(next_run_at, str) or not next_run_at.strip():
            raise CronValidationError("once 任务需要 next_run_at")
        return _parse_beijing(next_run_at).isoformat()

    if task_type == "daily":
        time_str = task.get("time")
        if not isinstance(time_str, str):
            raise CronValidationError("daily 任务需要 time")
        try:
            hour, minute = (int(part) for part in time_str.split(":"))
        except (TypeError, ValueError) as exc:
            raise CronValidationError(f"daily 任务 time 无效：{time_str!r}") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise CronValidationError(f"daily 任务 time 超出范围：{time_str!r}")
        target = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= reference:
            target += timedelta(days=1)
        return target.isoformat()

    if task_type == "recurring":
        interval = task.get("interval_seconds")
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
            raise CronValidationError("recurring 间隔必须 >= 1 秒")
        return (reference + timedelta(seconds=interval)).isoformat()

    raise CronValidationError(f"未知任务类型：{task_type!r}")


def is_due(next_run_at: str, *, now: datetime | None = None) -> bool:
    """判断 next_run_at 是否已经到达。"""
    if not next_run_at:
        return False
    try:
        target = _parse_beijing(next_run_at)
    except CronValidationError:
        return False
    return target <= _as_beijing(now)
