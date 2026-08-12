from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins.manifest import parse_plugin_manifest
from plugins.task_plan.tool import run
from run.task_plan_store import PlanStore, normalize_plan
from run.tools import discover_tools, validate_arguments


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TaskPlanPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "users" / "alice").mkdir(parents=True)
        self.context = {
            "root": str(self.root),
            "user": "alice",
            "source": "test",
            "session_id": "session-a",
        }

    def create_plan(self, *, steps: int = 2, status: str = "pending") -> dict:
        plan = normalize_plan(
            title="测试计划",
            description="验证运行态插件",
            user="alice",
            source="test",
            session_id="session-a",
            status=status,
            steps=[
                {
                    "step_id": f"step_{index}",
                    "title": f"步骤 {index}",
                    "description": f"执行步骤 {index}",
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                }
                for index in range(1, steps + 1)
            ],
        )
        return PlanStore(self.root, "alice").create(plan)

    def test_manifest_is_discovered_with_eight_actions(self) -> None:
        manifest = parse_plugin_manifest(
            PROJECT_ROOT / "plugins" / "task_plan" / "SKILL.md",
            root=PROJECT_ROOT,
        )
        self.assertEqual(manifest.tool["name"], "task_plan")
        self.assertEqual(manifest.tool["version"], "1.1.0")
        actions = manifest.tool["input_schema"]["properties"]["action"]["enum"]
        self.assertEqual(
            set(actions),
            {"view", "list", "step_done", "step_fail", "abort", "approve", "pause", "resume"},
        )
        definition = discover_tools(PROJECT_ROOT, "kesepain").get("task_plan")
        validate_arguments(definition.input_schema, {"action": "list"})

    def test_list_view_and_active_plan_fallback(self) -> None:
        plan = self.create_plan()
        listed = run(action="list", context=self.context)
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["plans"][0]["plan_id"], plan["plan_id"])
        viewed = run(action="view", context=self.context)
        self.assertTrue(viewed["ok"])
        self.assertEqual(viewed["plan"]["plan_id"], plan["plan_id"])

    def test_active_plan_fallback_does_not_cross_conversation_spaces(self) -> None:
        plan_a = self.create_plan()
        PlanStore(self.root, "alice").update(
            plan_a["plan_id"],
            lambda plan: {
                **plan,
                "source": "web",
                "session_id": "conversation-a",
            },
        )
        context_b = {
            **self.context,
            "source": "web",
            "session_id": "conversation-b",
        }

        viewed = run(action="view", context=context_b)
        aborted = run(action="abort", context=context_b)
        explicit_view = run(
            action="view",
            plan_id=plan_a["plan_id"],
            context=context_b,
        )
        explicit_abort = run(
            action="abort",
            plan_id=plan_a["plan_id"],
            context=context_b,
        )

        self.assertFalse(viewed["ok"])
        self.assertIn("没有活跃计划", viewed["error"])
        self.assertFalse(aborted["ok"])
        self.assertIn("没有活跃计划", aborted["error"])
        self.assertFalse(explicit_view["ok"])
        self.assertIn("其他对话空间", explicit_view["error"])
        self.assertFalse(explicit_abort["ok"])
        self.assertIn("其他对话空间", explicit_abort["error"])
        self.assertEqual(
            PlanStore(self.root, "alice").read(plan_a["plan_id"])["status"],
            "pending",
        )

    def test_list_only_returns_current_conversation_space(self) -> None:
        plan_a = self.create_plan()
        store = PlanStore(self.root, "alice")
        store.update(
            plan_a["plan_id"],
            lambda plan: {
                **plan,
                "source": "web",
                "session_id": "conversation-a",
            },
        )
        plan_b = self.create_plan()
        store.update(
            plan_b["plan_id"],
            lambda plan: {
                **plan,
                "source": "web",
                "session_id": "conversation-b",
            },
        )

        listed = run(
            action="list",
            context={
                **self.context,
                "source": "web",
                "session_id": "conversation-a",
            },
        )

        self.assertTrue(listed["ok"])
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["plans"][0]["plan_id"], plan_a["plan_id"])

    def test_list_fails_closed_without_conversation_identity(self) -> None:
        result = run(
            action="list",
            context={"root": str(self.root), "user": "alice"},
        )
        self.assertFalse(result["ok"])
        self.assertIn("source", result["error"])

    def test_step_done_persists_result_is_idempotent_and_auto_completes(self) -> None:
        plan = self.create_plan(status="approved")
        first = run(
            action="step_done",
            plan_id=plan["plan_id"],
            step_id="step_1",
            result="第一步已完成",
            context=self.context,
        )
        self.assertTrue(first["ok"])
        self.assertEqual(first["plan"]["status"], "running")
        self.assertEqual(first["plan"]["steps"][0]["result"], "第一步已完成")
        self.assertEqual(first["completed_step"]["step_id"], "step_1")
        self.assertEqual(first["progress"], {"completed": 1, "total": 2, "remaining": 1})
        self.assertEqual(first["next_step"]["step_id"], "step_2")
        self.assertEqual(
            [step["step_id"] for step in first["remaining_steps"]],
            ["step_2"],
        )
        self.assertEqual(first["plan_status"], "running")
        revision = first["plan"]["revision"]
        repeated = run(
            action="step_done",
            plan_id=plan["plan_id"],
            step_id="step_1",
            result="网络重试不应覆盖",
            context=self.context,
        )
        self.assertEqual(repeated["plan"]["revision"], revision)
        self.assertEqual(repeated["plan"]["steps"][0]["result"], "第一步已完成")

        completed = run(
            action="step_done",
            plan_id=plan["plan_id"],
            step_id="step_2",
            result="第二步已完成",
            context=self.context,
        )
        self.assertTrue(completed["ok"])
        self.assertEqual(completed["plan"]["status"], "completed")
        self.assertEqual(completed["progress"], {"completed": 2, "total": 2, "remaining": 0})
        self.assertIsNone(completed["next_step"])
        self.assertEqual(completed["remaining_steps"], [])
        self.assertEqual(completed["plan_status"], "completed")
        self.assertTrue(
            all(step["status"] == "completed" for step in completed["plan"]["steps"])
        )

    def test_agent_managed_inflight_step_can_finish_after_pause_without_resuming(self) -> None:
        plan = self.create_plan(status="running")
        PlanStore(self.root, "alice").update(
            plan["plan_id"], lambda current: {**current, "status": "paused"}
        )

        rejected = run(
            action="step_done",
            plan_id=plan["plan_id"],
            step_id="step_1",
            result="普通调用不应修改暂停计划",
            context=self.context,
        )
        self.assertFalse(rejected["ok"])

        completed = run(
            action="step_done",
            plan_id=plan["plan_id"],
            step_id="step_1",
            result="当前步骤在暂停后收尾完成",
            context={
                **self.context,
                "task_plan_mode": "agent_managed",
                "task_plan_id": plan["plan_id"],
            },
        )
        self.assertTrue(completed["ok"])
        self.assertEqual(completed["plan"]["status"], "paused")
        self.assertEqual(completed["completed_step"]["status"], "completed")
        self.assertIsNone(completed["next_step"])
        self.assertEqual(completed["progress"]["remaining"], 1)

    def test_executor_managed_mode_rejects_agent_step_state_writes(self) -> None:
        plan = self.create_plan(status="running")
        result = run(
            action="step_done",
            plan_id=plan["plan_id"],
            step_id="step_1",
            context={**self.context, "task_plan_mode": "executor_managed"},
        )
        self.assertFalse(result["ok"])
        self.assertIn("框架执行器维护状态", result["error"])
        self.assertEqual(
            PlanStore(self.root, "alice").read(plan["plan_id"])["steps"][0]["status"],
            "pending",
        )

    def test_step_fail_stores_schema_error_and_pauses(self) -> None:
        plan = self.create_plan(status="running")
        failed = run(
            action="step_fail",
            plan_id=plan["plan_id"],
            step_id="step_1",
            error="外部服务不可用",
            context=self.context,
        )
        self.assertTrue(failed["ok"])
        self.assertEqual(failed["plan"]["status"], "paused")
        step = failed["plan"]["steps"][0]
        self.assertEqual(step["status"], "failed")
        self.assertEqual(step["error"]["message"], "外部服务不可用")
        self.assertEqual(step["error"]["exception_type"], "ManualStepFailure")

    def test_executor_state_actions_and_abort_fallback(self) -> None:
        plan = self.create_plan()
        approved = run(action="approve", plan_id=plan["plan_id"], context=self.context)
        self.assertEqual(approved["plan"]["status"], "approved")
        paused = run(action="pause", plan_id=plan["plan_id"], context=self.context)
        self.assertEqual(paused["plan"]["status"], "paused")
        resumed = run(action="resume", plan_id=plan["plan_id"], context=self.context)
        self.assertEqual(resumed["plan"]["status"], "approved")
        aborted = run(action="abort", context=self.context)
        self.assertEqual(aborted["plan"]["status"], "cancelled")
        self.assertTrue(
            all(step["status"] == "cancelled" for step in aborted["plan"]["steps"])
        )

    def test_invalid_inputs_are_safe_and_do_not_mutate(self) -> None:
        plan = self.create_plan(status="running")
        revision = plan["revision"]
        missing_step = run(
            action="step_done",
            plan_id=plan["plan_id"],
            step_id="step_99",
            context=self.context,
        )
        self.assertFalse(missing_step["ok"])
        self.assertIn("步骤不存在", missing_step["error"])
        self.assertEqual(PlanStore(self.root, "alice").read(plan["plan_id"])["revision"], revision)
        self.assertFalse(
            run(
                action="view",
                plan_id="../../config/global_config",
                context=self.context,
            )["ok"]
        )
        self.assertFalse(run(action="unknown", context=self.context)["ok"])
        self.assertFalse(run(action="approve", context=self.context)["ok"])
        with self.assertRaisesRegex(ValueError, "root 或 user"):
            run(action="list", context={})
        with self.assertRaisesRegex(ValueError, "非法用户名称"):
            run(
                action="list",
                context={"root": str(self.root), "user": "../escape"},
            )

    def test_management_tool_cannot_be_a_plan_step(self) -> None:
        with self.assertRaisesRegex(Exception, "管理工具"):
            normalize_plan(
                title="递归计划",
                description="不允许",
                user="alice",
                steps=[
                    {
                        "step_id": "step_1",
                        "title": "管理",
                        "description": "错误使用管理工具",
                        "tool_name": "task_plan",
                        "tool_arguments": {"action": "list"},
                        "critical": True,
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
