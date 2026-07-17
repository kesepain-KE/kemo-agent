"""Entry-agnostic task plan execution core.

Reads the latest plan from disk, selects the next runnable step based on
dependencies, executes it through the existing ToolRunner, and persists
status changes atomically.  Disk is the single source of truth — no
in-memory state competes with it.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from events import RunEvent, error_event
from provider.factory import create_provider
from run.config import load_config
from run.task_plan_store import (
    PlanError,
    PlanNotFoundError,
    PlanStore,
    PlanValidationError,
)
from run.tools import ToolRegistry, discover_tools, execute_tool, ToolError


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
) -> Iterator[RunEvent]:
    """Execute a plan step by step, yielding events.

    The plan must be in ``approved`` or ``running`` status.  Each step is
    persisted before and after execution.  Critical step failures pause the
    plan; non-critical failures are recorded and execution continues.

    On process restart, ``recover_interrupted`` (called at startup) converts
    leftover ``running`` steps to ``pending`` and pauses the plan.
    """
    cfg = config or load_config(user, root)
    if tool_registry is None:
        tool_config = cfg.get("tools") or {}
        tool_registry = (
            discover_tools(root, user)
            if bool(tool_config.get("enabled", True))
            else ToolRegistry({})
        )
    tool_timeout = float((cfg.get("tools") or {}).get("timeout", 60))

    store = PlanStore(root, user)

    try:
        plan = store.read(plan_id)
    except (PlanNotFoundError, PlanError) as exc:
        yield error_event(exc, phase="plan_read")
        return

    if plan["status"] not in ("approved", "running"):
        yield error_event(
            PlanExecutionError(
                f"计划 {plan_id} 当前状态为 {plan['status']!r}，无法执行"
            ),
            phase="plan_status",
        )
        return

    # Transition to running if still approved
    if plan["status"] == "approved":
        try:
            plan = store.update(plan_id, lambda p: {**p, "status": "running"})
        except (PlanError, PlanValidationError) as exc:
            yield error_event(exc, phase="plan_status")
            return

    while True:
        if cancel_event is not None and cancel_event.is_set():
            return

        # Re-read from disk each iteration to pick up external edits
        try:
            plan = store.read(plan_id)
        except (PlanNotFoundError, PlanError) as exc:
            yield error_event(exc, phase="plan_read")
            return

        if plan["status"] != "running":
            # Plan was paused or cancelled externally
            yield RunEvent(
                type="done",
                metadata={
                    "plan_id": plan_id,
                    "status": plan["status"],
                    "reason": "plan_no_longer_running",
                },
            )
            return

        step = _next_step(plan)
        if step is None:
            # Check if all steps are in a terminal state
            terminal = {"completed", "failed", "skipped", "cancelled"}
            all_done = all(s["status"] in terminal for s in plan["steps"])
            if all_done:
                try:
                    plan = store.update(plan_id, lambda p: {**p, "status": "completed"})
                except (PlanError, PlanValidationError) as exc:
                    yield error_event(exc, phase="plan_complete")
                    return
                yield RunEvent(
                    type="done",
                    metadata={
                        "plan_id": plan_id,
                        "status": "completed",
                        "steps_total": len(plan["steps"]),
                        "steps_completed": sum(
                            1 for s in plan["steps"] if s["status"] == "completed"
                        ),
                    },
                )
                return
            # There are still pending steps but none are runnable (all blocked by
            # failed deps) — mark plan as failed
            try:
                plan = store.update(plan_id, lambda p: {**p, "status": "failed"})
            except (PlanError, PlanValidationError) as exc:
                yield error_event(exc, phase="plan_fail")
                return
            yield RunEvent(
                type="done",
                metadata={
                    "plan_id": plan_id,
                    "status": "failed",
                    "reason": "no_runnable_step",
                },
            )
            return

        step_id = step["step_id"]
        tool_name = step.get("tool_name")
        tool_arguments = step.get("tool_arguments") or {}

        # Persist running status
        def _mark_running(p: dict) -> dict:
            for s in p["steps"]:
                if s["step_id"] == step_id:
                    s["status"] = "running"
                    s["started_at"] = _now()
                    break
            p["current_step"] = step_id
            return p

        try:
            plan = store.update(plan_id, _mark_running)
        except (PlanError, PlanValidationError) as exc:
            yield error_event(exc, phase="step_start")
            return

        yield RunEvent(
            type="tool_call_start",
            tool_call_id=step_id,
            tool_name=tool_name or "",
            arguments=tool_arguments,
            metadata={"plan_id": plan_id, "step_id": step_id},
        )

        if cancel_event is not None and cancel_event.is_set():
            return

        # Execute the tool
        status: str
        result_payload: Any
        error_payload: dict[str, Any] | None = None

        if tool_name is None:
            status = "completed"
            result_payload = {"ok": True, "result": None}
        else:
            try:
                definition = tool_registry.get(tool_name)
                result = execute_tool(
                    definition,
                    tool_arguments,
                    context={
                        "root": str(root),
                        "user": user,
                        "source": f"plan:{plan_id}",
                        "session_id": plan.get("session_id", ""),
                        "window": "",
                        "plan_id": plan_id,
                        "step_id": step_id,
                    },
                    timeout=tool_timeout,
                    cancel_event=cancel_event,
                )
                status = "completed"
                result_payload = {"ok": True, "result": result}
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                    raise
                status = "failed"
                result_payload = {
                    "ok": False,
                    "error": {
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    },
                }
                error_payload = result_payload["error"]

        yield RunEvent(
            type="tool_call_result",
            tool_call_id=step_id,
            tool_name=tool_name or "",
            arguments=tool_arguments,
            result=result_payload,
            metadata={
                "plan_id": plan_id,
                "step_id": step_id,
                "status": status,
            },
        )

        # Persist step result
        def _mark_step_result(p: dict) -> dict:
            for s in p["steps"]:
                if s["step_id"] == step_id:
                    s["status"] = status
                    s["result"] = result_payload if status == "completed" else None
                    s["error"] = error_payload
                    s["finished_at"] = _now()
                    break
            return p

        try:
            plan = store.update(plan_id, _mark_step_result)
        except (PlanError, PlanValidationError) as exc:
            yield error_event(exc, phase="step_persist")
            return

        if status == "failed":
            critical = step.get("critical", True)
            if critical:
                # Pause the plan
                try:
                    plan = store.update(plan_id, lambda p: {**p, "status": "paused"})
                except (PlanError, PlanValidationError) as exc:
                    yield error_event(exc, phase="plan_pause")
                    return
                yield RunEvent(
                    type="done",
                    metadata={
                        "plan_id": plan_id,
                        "status": "paused",
                        "reason": "critical_step_failed",
                        "step_id": step_id,
                    },
                )
                return
            # Non-critical: continue to next step
        if cancel_event is not None and cancel_event.is_set():
            return


def approve_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Transition a plan from pending to approved."""
    store = PlanStore(root, user)
    plan = store.read(plan_id)
    if plan["status"] != "pending":
        raise PlanExecutionError(
            f"计划 {plan_id} 当前状态为 {plan['status']!r}，无法批准"
        )
    return store.update(plan_id, lambda p: {**p, "status": "approved"})


def pause_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Pause a running or approved plan."""
    store = PlanStore(root, user)
    plan = store.read(plan_id)
    if plan["status"] not in ("running", "approved"):
        raise PlanExecutionError(
            f"计划 {plan_id} 当前状态为 {plan['status']!r}，无法暂停"
        )
    return store.update(plan_id, lambda p: {**p, "status": "paused"})


def resume_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Resume a paused plan by transitioning to running."""
    store = PlanStore(root, user)
    plan = store.read(plan_id)
    if plan["status"] != "paused":
        raise PlanExecutionError(
            f"计划 {plan_id} 当前状态为 {plan['status']!r}，无法恢复"
        )
    return store.update(plan_id, lambda p: {**p, "status": "running"})


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
