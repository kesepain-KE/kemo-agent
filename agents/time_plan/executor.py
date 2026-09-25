from __future__ import annotations

from typing import Any

from run.agents import AgentOutputError, AgentRunResult

VALID_ACTIONS = frozenset({"create", "edit", "delete"})
VALID_TYPES = frozenset({"recurring", "daily", "weekly", "monthly", "once"})

REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "create": frozenset({"action", "user_request", "current_time_beijing"}),
    "edit": frozenset(
        {"action", "user_request", "current_time_beijing", "existing_task", "edit_request"}
    ),
    "delete": frozenset({"action", "existing_task"}),
}


def execute(context, input_data: dict[str, Any]) -> AgentRunResult:
    action = input_data.get("action")
    if action not in VALID_ACTIONS:
        raise AgentOutputError(f"time_plan action 必须是 {sorted(VALID_ACTIONS)} 之一，收到 {action!r}")

    missing = sorted(REQUIRED_FIELDS[action] - set(input_data))
    if missing:
        raise AgentOutputError(f"time_plan {action} 缺少字段：{', '.join(missing)}")

    result = context.run_model(input_data)

    output_action = result.data.get("action")
    allowed = {action}
    if action == "create":
        allowed.add("skip")
    if output_action not in allowed:
        raise AgentOutputError(f"time_plan 输出 action 与请求不一致：{output_action!r}")

    if output_action == "skip":
        return result

    task_type = result.data.get("type")
    if task_type not in VALID_TYPES:
        raise AgentOutputError(
            f"time_plan 输出 type 必须是 {sorted(VALID_TYPES)} 之一，收到 {task_type!r}"
        )

    if task_type == "recurring":
        interval = result.data.get("interval_seconds")
        if not isinstance(interval, (int, float)) or interval < 60:
            raise AgentOutputError(
                f"time_plan recurring 的 interval_seconds 必须 ≥ 60，收到 {interval!r}"
            )
    elif task_type in {"daily", "weekly", "monthly"}:
        time_str = result.data.get("time")
        times = result.data.get("times")
        valid_time = isinstance(time_str, str) and len(time_str) == 5 and time_str[2] == ":"
        valid_times = isinstance(times, list) and bool(times) and all(
            isinstance(item, str) and len(item) == 5 and item[2] == ":" for item in times
        )
        if valid_time == valid_times:
            raise AgentOutputError(f"time_plan {task_type} 必须且只能输出有效的 time 或 times")
        if task_type == "weekly":
            weekdays = result.data.get("weekdays")
            if not isinstance(weekdays, list) or not weekdays or any(
                isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 7
                for item in weekdays
            ):
                raise AgentOutputError("time_plan weekly 需要 1 到 7 的 weekdays")
        if task_type == "monthly":
            month_days = result.data.get("month_days")
            if not isinstance(month_days, list) or not month_days or any(
                isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 31
                for item in month_days
            ):
                raise AgentOutputError("time_plan monthly 需要 1 到 31 的 month_days")

    start_date = result.data.get("start_date")
    end_date = result.data.get("end_date")
    if start_date and (not isinstance(start_date, str) or len(start_date) != 10):
        raise AgentOutputError("time_plan start_date 必须是 YYYY-MM-DD")
    if end_date and (not isinstance(end_date, str) or len(end_date) != 10):
        raise AgentOutputError("time_plan end_date 必须是 YYYY-MM-DD")
    max_runs = result.data.get("max_runs")
    if max_runs not in (None, 0) and (isinstance(max_runs, bool) or not isinstance(max_runs, int) or max_runs < 1):
        raise AgentOutputError("time_plan max_runs 必须是 >= 1 的整数")

    if not result.data.get("prompt"):
        raise AgentOutputError("time_plan 输出缺少 prompt")

    return result
