"""任务计划可修正操作的共享领域规则。"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from run.tasks.store import (
    PlanConflictError,
    PlanStore,
    PlanValidationError,
    STEP_ID_RE,
)


EDITABLE_PLAN_STATUSES = frozenset({"pending", "approved", "paused", "failed"})
# ``revise_remaining`` is the only mutation intentionally allowed while the
# plan is running, and it can touch only the still-pending suffix.
REVISION_PLAN_STATUSES = EDITABLE_PLAN_STATUSES | {"running"}
RESETTABLE_PLAN_STATUSES = frozenset(
    {"pending", "approved", "paused", "failed", "cancelled"}
)
RESETTABLE_STEP_STATUSES = frozenset({"failed", "cancelled"})
EDITABLE_STEP_FIELDS = frozenset(
    {"tool_name", "tool_arguments", "depends_on", "critical"}
)
_ACTIVATING_REASONS = frozenset({"auto_accept", "auto_retry_on_fix"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            if previous_status == "running":
                raise PlanValidationError(
                    f"运行中的步骤 {step_id} 不得通过普通 edit 修改"
                )
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


def revise_remaining_steps(
    store: PlanStore,
    plan_id: str,
    *,
    expected_revision: Any,
    steps: Any,
    retry_failed: bool = False,
) -> dict[str, Any]:
    """Reorder, insert, or delete only the not-yet-started plan suffix."""

    if not isinstance(steps, list):
        raise PlanValidationError("steps 必须是步骤数组")
    if not isinstance(retry_failed, bool):
        raise PlanValidationError("retry_failed 必须是布尔值")

    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        _require_revision(current, expected_revision)
        status = str(current.get("status") or "")
        if status not in REVISION_PLAN_STATUSES:
            raise PlanValidationError(
                f"计划 {plan_id} 当前状态为 {status!r}，不能修正剩余步骤"
            )
        original_steps = [
            copy.deepcopy(step)
            for step in current.get("steps") or []
            if isinstance(step, dict)
        ]
        if not original_steps:
            raise PlanValidationError("计划没有可修正的步骤")
        protected_indexes = [
            index for index, step in enumerate(original_steps)
            if step.get("status") != "pending"
        ]
        last_protected = max(protected_indexes, default=-1)
        if last_protected >= 0 and any(
            step.get("status") == "pending"
            for step in original_steps[:last_protected]
        ):
            raise PlanValidationError("仅支持修正当前步骤之后连续的 pending 步骤")

        by_id = {str(step.get("step_id")): step for step in original_steps}
        seen: set[str] = set()
        requested_positions: list[int] = []
        desired: list[tuple[int, dict[str, Any]]] = []
        allowed_fields = {
            "step_id", "position", "title", "description", "tool_name",
            "tool_arguments", "depends_on", "critical",
        }
        for index, patch in enumerate(steps):
            if not isinstance(patch, dict):
                raise PlanValidationError(f"步骤修正 {index} 必须是对象")
            step_id = patch.get("step_id")
            if not isinstance(step_id, str) or not STEP_ID_RE.fullmatch(step_id):
                raise PlanValidationError(f"步骤修正 {index} 的 step_id 无效")
            if step_id in seen:
                raise PlanValidationError(f"步骤 {step_id} 重复")
            seen.add(step_id)
            position = patch.get("position")
            if isinstance(position, bool) or not isinstance(position, int) or position < 1:
                raise PlanValidationError(f"步骤 {step_id} 的 position 必须是正整数")
            requested_positions.append(position)
            unknown = set(patch) - allowed_fields
            if unknown:
                raise PlanValidationError(
                    f"步骤 {step_id} 不支持字段：{', '.join(sorted(unknown))}"
                )
            original = by_id.get(step_id)
            if original is not None and original.get("status") != "pending":
                comparable = {
                    key: value for key, value in patch.items()
                    if key not in {"step_id", "position"}
                }
                for key, value in comparable.items():
                    if value != original.get(key):
                        raise PlanValidationError(
                            f"已开始步骤 {step_id} 不得修改"
                        )
                continue
            if original is not None:
                if "depends_on" not in patch:
                    raise PlanValidationError(
                        f"步骤 {step_id} 必须同时提供 depends_on"
                    )
                revised = copy.deepcopy(original)
                for key in allowed_fields - {"step_id", "position"}:
                    if key in patch:
                        revised[key] = copy.deepcopy(patch[key])
            else:
                required = {"title", "description", "depends_on"}
                missing = required - set(patch)
                if missing:
                    raise PlanValidationError(
                        f"新增步骤 {step_id} 缺少：{', '.join(sorted(missing))}"
                    )
                revised = {
                    "step_id": step_id,
                    "title": patch["title"],
                    "description": patch["description"],
                    "status": "pending",
                    "depends_on": copy.deepcopy(patch["depends_on"]),
                    "tool_name": patch.get("tool_name"),
                    "tool_arguments": copy.deepcopy(patch.get("tool_arguments") or {}),
                    "critical": patch.get("critical", True),
                    "result": None,
                    "error": None,
                    "started_at": "",
                    "finished_at": "",
                }
            desired.append((position, revised))

        if sorted(requested_positions) != list(range(1, len(requested_positions) + 1)):
            raise PlanValidationError("剩余步骤 position 必须从 1 连续编号")
        desired_steps = [
            step for _, step in sorted(desired, key=lambda item: item[0])
        ]

        protected_steps = original_steps[:last_protected + 1]
        for step in protected_steps:
            if step.get("status") == "failed" and retry_failed:
                step["status"] = "pending"
                step["error"] = None
                step["result"] = None
                step["started_at"] = ""
                step["finished_at"] = ""
        has_failed = any(
            step.get("status") == "failed"
            for step in protected_steps + desired_steps
        )
        # revise_remaining explicitly requests continuation of the corrected
        # plan. retry_failed controls failed steps only, not plan activation.
        if not has_failed and status in {"paused", "failed", "pending", "approved"}:
            current["status"] = "approved"
        current["steps"] = protected_steps + desired_steps
        return current

    return store.update(plan_id, mutate, note="修正剩余步骤")


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


def skip_plan_step(
    store: PlanStore,
    plan_id: str,
    step_id: str,
    *,
    expected_revision: Any,
) -> dict[str, Any]:
    """Explicitly skip one pending, non-critical branch of a plan."""

    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        _require_revision(current, expected_revision)
        plan_status = str(current.get("status") or "")
        if plan_status not in {"approved", "running", "paused"}:
            raise PlanValidationError(
                f"计划 {plan_id} 当前状态为 {plan_status!r}，无法跳过步骤"
            )
        target = _find_step(current, step_id)
        step_status = str(target.get("status") or "")
        if step_status != "pending":
            raise PlanValidationError(
                f"步骤 {step_id} 当前状态为 {step_status!r}，只有 pending 步骤可以跳过"
            )
        if bool(target.get("critical", True)):
            raise PlanValidationError(
                f"步骤 {step_id} 是关键步骤，不能跳过；请修正或重试该步骤"
            )
        target["status"] = "skipped"
        target["result"] = {
            "status": "skipped",
            "reason": "explicit_skip",
        }
        target["error"] = None
        target["finished_at"] = _now()
        current["current_step"] = step_id
        return current

    return store.update(plan_id, mutate, note=f"跳过步骤 {step_id}")
