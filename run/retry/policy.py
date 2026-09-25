"""Single retry decision point shared by conversation and subagent runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from events import RunEvent


MAX_RETRIES = 5
MAX_ATTEMPTS = MAX_RETRIES + 1
BUDGET_EXHAUSTED = "retry_budget_exhausted"


@dataclass(frozen=True, slots=True)
class FailureView:
    retryable: bool | None = None
    retryable_declared: bool = False
    category: str = ""
    code: str = ""
    status_code: int | None = None
    phase: str = "run"
    attempt_count: int | None = None
    retry_after_ms: int | None = None
    cancelled: bool = False
    budget_exhausted: bool = False
    exception_type: str = ""


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_text(value: Any) -> str:
    return str(value or "").strip()[:160]


def _mapping_containers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    containers = [value]
    for key in ("details", "incomplete_details", "failure", "error", "metadata"):
        nested = value.get(key)
        if isinstance(nested, dict):
            containers.extend(_mapping_containers(nested))
    return containers


def _view_from_containers(
    containers: list[dict[str, Any]],
    *,
    exception_type: str = "",
) -> FailureView:
    retryable: bool | None = None
    retryable_declared = False
    category = ""
    code = ""
    phase = "run"
    status_code: int | None = None
    attempt_count: int | None = None
    retry_after_ms: int | None = None
    cancelled = False
    budget_exhausted = False
    resolved_exception_type = exception_type

    for container in containers:
        declared = container.get("retryable")
        if retryable is None and isinstance(declared, bool):
            retryable = declared
            retryable_declared = True
        if not category:
            category = _safe_text(container.get("category") or container.get("type"))
        if not code:
            code = _safe_text(container.get("code") or container.get("stop_reason"))
        if phase == "run" and container.get("phase"):
            phase = _safe_text(container.get("phase")).casefold() or "run"
        if status_code is None:
            status_code = _safe_int(
                container.get("status_code", container.get("provider_status"))
            )
        if attempt_count is None:
            attempt_count = _safe_int(container.get("attempt_count"))
        if retry_after_ms is None:
            retry_after_ms = _safe_int(container.get("retry_after_ms"))
        cancelled = cancelled or container.get("cancelled") is True
        budget_exhausted = budget_exhausted or container.get(BUDGET_EXHAUSTED) is True
        if not resolved_exception_type:
            resolved_exception_type = _safe_text(container.get("exception_type"))

    normalized_category = category.casefold()
    normalized_code = code.casefold()
    normalized_exception = resolved_exception_type.casefold()
    cancelled = cancelled or normalized_category in {"cancelled", "cancellation"}
    cancelled = cancelled or "cancelled" in normalized_category
    cancelled = cancelled or "cancelled" in normalized_code
    cancelled = cancelled or "cancelled" in normalized_exception
    budget_exhausted = budget_exhausted or normalized_category == BUDGET_EXHAUSTED
    budget_exhausted = budget_exhausted or normalized_code == BUDGET_EXHAUSTED
    return FailureView(
        retryable=retryable,
        retryable_declared=retryable_declared,
        category=category,
        code=code,
        status_code=status_code,
        phase=phase,
        attempt_count=attempt_count,
        retry_after_ms=retry_after_ms,
        cancelled=cancelled,
        budget_exhausted=budget_exhausted,
        exception_type=resolved_exception_type,
    )


def view_from_exception(error: BaseException) -> FailureView:
    retryable = getattr(error, "retryable", None)
    declared = getattr(error, "retryable_declared", None)
    retryable_declared = declared is True and isinstance(retryable, bool)
    category = _safe_text(getattr(error, "category", ""))
    code = _safe_text(getattr(error, "code", ""))
    exception_type = type(error).__name__
    cancelled = bool(getattr(error, "cancelled", False)) or "cancelled" in exception_type.casefold()
    cancelled = cancelled or "cancelled" in category.casefold() or "cancelled" in code.casefold()
    budget_exhausted = bool(getattr(error, BUDGET_EXHAUSTED, False))
    budget_exhausted = budget_exhausted or category.casefold() == BUDGET_EXHAUSTED
    budget_exhausted = budget_exhausted or code.casefold() == BUDGET_EXHAUSTED
    return FailureView(
        retryable=retryable if retryable_declared else None,
        retryable_declared=retryable_declared,
        category=category,
        code=code,
        status_code=_safe_int(
            getattr(error, "status_code", getattr(error, "provider_status", None))
        ),
        phase=_safe_text(getattr(error, "phase", "run")).casefold() or "run",
        attempt_count=_safe_int(getattr(error, "attempt_count", None)),
        retry_after_ms=_safe_int(getattr(error, "retry_after_ms", None)),
        cancelled=cancelled,
        budget_exhausted=budget_exhausted,
        exception_type=exception_type,
    )


def view_from_event(event: Any) -> FailureView:
    containers: list[dict[str, Any]] = []
    error = getattr(event, "error", None)
    metadata = getattr(event, "metadata", None)
    containers.extend(_mapping_containers(error))
    containers.extend(_mapping_containers(metadata))
    return _view_from_containers(containers)


def attempt_budget(request: dict[str, Any] | None) -> int:
    raw = (request or {}).get("_auto_retry_max_attempts", MAX_ATTEMPTS)
    if isinstance(raw, bool):
        return MAX_ATTEMPTS
    value = _safe_int(raw)
    if value is None:
        return MAX_ATTEMPTS
    return max(1, min(MAX_ATTEMPTS, value))


def should_retry(view: FailureView, *, cancelled: bool = False) -> bool:
    """Retry every failed attempt except explicit cancellation or exhausted L1 budget."""

    return not (cancelled or view.cancelled or view.budget_exhausted)


def must_commit_now(view: FailureView, *, cancelled: bool = False) -> bool:
    return not should_retry(view, cancelled=cancelled)


def backoff_seconds(failed_attempt: int, view: FailureView) -> float:
    if view.retry_after_ms is not None and view.retry_after_ms >= 0:
        return min(120.0, max(0.25, view.retry_after_ms / 1000.0))
    return min(2.0, 0.25 * (2 ** max(0, failed_attempt - 1)))


def retry_reason(view: FailureView) -> str:
    raw = view.code or view.category or view.exception_type or "run_error"
    safe = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in raw
    )
    return safe[:80] or "run_error"


def retrying_event(
    event: RunEvent,
    *,
    run_id: str,
    failed_attempt: int,
    next_attempt: int,
    max_attempts: int,
) -> RunEvent:
    view = view_from_event(event)
    return RunEvent(
        type="retrying",
        content=(
            "运行出现问题，正在自动重试"
            f"（第 {next_attempt}/{max_attempts} 次尝试；已连续失败 {failed_attempt} 次）"
        ),
        metadata={
            "run_id": run_id,
            "failed_attempt": failed_attempt,
            "next_attempt": next_attempt,
            "max_attempts": max_attempts,
            "max_retries": max(0, max_attempts - 1),
            "consecutive_failures": failed_attempt,
            "exception_type": (view.exception_type or "RuntimeError")[:80],
            "reason": retry_reason(view),
        },
    )


def mark_retry_exhausted(
    error: BaseException,
    *,
    attempts: int,
    max_attempts: int,
) -> None:
    setattr(error, "retry_exhausted", True)
    setattr(error, BUDGET_EXHAUSTED, True)
    setattr(error, "retry_attempts", attempts)
    setattr(error, "retry_max_attempts", max_attempts)
    setattr(error, "retryable_declared", True)
    setattr(error, "retryable", False)
