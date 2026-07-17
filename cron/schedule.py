"""cron 任务的确定性计划时间计算。

所有时间均存储为 UTC ISO 字符串。  日常任务使用用户指定的
timezone 来确定下一个本地执行点，然后转换回 UTC。
LLM不涉及时间计算。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from run.cron_store import CronValidationError


def _parse_utc(iso_str: str) -> datetime:
    """Parse a UTC ISO string into an aware datetime.

    Accepts trailing 'Z' or explicit offset.  Naive datetimes are assumed UTC.
    """
    raw = iso_str.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CronValidationError(f"无法解析 UTC 时间：{iso_str!r}（{exc}）") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_utc_iso(dt: datetime) -> str:
    """Convert an aware datetime to UTC ISO string."""
    return dt.astimezone(timezone.utc).isoformat()


def _get_timezone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception as exc:
        raise CronValidationError(f"无效时区：{tz_name!r}（{exc}）") from exc


def compute_next_run(
    schedule: dict[str, Any],
    *,
    after: datetime | None = None,
) -> str:
    """Compute the next run time (UTC ISO) for a schedule.

    Args:
        schedule: The schedule dict from a cron task.
        after: The reference time (UTC aware).  Defaults to now.

    Returns:
        UTC ISO string for the next execution time.
    """
    if after is None:
        after = datetime.now(timezone.utc)
    elif after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    else:
        after = after.astimezone(timezone.utc)

    stype = schedule.get("type")

    if stype == "once":
        start_at = schedule.get("start_at")
        if not isinstance(start_at, str):
            raise CronValidationError("once 任务需要 start_at")
        return _to_utc_iso(_parse_utc(start_at))

    if stype == "daily":
        time_str = schedule.get("time", "00:00")
        tz_name = schedule.get("timezone", "UTC")
        tz = _get_timezone(tz_name)
        hour, minute = (int(x) for x in time_str.split(":"))

        # 将参考时间转换为用户当地时间
        local_now = after.astimezone(tz)
        # 用「午夜 + timedelta」构建目标时间，避免 DST 跳变时 replace() 抛错
        try:
            midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            # 极端情况：当地午夜不存在（某些时区 DST 在 00:00 切换）
            midnight_utc = after.replace(hour=0, minute=0, second=0, microsecond=0)
            midnight = midnight_utc.astimezone(tz)
        target_today = midnight + timedelta(hours=hour, minutes=minute)
        # 如果今天的时间已经过去，安排明天的时间
        if target_today <= local_now:
            target_today = target_today + timedelta(days=1)
        # 转换回 UTC
        return _to_utc_iso(target_today)

    if stype == "recurring":
        interval = int(schedule.get("interval_seconds", 60))
        if interval < 60:
            raise CronValidationError("recurring 间隔必须 >= 60 秒")
        return _to_utc_iso(after + timedelta(seconds=interval))

    raise CronValidationError(f"未知调度类型：{stype!r}")


def is_due(next_run_at: str, *, now: datetime | None = None) -> bool:
    """Check if a task's next_run_at has arrived."""
    if not next_run_at:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    try:
        target = _parse_utc(next_run_at)
    except CronValidationError:
        return False
    return target <= now
