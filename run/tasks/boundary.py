"""Detect the execution boundary created by a persisted task plan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TaskPlanCreationBoundary:
    plan_id: str
    status: str
    auto_accept: bool

    @property
    def awaiting_user_approval(self) -> bool:
        return self.status == "pending"

    @property
    def stop_reason(self) -> str:
        return (
            "task_plan_approval_required"
            if self.awaiting_user_approval
            else "task_plan_created"
        )

    @property
    def message(self) -> str:
        if self.awaiting_user_approval:
            return "任务计划已创建并等待用户批准；当前运行已在计划边界停止。"
        return "任务计划已创建并自动批准；当前运行已停止，后续由任务计划执行器处理。"


def detect_task_plan_creation_boundary(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result_payload: Any,
) -> TaskPlanCreationBoundary | None:
    """Return a boundary only for a successfully persisted plan creation."""

    if tool_name != "subagent_dispatch":
        return None
    if str(arguments.get("action") or "").strip().casefold() != "call":
        return None
    if str(arguments.get("agent") or "").strip() != "task_plan":
        return None
    if not isinstance(result_payload, dict) or result_payload.get("ok") is not True:
        return None
    response = result_payload.get("result")
    if not isinstance(response, dict):
        return None
    if str(response.get("status") or "").strip().casefold() != "completed":
        return None
    data = response.get("data")
    if not isinstance(data, dict) or data.get("action") != "create":
        return None
    plan = response.get("plan")
    if not isinstance(plan, dict):
        return None
    plan_id = str(plan.get("plan_id") or "").strip()
    status = str(plan.get("status") or "").strip().casefold()
    auto_accept = plan.get("auto_accept")
    if not plan_id or not isinstance(auto_accept, bool):
        return None
    if status in {"pending", "approved"}:
        return TaskPlanCreationBoundary(plan_id, status, auto_accept)
    return None
