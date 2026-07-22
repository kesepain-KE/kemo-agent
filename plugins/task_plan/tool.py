"""Runtime task-plan management exposed to the main agent."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run.task_plan_executor import (
    approve_plan,
    cancel_plan,
    pause_plan,
    resume_plan,
)
from run.task_plan_store import (
    PLAN_ID_RE,
    STEP_ID_RE,
    PlanError,
    PlanNotFoundError,
    PlanStore,
)
from run.users import validate_user_name


_ACTIVE_STATUSES = frozenset({"pending", "approved", "running", "paused"})
_STEP_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "skipped", "cancelled"}
)
_ACTIONS = frozenset(
    {"view", "list", "step_done", "step_fail", "abort", "approve", "pause", "resume"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


def _find_active_plan(store: PlanStore) -> dict[str, Any] | None:
    for plan in store.list_plans():
        if str(plan.get("status") or "") in _ACTIVE_STATUSES:
            return plan
    return None


def _step_by_id(plan: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    return next(
        (
            step
            for step in plan.get("steps") or []
            if isinstance(step, dict) and step.get("step_id") == step_id
        ),
        None,
    )


def _next_runnable_step(plan: dict[str, Any]) -> dict[str, Any] | None:
    steps = [step for step in (plan.get("steps") or []) if isinstance(step, dict)]
    by_id = {str(step.get("step_id") or ""): step for step in steps}
    for step in steps:
        if step.get("status") != "pending":
            continue
        dependencies = [str(value) for value in (step.get("depends_on") or [])]
        if all(by_id.get(dependency, {}).get("status") == "completed" for dependency in dependencies):
            return step
    return None


def _progress_payload(plan: dict[str, Any], *, completed_step_id: str = "") -> dict[str, Any]:
    steps = [step for step in (plan.get("steps") or []) if isinstance(step, dict)]
    completed = sum(step.get("status") == "completed" for step in steps)
    remaining_steps = [
        step for step in steps if step.get("status") not in _STEP_TERMINAL_STATUSES
    ]
    next_step = _next_runnable_step(plan) if plan.get("status") == "running" else None
    completed_step = _step_by_id(plan, completed_step_id) if completed_step_id else None
    return {
        "completed_step": completed_step,
        "progress": {
            "completed": completed,
            "total": len(steps),
            "remaining": len(remaining_steps),
        },
        "next_step": next_step,
        "remaining_steps": remaining_steps,
        "plan_status": str(plan.get("status") or ""),
    }


def _auto_complete_check(store: PlanStore, plan_id: str) -> dict[str, Any]:
    plan = store.read(plan_id)
    steps = plan.get("steps") or []
    if steps and all(
        isinstance(step, dict) and step.get("status") in _STEP_TERMINAL_STATUSES
        for step in steps
    ) and all(step.get("status") == "completed" for step in steps):
        if plan.get("status") != "completed":
            return store.update(
                plan_id,
                lambda current: {**current, "status": "completed"},
            )
    return plan


def _step_done(
    store: PlanStore,
    plan_id: str,
    step_id: str,
    result_text: str,
    *,
    allow_paused: bool = False,
) -> dict[str, Any]:
    current = store.read(plan_id)
    step = _step_by_id(current, step_id)
    if step is None:
        raise ValueError(f"步骤不存在: {step_id}")
    if step.get("status") == "completed":
        return current
    if step.get("status") in {"failed", "skipped", "cancelled"}:
        raise ValueError(f"步骤 {step_id} 当前状态为 {step.get('status')!r}，无法完成")
    allowed_statuses = {"approved", "running"}
    if allow_paused:
        allowed_statuses.add("paused")
    if current.get("status") not in allowed_statuses:
        raise ValueError(
            f"计划 {plan_id} 当前状态为 {current.get('status')!r}，无法更新步骤"
        )

    def mark(plan: dict[str, Any]) -> dict[str, Any]:
        target = _step_by_id(plan, step_id)
        if target is None:
            raise ValueError(f"步骤不存在: {step_id}")
        target["status"] = "completed"
        target["error"] = None
        if result_text:
            target["result"] = result_text
        target["finished_at"] = _now()
        plan["current_step"] = step_id
        if plan.get("status") == "approved":
            plan["status"] = "running"
        return plan

    store.update(plan_id, mark)
    return _auto_complete_check(store, plan_id)


def _step_fail(
    store: PlanStore,
    plan_id: str,
    step_id: str,
    error_text: str,
) -> dict[str, Any]:
    current = store.read(plan_id)
    step = _step_by_id(current, step_id)
    if step is None:
        raise ValueError(f"步骤不存在: {step_id}")
    if step.get("status") in _STEP_TERMINAL_STATUSES:
        raise ValueError(f"步骤 {step_id} 当前状态为 {step.get('status')!r}，无法失败")
    if current.get("status") not in {"approved", "running", "paused"}:
        raise ValueError(
            f"计划 {plan_id} 当前状态为 {current.get('status')!r}，无法更新步骤"
        )

    def mark(plan: dict[str, Any]) -> dict[str, Any]:
        target = _step_by_id(plan, step_id)
        if target is None:
            raise ValueError(f"步骤不存在: {step_id}")
        target["status"] = "failed"
        target["result"] = None
        target["error"] = {
            "message": error_text,
            "exception_type": "ManualStepFailure",
        }
        target["finished_at"] = _now()
        plan["status"] = "paused"
        plan["current_step"] = step_id
        return plan

    return store.update(plan_id, mark)


def run(
    *,
    action: str,
    plan_id: str = "",
    step_id: str = "",
    result: str = "",
    error: str = "",
    context: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(context, dict) or not context.get("root") or not context.get("user"):
        raise ValueError("工具上下文缺少 root 或 user")
    root = Path(str(context["root"])).resolve()
    try:
        user = validate_user_name(str(context["user"]))
    except Exception as exc:
        raise ValueError(str(exc)) from exc
    store = PlanStore(root, user)
    selected_action = str(action or "").strip().casefold()
    if selected_action not in _ACTIONS:
        return _result(False, error=f"未知 action: {selected_action or action}")
    if (
        str(context.get("task_plan_mode") or "") == "executor_managed"
        and selected_action in {"step_done", "step_fail"}
    ):
        return _result(
            False,
            error="当前计划步骤由框架执行器维护状态，请只报告执行结果，不要调用 step_done 或 step_fail",
        )

    if selected_action == "list":
        plans = store.list_plans()
        return _result(True, plans=plans, total=len(plans))

    selected_plan_id = str(plan_id or "").strip()
    if not selected_plan_id and selected_action in {"view", "abort"}:
        active = _find_active_plan(store)
        if active is None:
            return _result(False, error="没有活跃计划，请指定 plan_id")
        selected_plan_id = str(active["plan_id"])
    if not selected_plan_id:
        return _result(False, error=f"{selected_action or '该操作'} 需要 plan_id")
    if PLAN_ID_RE.fullmatch(selected_plan_id) is None:
        return _result(False, error=f"plan_id 无效: {selected_plan_id}")

    if selected_action == "view":
        try:
            return _result(True, plan=store.read(selected_plan_id))
        except PlanNotFoundError:
            return _result(False, error=f"计划不存在: {selected_plan_id}")
        except PlanError as exc:
            return _result(False, error=str(exc))

    if selected_action == "step_done":
        selected_step_id = str(step_id or "").strip()
        if not selected_step_id:
            return _result(False, error="step_done 需要 step_id")
        if STEP_ID_RE.fullmatch(selected_step_id) is None:
            return _result(False, error=f"step_id 无效: {selected_step_id}")
        try:
            plan = _step_done(
                store,
                selected_plan_id,
                selected_step_id,
                str(result or ""),
                allow_paused=(
                    str(context.get("task_plan_mode") or "") == "agent_managed"
                    and str(context.get("task_plan_id") or "") == selected_plan_id
                ),
            )
            return _result(
                True,
                plan=plan,
                **_progress_payload(plan, completed_step_id=selected_step_id),
            )
        except (PlanError, ValueError) as exc:
            return _result(False, error=str(exc))

    if selected_action == "step_fail":
        selected_step_id = str(step_id or "").strip()
        error_text = str(error or "").strip()
        if not selected_step_id or not error_text:
            return _result(False, error="step_fail 需要 step_id 和 error")
        if STEP_ID_RE.fullmatch(selected_step_id) is None:
            return _result(False, error=f"step_id 无效: {selected_step_id}")
        try:
            plan = _step_fail(store, selected_plan_id, selected_step_id, error_text)
            return _result(
                True,
                plan=plan,
                **_progress_payload(plan, completed_step_id=selected_step_id),
            )
        except (PlanError, ValueError) as exc:
            return _result(False, error=str(exc))

    state_actions = {
        "abort": cancel_plan,
        "approve": approve_plan,
        "pause": pause_plan,
        "resume": resume_plan,
    }
    operation = state_actions.get(selected_action)
    try:
        return _result(True, plan=operation(root, user, selected_plan_id))
    except Exception as exc:
        return _result(False, error=str(exc))
