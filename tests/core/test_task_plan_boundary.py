from __future__ import annotations

import unittest

from run.task_plan_boundary import detect_task_plan_creation_boundary


def _payload(*, status: str, auto_accept: bool, action: str = "create") -> dict:
    return {
        "ok": True,
        "result": {
            "status": "completed",
            "agent": "task_plan",
            "data": {"action": action},
            "plan": {
                "plan_id": "plan_12345678",
                "status": status,
                "auto_accept": auto_accept,
            },
        },
    }


class TaskPlanBoundaryTests(unittest.TestCase):
    def detect(self, payload: dict):
        return detect_task_plan_creation_boundary(
            tool_name="subagent_dispatch",
            arguments={"action": "call", "agent": "task_plan"},
            result_payload=payload,
        )

    def test_pending_plan_requires_approval(self) -> None:
        boundary = self.detect(_payload(status="pending", auto_accept=False))
        self.assertIsNotNone(boundary)
        assert boundary is not None
        self.assertTrue(boundary.awaiting_user_approval)
        self.assertEqual(boundary.stop_reason, "task_plan_approval_required")

    def test_auto_approved_plan_still_stops_the_creation_run(self) -> None:
        boundary = self.detect(_payload(status="approved", auto_accept=True))
        self.assertIsNotNone(boundary)
        assert boundary is not None
        self.assertFalse(boundary.awaiting_user_approval)
        self.assertEqual(boundary.stop_reason, "task_plan_created")

    def test_legacy_mismatched_state_fails_closed(self) -> None:
        boundary = self.detect(_payload(status="pending", auto_accept=True))
        self.assertIsNotNone(boundary)
        assert boundary is not None
        self.assertTrue(boundary.awaiting_user_approval)

    def test_edit_failure_and_unpersisted_results_do_not_trigger(self) -> None:
        self.assertIsNone(self.detect(_payload(status="pending", auto_accept=False, action="edit")))
        self.assertIsNone(self.detect({"ok": False, "error": {"message": "failed"}}))
        self.assertIsNone(
            detect_task_plan_creation_boundary(
                tool_name="file",
                arguments={"action": "call", "agent": "task_plan"},
                result_payload=_payload(status="pending", auto_accept=False),
            )
        )


if __name__ == "__main__":
    unittest.main()
