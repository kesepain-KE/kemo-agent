from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.manifest import parse_plugin_manifest
from plugins.task_time.tool import run
from run.cron_store import CronStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TaskTimePluginOptimizationTests(unittest.TestCase):
    @staticmethod
    def context(root: Path, user: str = "alice") -> dict[str, str]:
        return {"root": str(root), "user": user, "source": "test"}

    @staticmethod
    def create_task(
        root: Path,
        title: str,
        *,
        status: str = "enabled",
    ) -> dict:
        context = TaskTimePluginOptimizationTests.context(root)
        result = run(
            "create",
            title=title,
            prompt=f"执行任务：{title}",
            type="recurring",
            interval_seconds=300,
            context=context,
        )
        if status != "enabled":
            return run(
                "update",
                task_id=result["task"]["task_id"],
                status=status,
                context=context,
            )["task"]
        return result["task"]

    def test_list_query_filters_title_case_insensitively_and_recounts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_task(root, "Daily Backup")
            self.create_task(root, "DATABASE BACKUP", status="paused")
            self.create_task(root, "Weekly report")
            context = self.context(root)

            filtered = run("list", query="  backup  ", context=context)
            self.assertEqual(filtered["total"], 2)
            self.assertEqual(filtered["active"], 1)
            self.assertEqual(
                {task["title"] for task in filtered["tasks"]},
                {"Daily Backup", "DATABASE BACKUP"},
            )
            all_tasks = run("list", query="   ", context=context)
            self.assertEqual(all_tasks["total"], 3)
            self.assertEqual(all_tasks["active"], 2)
            self.assertEqual(run("list", query="不存在", context=context)["tasks"], [])

    def test_get_reads_one_task_without_using_list_and_reports_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = self.create_task(root, "Direct lookup")
            context = self.context(root)
            with patch.object(CronStore, "list_tasks", side_effect=AssertionError("get 不应遍历任务")):
                fetched = run("get", task_id=task["task_id"], context=context)
            self.assertTrue(fetched["ok"])
            self.assertEqual(fetched["task"], task)

            missing = run("get", task_id="cron_deadbeef", context=context)
            self.assertEqual(
                missing,
                {"ok": False, "error": "任务不存在: cron_deadbeef"},
            )
            with self.assertRaisesRegex(ValueError, "get 需要 task_id"):
                run("get", context=context)

    def test_existing_create_update_delete_behavior_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            created = run(
                "create",
                title="heartbeat",
                prompt="独立执行一次健康检查并汇报结果。",
                type="daily",
                time="09:30",
                context=context,
            )
            self.assertTrue(created["ok"])
            task_id = created["task"]["task_id"]
            updated = run("update", task_id=task_id, title="heartbeat v2", context=context)
            self.assertEqual(updated["task"]["time"], "09:30")
            self.assertEqual(updated["task"]["type"], "daily")
            self.assertTrue(run("delete", task_id=task_id, context=context)["deleted"])
            self.assertFalse(run("get", task_id=task_id, context=context)["ok"])

    def test_context_query_and_action_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "root 或 user"):
                run("list", context={})
            with self.assertRaisesRegex(ValueError, "非法用户名称"):
                run("list", context=self.context(root, "../escape"))
            with self.assertRaisesRegex(ValueError, "query 必须是字符串"):
                run("list", query=None, context=self.context(root))  # type: ignore[arg-type]
            with self.assertRaisesRegex(ValueError, "list / get"):
                run("unknown", context=self.context(root))

    def test_manifest_and_instruction_contract_are_updated(self) -> None:
        manifest = parse_plugin_manifest(
            PROJECT_ROOT / "plugins" / "task_time" / "SKILL.md",
            root=PROJECT_ROOT,
        )
        schema = manifest.tool["input_schema"]
        self.assertEqual(manifest.tool["version"], "2.1.0")
        self.assertEqual(
            set(schema["properties"]["action"]["enum"]),
            {"list", "get", "create", "update", "delete"},
        )
        self.assertEqual(schema["properties"]["interval_seconds"]["minimum"], 60)
        self.assertIn("query", schema["properties"])

        skill_text = (PROJECT_ROOT / "plugins" / "task_time" / "SKILL.md").read_text("utf-8")
        trigger_text = (PROJECT_ROOT / "agents" / "time_plan" / "trigger.md").read_text("utf-8")
        agents_text = (PROJECT_ROOT / "agents.md").read_text("utf-8")
        self.assertIn("自然语言定时需求必须先走 time_plan", skill_text)
        self.assertIn("主智能体硬性调用规则", trigger_text)
        self.assertIn("task_time get", trigger_text)
        self.assertIn("不得直接猜测时间参数", agents_text)


if __name__ == "__main__":
    unittest.main()
