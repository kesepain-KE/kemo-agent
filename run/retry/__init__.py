"""Unified attempt-level retry contracts for main runs and subagents.

The retry budget belongs to one interruption sequence inside one Run:
``MAX_RETRIES`` is five and ``MAX_ATTEMPTS`` is therefore six.  A later
interruption, or a new Run started by the user, receives a fresh budget.  This
package never persists a conversation-wide retry counter.

Transport retries keep their own request-level budgets.  They may stop an
outer retry only by setting ``retry_budget_exhausted``.  Attempt-local repair
loops (tool argument repair and context compression) also keep their own
budgets; when they fail, their terminal error flows into this package.
"""

from run.retry.loop import RetryLedger, RunAttemptContext, run_attempts
from run.retry.policy import (
    BUDGET_EXHAUSTED,
    MAX_ATTEMPTS,
    MAX_RETRIES,
    FailureView,
    attempt_budget,
    backoff_seconds,
    mark_retry_exhausted,
    must_commit_now,
    retry_reason,
    retrying_event,
    should_retry,
    view_from_event,
    view_from_exception,
)

__all__ = [
    "BUDGET_EXHAUSTED",
    "MAX_ATTEMPTS",
    "MAX_RETRIES",
    "FailureView",
    "RetryLedger",
    "RunAttemptContext",
    "attempt_budget",
    "backoff_seconds",
    "mark_retry_exhausted",
    "must_commit_now",
    "retry_reason",
    "retrying_event",
    "run_attempts",
    "should_retry",
    "view_from_event",
    "view_from_exception",
]
