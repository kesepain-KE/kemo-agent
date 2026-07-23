from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from run.subagent_invocation import (
    SubagentInvocationError,
    prepare_main_agent_invocation,
)


class MainAgentSubagentInvocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def prepare(self, agent: str, payload: dict):
        return prepare_main_agent_invocation(
            root=self.root,
            user="alice",
            agent=agent,
            input_data=payload,
            config={},
        )

    def test_time_plan_overwrites_forged_runtime_time(self) -> None:
        prepared = self.prepare(
            "time_plan",
            {
                "action": "create",
                "user_request": "十分钟后提醒我",
                "current_time_beijing": "2000-01-01T00:00:00+08:00",
            },
        )
        value = datetime.fromisoformat(prepared.payload["current_time_beijing"])
        self.assertEqual(value.utcoffset(), timedelta(hours=8))
        self.assertGreater(value.year, 2000)
        self.assertFalse(prepared.synchronous_only)

    def test_time_plan_delete_drops_caller_runtime_time(self) -> None:
        prepared = self.prepare(
            "time_plan",
            {
                "action": "delete",
                "existing_task": {"task_id": "cron_1"},
                "current_time_beijing": "forged",
            },
        )
        self.assertNotIn("current_time_beijing", prepared.payload)

    def test_self_improve_main_agent_only_gets_manual_mode(self) -> None:
        prepared = self.prepare(
            "self_improve",
            {"trigger": "manual_review", "request": "整理我的长期偏好"},
        )
        self.assertEqual(prepared.payload["trigger"], "manual_review")
        for trigger in ("context_compression", "memory_promotion"):
            with self.subTest(trigger=trigger):
                with self.assertRaisesRegex(SubagentInvocationError, "仅供引擎或调度器"):
                    self.prepare("self_improve", {"trigger": trigger})

    def test_important_memory_accepts_only_its_public_manual_modes(self) -> None:
        for trigger in ("periodic_scan", "daily_consolidate"):
            prepared = self.prepare(
                "memory_temporary_important",
                {"trigger": trigger},
            )
            self.assertEqual(prepared.payload["trigger"], trigger)
        with self.assertRaisesRegex(SubagentInvocationError, "trigger"):
            self.prepare("memory_temporary_important", {"trigger": "internal"})

    def test_user_agent_payload_remains_explicit_and_unmodified(self) -> None:
        payload = {"custom": {"value": 1}}
        prepared = self.prepare("user_defined_agent", payload)
        self.assertEqual(prepared.payload, payload)
        self.assertIsNot(prepared.payload, payload)


if __name__ == "__main__":
    unittest.main()
