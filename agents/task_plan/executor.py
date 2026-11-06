from __future__ import annotations

from typing import Any

from run.agents import AgentOutputError, AgentRunResult


def execute(context, input_data: dict[str, Any]) -> AgentRunResult:
    requested_action = input_data["action"]  # Validated by AgentRunner's input schema.
    result = context.run_model(input_data)
    action = result.data.get("action")
    allowed = {requested_action}
    if requested_action == "create":
        allowed.add("skip")
    if action not in allowed:
        raise AgentOutputError(
            f"task_plan 输出 action 与请求不一致：{action!r}"
        )
    if action in {"create", "edit"}:
        if not isinstance(result.data.get("steps"), list):
            raise AgentOutputError("task_plan 输出缺少 steps 数组")
        if bool(input_data.get("auto_accept", False)):
            result.data["reminder"] = ""
        else:
            result.data["reminder"] = (
                "当前任务计划已修改，请让用户点击批准后执行"
                if action == "edit"
                else "当前任务计划已创建，请让用户点击批准后执行"
            )
    return result
