from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from run.task_plan_store import PlanStore, normalize_plan


class StoragePollingTests(unittest.TestCase):
    def test_task_plan_polling_uses_query_only_single_row_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice" / "task_plan").mkdir(parents=True)
            store = PlanStore(root, "alice")
            plan = store.create(
                normalize_plan(
                    title="低磁盘占用计划",
                    description="验证调度器领取查询",
                    user="alice",
                    steps=[
                        {
                            "step_id": "step_1",
                            "title": "执行",
                            "description": "执行计划",
                            "tool_name": None,
                            "tool_arguments": {},
                            "critical": True,
                        }
                    ],
                )
            )

            self.assertIsNone(store.first_approved_plan_id())
            with store._connection() as database:
                self.assertEqual(database.execute("PRAGMA query_only").fetchone()[0], 1)

            store.update(plan["plan_id"], lambda value: {**value, "status": "approved"})
            self.assertEqual(store.first_approved_plan_id(), plan["plan_id"])


if __name__ == "__main__":
    unittest.main()
