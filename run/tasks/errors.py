"""Stable task-plan domain errors shared by storage helpers."""


class PlanError(RuntimeError):
    pass


class PlanNotFoundError(PlanError):
    pass


class PlanValidationError(PlanError):
    pass


class PlanConflictError(PlanError):
    code = "plan_revision_conflict"


__all__ = [
    "PlanConflictError",
    "PlanError",
    "PlanNotFoundError",
    "PlanValidationError",
]
