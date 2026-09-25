from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.manifest import parse_plugin_manifest
from plugins.task_time.tool import run
from run.infra import LogStore
from run.scheduler import CronStore


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

    def test_weekly_range_count_and_execution_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            created = run(
                "create", title="weekly", prompt="检查状态", type="weekly",
                weekdays=[1, 5], times=["09:00", "20:00"],
                start_date="2026-10-01", end_date="2026-12-31", max_runs=3,
                context=context,
            )["task"]
            self.assertEqual(created["weekdays"], [1, 5])
            self.assertEqual(created["times"], ["09:00", "20:00"])
            self.assertEqual(created["max_runs"], 3)
            LogStore(root).append_cron({
                "executed_at": "2026-10-02T09:00:00+08:00", "user": "alice",
                "task_id": created["task_id"], "status": "failed", "duration_ms": 10,
                "result": {}, "error": {"type": "RuntimeError", "message": "boom"},
            })
            with patch.object(LogStore, "list_cron", side_effect=AssertionError("history 不应扫描用户全部记录")):
                history = run("history", task_id=created["task_id"], history_limit=5, context=context)
            self.assertEqual(history["runs"][0]["status"], "failed")
            self.assertEqual(history["runs"][0]["error"]["message"], "boom")

            updated = run(
                "update",
                task_id=created["task_id"],
                time="12:30",
                start_date="",
                end_date="",
                clear_max_runs=True,
                context=context,
            )["task"]
            self.assertEqual(updated["time"], "12:30")
            self.assertNotIn("times", updated)
            self.assertNotIn("start_date", updated)
            self.assertNotIn("end_date", updated)
            self.assertNotIn("max_runs", updated)

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
        self.assertEqual(manifest.tool["version"], "2.2.0")
        self.assertEqual(
            set(schema["properties"]["action"]["enum"]),
            {"list", "get", "history", "create", "update", "delete"},
        )
        self.assertEqual(schema["properties"]["interval_seconds"]["minimum"], 60)
        self.assertIn("weekly", schema["properties"]["type"]["enum"])
        self.assertIn("monthly", schema["properties"]["type"]["enum"])
        self.assertIn("history", schema["properties"]["action"]["enum"])
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
