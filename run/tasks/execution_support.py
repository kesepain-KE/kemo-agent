"""Shared, dependency-free helpers for task-plan execution.

The public ``run.tasks.executor`` module remains the compatibility facade, but
the step executor must not import that facade back at runtime.  Keeping the
small state-selection helpers and error type here removes that cycle while
preserving the existing public names through re-export.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class PlanExecutionError(RuntimeError):
    """Raised when a plan or one of its steps cannot change state."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_step(plan: dict[str, Any]) -> dict[str, Any] | None:
    """Select the first pending step whose dependencies have completed."""

    steps_by_id = {step["step_id"]: step for step in plan["steps"]}
    for step in plan["steps"]:
        if step["status"] != "pending":
            continue
        dependencies = step.get("depends_on") or []
        if all(
            steps_by_id.get(dependency, {}).get("status") == "completed"
            for dependency in dependencies
        ):
            return step
    return None


def _failed_steps_needing_fix(plan: dict[str, Any]) -> list[str]:
    """Return every critical failed step that requires an explicit repair."""

    return [
        str(step.get("step_id") or "")
        for step in (plan.get("steps") or [])
        if isinstance(step, dict)
        and str(step.get("step_id") or "")
        and step.get("status") == "failed"
        and bool(step.get("critical", True))
    ]
