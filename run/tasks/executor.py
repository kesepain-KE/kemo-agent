"""与条目无关的任务计划执行核心。

从磁盘读取最新的计划，根据选择下一个可运行的步骤
依赖项，通过现有的 ToolRunner 执行它，并持久化
状态以原子方式改变。  磁盘是唯一的事实来源——不
内存状态与之竞争。"""

from __future__ import annotations

import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from events import RunEvent, error_event
from provider.factory import create_provider
from run.config import load_config
from run.tasks.store import (
    PlanError,
    PlanNotFoundError,
    PlanStore,
    PlanValidationError,
)
from run.tools import (
    ToolResultTooLargeError,
    ToolRegistry,
    apply_runtime_tool_policy,
    discover_tools,
    execute_tool,
)
from run.tasks.plan_execution import execute_plan as _execute_plan_impl


class PlanExecutionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_step(plan: dict[str, Any]) -> dict[str, Any] | None:
    """Select the next runnable step: pending with all deps completed."""
    steps_by_id = {s["step_id"]: s for s in plan["steps"]}
    for step in plan["steps"]:
        if step["status"] != "pending":
            continue
        deps = step.get("depends_on") or []
        if all(
            steps_by_id.get(dep, {}).get("status") == "completed"
            for dep in deps
        ):
            return step
    return None


def _failed_steps_needing_fix(plan: dict[str, Any]) -> list[str]:
    """Return every critical failed step that must be explicitly repaired."""

    return [
        str(step.get("step_id") or "")
        for step in (plan.get("steps") or [])
        if isinstance(step, dict)
        and str(step.get("step_id") or "")
        and step.get("status") == "failed"
        and bool(step.get("critical", True))
    ]


def _is_plan_active(status: str) -> bool:
    return status in ("approved", "running")


def execute_plan(
    *,
    root: Path,
    user: str,
    plan_id: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry: ToolRegistry | None = None,
    cancel_event: threading.Event | None = None,
    event_callback: Callable[[RunEvent], None] | None = None,
    agent_event_source: Callable[[dict[str, Any]], Iterator[RunEvent]] | None = None,
) -> Iterator[RunEvent]:
    """Compatibility entry point delegated to the plan execution module."""

    return _execute_plan_impl(
        root=root,
        user=user,
        plan_id=plan_id,
        config=config,
        provider_factory=provider_factory,
        tool_registry=tool_registry,
        cancel_event=cancel_event,
        event_callback=event_callback,
        agent_event_source=agent_event_source,
    )

def approve_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Transition a plan from pending to approved."""
    store = PlanStore(root, user)

    def _approve(plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("status") != "pending":
            raise PlanExecutionError(
                f"计划 {plan_id} 当前状态为 {plan.get('status')!r}，无法批准"
            )
        return {**plan, "status": "approved"}

    return store.update(plan_id, _approve)


def pause_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Pause a running or approved plan."""
    store = PlanStore(root, user)

    def _pause(plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("status") not in ("running", "approved"):
            raise PlanExecutionError(
                f"计划 {plan_id} 当前状态为 {plan.get('status')!r}，无法暂停"
            )
        return {**plan, "status": "paused"}

    return store.update(plan_id, _pause)


def resume_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Make a paused plan eligible for one executor to claim."""
    store = PlanStore(root, user)

    def _resume(plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("status") != "paused":
            raise PlanExecutionError(
                f"计划 {plan_id} 当前状态为 {plan.get('status')!r}，无法恢复"
            )
        return {**plan, "status": "approved"}

    return store.update(plan_id, _resume)


def cancel_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Cancel a plan and skip all pending steps."""
    store = PlanStore(root, user)

    def _cancel(p: dict) -> dict:
        p["status"] = "cancelled"
        for s in p["steps"]:
            if s["status"] == "pending":
                s["status"] = "cancelled"
            elif s["status"] == "running":
                s["status"] = "pending"
        return p

    return store.update(plan_id, _cancel)


def get_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    return PlanStore(root, user).read(plan_id)


def list_plans(root: Path, user: str) -> list[dict[str, Any]]:
    return PlanStore(root, user).list_plans()
