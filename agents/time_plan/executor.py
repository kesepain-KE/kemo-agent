from __future__ import annotations

from typing import Any

from run.agent_runner import AgentOutputError, AgentRunResult

VALID_ACTIONS = frozenset({"create", "edit", "delete"})
VALID_TYPES = frozenset({"recurring", "daily", "once"})

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
    elif task_type == "daily":
        time_str = result.data.get("time")
        if not isinstance(time_str, str) or len(time_str) != 5 or time_str[2] != ":":
            raise AgentOutputError(f"time_plan daily 的 time 格式必须是 HH:MM，收到 {time_str!r}")

    if not result.data.get("prompt"):
        raise AgentOutputError("time_plan 输出缺少 prompt")

    return result
