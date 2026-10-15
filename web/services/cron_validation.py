"""Web 定时任务写入边界；存储层的旧数据迁移规则保持独立。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from web.errors import InvalidRequestError


_WRITABLE_FIELDS = frozenset({
    "title", "prompt", "type", "interval_seconds", "time", "times",
    "weekdays", "month_days", "start_date", "end_date", "max_runs",
    "next_run_at", "status",
})
_TASK_TYPES = frozenset({"once", "daily", "weekly", "monthly", "recurring"})
_USER_STATUSES = frozenset({"enabled", "paused"})


def validate_cron_input(
    payload: dict[str, Any], current: dict[str, Any] | None = None,
) -> None:
    """拒绝错误字段和类型，防止计算调度前抛出未处理异常或悄悄采用默认值。"""
    unknown = set(payload) - _WRITABLE_FIELDS
    if unknown:
        raise InvalidRequestError(f"定时任务包含未知或只读字段：{', '.join(sorted(unknown))}")
    for field in ("title", "prompt"):
        if field in payload and (
            not isinstance(payload[field], str) or not payload[field].strip()
        ):
            raise InvalidRequestError(f"{field} 必须是非空字符串")
    task_type = payload.get("type", (current or {}).get("type"))
    if not isinstance(task_type, str) or task_type not in _TASK_TYPES:
        raise InvalidRequestError("type 必须是 once/daily/weekly/monthly/recurring")
    if "status" in payload:
        status = payload["status"]
        # 编辑可原样携带既有终态；新建及状态变更只允许用户可控状态。
        if not isinstance(status, str) or (
            status not in _USER_STATUSES
            and (current is None or status != current.get("status"))
        ):
            raise InvalidRequestError("status 只能设置 enabled 或 paused")
    if "time" in payload and "times" in payload:
        raise InvalidRequestError("time 与 times 不能同时提交")
    if "time" in payload and not isinstance(payload["time"], str):
        raise InvalidRequestError("time 必须是 HH:MM 字符串")
    for field, maximum in (("times", 24), ("weekdays", 7), ("month_days", 31)):
        if field not in payload:
            continue
        values = payload[field]
        if not isinstance(values, list) or not 1 <= len(values) <= maximum:
            raise InvalidRequestError(f"{field} 必须包含 1 到 {maximum} 个元素")
        if field == "times":
            if any(not isinstance(value, str) for value in values):
                raise InvalidRequestError("times 只能包含 HH:MM 字符串")
        elif any(
            isinstance(value, bool) or not isinstance(value, int)
            or not 1 <= value <= maximum for value in values
        ):
            raise InvalidRequestError(f"{field} 只能包含 1 到 {maximum} 的整数")
    incompatible = {
        "interval_seconds": task_type != "recurring",
        "time": task_type not in {"daily", "weekly", "monthly"},
        "times": task_type not in {"daily", "weekly", "monthly"},
        "weekdays": task_type != "weekly",
        "month_days": task_type != "monthly",
    }
    for field, invalid in incompatible.items():
        if field in payload and invalid:
            raise InvalidRequestError(f"{task_type} 任务不能提交 {field}")
    for field in ("start_date", "end_date"):
        if field in payload and payload[field] is not None and not isinstance(payload[field], str):
            raise InvalidRequestError(f"{field} 必须是 YYYY-MM-DD 字符串或 null")
    if "next_run_at" in payload:
        raw = payload["next_run_at"]
        if not isinstance(raw, str):
            raise InvalidRequestError("next_run_at 必须是带时区的 ISO 时间字符串")
        if raw.strip():
            try:
                parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            except ValueError:
                raise InvalidRequestError("next_run_at 必须是带时区的 ISO 时间字符串") from None
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise InvalidRequestError("next_run_at 必须显式包含时区，例如 +08:00")
