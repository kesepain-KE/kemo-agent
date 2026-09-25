from __future__ import annotations

import unittest

from events import RunEvent
from provider.schema import ProviderError
from run.conversation.round_finalizer import _safe_failure_detail
from run.retry import (
    MAX_ATTEMPTS,
    MAX_RETRIES,
    attempt_budget,
    must_commit_now,
    retrying_event,
    should_retry,
    view_from_event,
    view_from_exception,
)


class RetryPolicyTests(unittest.TestCase):
    def test_budget_is_initial_attempt_plus_five_retries(self) -> None:
        self.assertEqual(MAX_RETRIES, 5)
        self.assertEqual(MAX_ATTEMPTS, 6)
        self.assertEqual(attempt_budget({}), 6)
        self.assertEqual(attempt_budget({"_auto_retry_max_attempts": 99}), 6)
        self.assertEqual(attempt_budget({"_auto_retry_max_attempts": 1}), 1)

    def test_retry_and_immediate_commit_are_strict_complements(self) -> None:
        cases = [
            RunEvent(
                type="error",
                error={
                    "category": "auth_error",
                    "status_code": 401,
                    "retryable": False,
                },
            ),
            RunEvent(type="error", error={"phase": "request", "retryable": False}),
            RunEvent(type="error", error={"cancelled": True}),
            RunEvent(type="error", error={"retry_budget_exhausted": True}),
        ]
        for event in cases:
            with self.subTest(error=event.error):
                view = view_from_event(event)
                self.assertNotEqual(should_retry(view), must_commit_now(view))

    def test_auth_and_deterministic_failures_retry_but_cancellation_does_not(self) -> None:
        auth = view_from_exception(
            ProviderError(
                "denied",
                category="auth_error",
                status_code=401,
                retryable=False,
            )
        )
        deterministic = view_from_event(
            RunEvent(
                type="error",
                error={"phase": "request", "category": "validation_error"},
            )
        )
        cancelled = view_from_event(
            RunEvent(type="error", error={"cancelled": True})
        )
        self.assertTrue(should_retry(auth))
        self.assertTrue(should_retry(deterministic))
        self.assertFalse(should_retry(cancelled))

    def test_lower_layer_budget_exhaustion_stops_outer_retry(self) -> None:
        view = view_from_exception(
            ProviderError(
                "chat transport exhausted",
                category="connection_error",
                retryable=False,
                retry_budget_exhausted=True,
            )
        )
        self.assertFalse(should_retry(view))
        self.assertTrue(must_commit_now(view))

    def test_exception_code_survives_safe_failure_projection(self) -> None:
        error = ProviderError("protocol failure", category="protocol_error")
        error.code = "INVALID_EVENT_SEQUENCE"
        detail = _safe_failure_detail(error)
        self.assertEqual(detail["code"], "INVALID_EVENT_SEQUENCE")

    def test_retrying_event_exposes_six_attempt_denominator(self) -> None:
        event = retrying_event(
            RunEvent(type="error", error={"exception_type": "ProviderError"}),
            run_id="run_retry",
            failed_attempt=1,
            next_attempt=2,
            max_attempts=6,
        )
        self.assertIn("第 2/6 次尝试", event.content)
        self.assertEqual(event.metadata["max_retries"], 5)
        self.assertEqual(event.metadata["consecutive_failures"], 1)


if __name__ == "__main__":
    unittest.main()
