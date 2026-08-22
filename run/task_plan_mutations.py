"""任务计划可修正操作的共享领域规则。"""

from __future__ import annotations

from typing import Any

from run.task_plan_store import (
    PlanConflictError,
    PlanStore,
    PlanValidationError,
)


EDITABLE_PLAN_STATUSES = frozenset({"pending", "approved", "paused", "failed"})
RESETTABLE_PLAN_STATUSES = frozenset(
    {"pending", "approved", "paused", "failed", "cancelled"}
)
RESETTABLE_STEP_STATUSES = frozenset({"failed", "cancelled"})
EDITABLE_STEP_FIELDS = frozenset(
    {"tool_name", "tool_arguments", "depends_on", "critical"}
)
_ACTIVATING_REASONS = frozenset({"auto_accept", "auto_retry_on_fix"})


def _require_revision(current: dict[str, Any], expected_revision: Any) -> None:
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise PlanValidationError("revision 必须是正整数")
    if expected_revision < 1:
        raise PlanValidationError("revision 必须是正整数")
    if int(current.get("revision", 0)) != expected_revision:
        raise PlanConflictError("计划版本已变化，请重新读取后再操作")


def _find_step(plan: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in plan.get("steps") or []:
        if isinstance(step, dict) and step.get("step_id") == step_id:
            return step
    raise PlanValidationError(f"步骤不存在：{step_id}")


def plan_fix_activation_reason(
    plan: dict[str, Any],
    *,
    auto_retry_on_fix: bool,
) -> str:
    """Explain whether a paused/failed plan should reactivate after a fix."""

    if str(plan.get("status") or "") not in {"paused", "failed"}:
        return "plan_not_failed"
    if bool(plan.get("auto_accept", False)):
        return "auto_accept"
    if auto_retry_on_fix:
        return "auto_retry_on_fix"
    return "activation_disabled"


def plan_fix_activation_result(
    original: dict[str, Any],
    updated: dict[str, Any],
    *,
    auto_retry_on_fix: bool,
) -> tuple[bool, str]:
    """Return the actual activation outcome after an edit or retry mutation."""

    requested_reason = plan_fix_activation_reason(
        original,
        auto_retry_on_fix=auto_retry_on_fix,
    )
    activated = (
        requested_reason in _ACTIVATING_REASONS
        and str(updated.get("status") or "") == "approved"
    )
    if activated or requested_reason not in _ACTIVATING_REASONS:
        return activated, requested_reason
    return False, "fix_incomplete"


def ensure_completed_steps_preserved(
    current: dict[str, Any],
    proposed_steps: Any,
) -> None:
    """Reject deletion or mutation of completed steps in a full-list update."""

    if not isinstance(proposed_steps, list):
        raise PlanValidationError("steps 必须是数组")
    proposed_by_id = {
        str(step.get("step_id") or ""): step
        for step in proposed_steps
        if isinstance(step, dict)
    }
    for original in current.get("steps") or []:
        if not isinstance(original, dict) or original.get("status") != "completed":
            continue
        step_id = str(original.get("step_id") or "")
        proposed = proposed_by_id.get(step_id)
        if proposed is None:
            raise PlanValidationError(f"已完成步骤 {step_id} 不得删除")
        if proposed != original:
            raise PlanValidationError(f"已完成步骤 {step_id} 不得修改")


def edit_plan_fields(
    store: PlanStore,
    plan_id: str,
    *,
    expected_revision: Any,
    changes: dict[str, Any],
    auto_retry_on_fix: bool = False,
) -> dict[str, Any]:
    """Atomically edit plan metadata and execution fields on non-completed steps."""

    if not isinstance(changes, dict):
        raise PlanValidationError("计划修改内容必须是对象")
    unknown = set(changes) - {"title", "description", "steps"}
    if unknown:
        raise PlanValidationError(f"不支持修改字段：{', '.join(sorted(unknown))}")
    if not changes:
        raise PlanValidationError("至少需要提供一项计划修改")
    step_changes = changes.get("steps")
    if step_changes is not None and not isinstance(step_changes, list):
        raise PlanValidationError("steps 必须是步骤修改数组")

    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        _require_revision(current, expected_revision)
        status = str(current.get("status") or "")
        if status not in EDITABLE_PLAN_STATUSES:
            raise PlanValidationError(
                f"计划 {plan_id} 当前状态为 {status!r}，"
                "只能编辑 pending/approved/paused/failed 状态的计划"
            )
        if "title" in changes:
            title = changes["title"]
            if not isinstance(title, str) or not title.strip():
                raise PlanValidationError("title 不能为空")
            current["title"] = title
        if "description" in changes:
            description = changes["description"]
            if not isinstance(description, str) or not description.strip():
                raise PlanValidationError("description 不能为空")
            current["description"] = description
        activation_requested = plan_fix_activation_reason(
            current,
            auto_retry_on_fix=auto_retry_on_fix,
        ) in _ACTIVATING_REASONS
        for index, patch in enumerate(step_changes or []):
            if not isinstance(patch, dict):
                raise PlanValidationError(f"步骤修改 {index} 必须是对象")
            step_id = patch.get("step_id")
            if not isinstance(step_id, str) or not step_id:
                raise PlanValidationError(f"步骤修改 {index} 缺少 step_id")
            unknown_step_fields = set(patch) - ({"step_id"} | EDITABLE_STEP_FIELDS)
            if unknown_step_fields:
                raise PlanValidationError(
                    f"步骤 {step_id} 不支持修改字段："
                    f"{', '.join(sorted(unknown_step_fields))}"
                )
            if len(patch) == 1:
                raise PlanValidationError(f"步骤 {step_id} 没有提供修改字段")
            target = _find_step(current, step_id)
            if target.get("status") == "completed":
                raise PlanValidationError(f"已完成步骤 {step_id} 不得修改")
            previous_status = str(target.get("status") or "")
            for field in EDITABLE_STEP_FIELDS:
                if field in patch:
                    target[field] = patch[field]
            if previous_status in RESETTABLE_STEP_STATUSES and activation_requested:
                target["status"] = "pending"
                target["error"] = None
                target["result"] = None
                target["started_at"] = ""
                target["finished_at"] = ""
        has_unresolved_steps = any(
            isinstance(step, dict)
            and str(step.get("status") or "") in RESETTABLE_STEP_STATUSES
            for step in current.get("steps") or []
        )
        if activation_requested and not has_unresolved_steps:
            current["status"] = "approved"
        return current

    return store.update(plan_id, mutate, note="修改计划")


def reset_plan_step(
    store: PlanStore,
    plan_id: str,
    step_id: str,
    *,
    expected_revision: Any,
    activate_paused: bool,
    auto_retry_on_fix: bool = False,
) -> dict[str, Any]:
    """Reset one failed/cancelled step; retry may reactivate by configured policy."""

    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        _require_revision(current, expected_revision)
        plan_status = str(current.get("status") or "")
        if plan_status not in RESETTABLE_PLAN_STATUSES:
            raise PlanValidationError(
                f"计划 {plan_id} 当前状态为 {plan_status!r}，无法重置步骤"
            )
        target = _find_step(current, step_id)
        step_status = str(target.get("status") or "")
        if step_status == "completed":
            raise PlanValidationError(f"已完成步骤 {step_id} 不得重置")
        if step_status not in RESETTABLE_STEP_STATUSES:
            raise PlanValidationError(
                f"步骤 {step_id} 当前状态为 {step_status!r}，"
                "只有 failed/cancelled 步骤可以重置"
            )
        target["status"] = "pending"
        target["error"] = None
        target["result"] = None
        target["started_at"] = ""
        target["finished_at"] = ""
        has_unresolved_steps = any(
            isinstance(step, dict)
            and str(step.get("status") or "") in RESETTABLE_STEP_STATUSES
            for step in current.get("steps") or []
        )
        if activate_paused and not has_unresolved_steps and plan_fix_activation_reason(
            current,
            auto_retry_on_fix=auto_retry_on_fix,
        ) in _ACTIVATING_REASONS:
            current["status"] = "approved"
        return current

    return store.update(
        plan_id,
        mutate,
        note=("重试步骤" if activate_paused else "重置步骤") + f" {step_id}",
    )
