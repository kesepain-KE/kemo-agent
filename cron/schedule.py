"""Deterministic schedule time calculation for cron tasks.

All times are stored as UTC ISO strings.  Daily tasks use a user-specified
timezone to determine the next local execution point, then convert back to UTC.
No LLM is involved in time calculation.
"""

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

        # Convert reference time to user's local time
        local_now = after.astimezone(tz)
        # Build today's target time
        target_today = local_now.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        # If today's time has passed, schedule for tomorrow
        if target_today <= local_now:
            target_today = target_today + timedelta(days=1)
        # Convert back to UTC
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
