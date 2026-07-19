from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from cron.executor import execute_cron_task
from cron.schedule import compute_next_run, is_due
from cron.scheduler import (
    CronScheduler,
    ensure_memory_maintenance_tasks,
    ensure_memory_promotion_task,
)
from cron.service import generate_cron_task
from run.cron_store import CronStore, CronValidationError, normalize_task


BEIJING = ZoneInfo("Asia/Shanghai")


class CronStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CronStore(self.root, "alice")

    def _task(self, **overrides):
        values = {
            "title": "heartbeat",
            "prompt": "ping",
            "user": "alice",
            "type": "recurring",
            "interval_seconds": 60,
            "next_run_at": "2026-07-20T10:00:00+08:00",
        }
        values.update(overrides)
        return normalize_task(**values)

    def test_flat_schema_create_read_update(self) -> None:
        created = self.store.create(self._task())
        self.assertEqual(
            set(created),
            {
                "task_id", "title", "prompt", "user", "type",
                "interval_seconds", "next_run_at", "latest_run_at",
                "status", "created_at", "exec_mode", "system_key",
            },
        )
        self.assertTrue(created["created_at"].endswith("+08:00"))
        self.assertEqual(created["exec_mode"], "agent")
        self.assertEqual(created["system_key"], "")
        updated = self.store.update(
            created["task_id"],
            lambda task: {**task, "title": "renamed"},
        )
        self.assertEqual(updated["title"], "renamed")
        self.assertEqual(self.store.read(created["task_id"]), updated)

    def test_type_specific_fields_are_enforced(self) -> None:
        with self.assertRaises(CronValidationError):
            self._task(interval_seconds=0)
        with self.assertRaises(CronValidationError):
            normalize_task(
                title="daily", prompt="x", user="alice", type="daily",
                time="25:00", next_run_at="2026-07-20T10:00:00+08:00",
            )
        with self.assertRaises(CronValidationError):
            normalize_task(
                title="once", prompt="x", user="alice", type="once",
                next_run_at="not-a-time",
            )
        with self.assertRaises(CronValidationError):
            self._task(exec_mode="worker")

    def test_old_schema_is_migrated_and_persisted(self) -> None:
        directory = self.root / "users" / "alice" / "task_cron"
        directory.mkdir(parents=True)
        path = directory / "cron_1234abcd.json"
        path.write_text(
            json.dumps({
                "schema_version": 1,
                "task_id": "cron_1234abcd",
                "title": "legacy",
                "prompt": "run",
                "user": "alice",
                "source": "cli",
                "session_id": "old",
                "exec_mode": "agent",
                "system_key": "",
                "schedule": {"type": "daily", "time": "09:00", "timezone": "Asia/Shanghai"},
                "status": "enabled",
                "next_run_at": "2026-07-20T01:00:00+00:00",
                "last_run_at": "2026-07-19T01:00:00+00:00",
                "last_result": None,
                "last_error": None,
                "run_count": 2,
                "revision": 3,
                "created_at": "2026-07-18T01:00:00+00:00",
                "updated_at": "2026-07-19T01:00:00+00:00",
            }, ensure_ascii=False),
            "utf-8",
        )
        migrated = self.store.read("cron_1234abcd")
        self.assertEqual(migrated["type"], "daily")
        self.assertEqual(migrated["time"], "09:00")
        self.assertEqual(migrated["next_run_at"], "2026-07-20T09:00:00+08:00")
        self.assertEqual(migrated["latest_run_at"], "2026-07-19T09:00:00+08:00")
        self.assertEqual(migrated["exec_mode"], "agent")
        self.assertEqual(migrated["system_key"], "")
        self.assertNotIn("schedule", migrated)
        self.assertEqual(json.loads(path.read_text("utf-8")), migrated)

    def test_previous_flat_schema_gains_execution_defaults(self) -> None:
        task = self._task()
        task.pop("exec_mode")
        task.pop("system_key")
        directory = self.root / "users" / "alice" / "task_cron"
        directory.mkdir(parents=True)
        path = directory / f"{task['task_id']}.json"
        path.write_text(json.dumps(task, ensure_ascii=False), "utf-8")
        migrated = self.store.read(task["task_id"])
        self.assertEqual(migrated["exec_mode"], "agent")
        self.assertEqual(migrated["system_key"], "")

    def test_recover_running_uses_beijing_time(self) -> None:
        task = self.store.create(self._task(status="running"))
        self.assertEqual(self.store.recover_interrupted(), [task["task_id"]])
        recovered = self.store.read(task["task_id"])
        self.assertEqual(recovered["status"], "enabled")
        self.assertTrue(recovered["next_run_at"].endswith("+08:00"))


class ScheduleTests(unittest.TestCase):
    def test_recurring_and_daily_are_beijing(self) -> None:
        after = datetime(2026, 7, 20, 8, 0, tzinfo=BEIJING)
        self.assertEqual(
            compute_next_run({"type": "recurring", "interval_seconds": 30}, after=after),
            "2026-07-20T08:00:30+08:00",
        )
        self.assertEqual(
            compute_next_run({"type": "daily", "time": "09:00"}, after=after),
            "2026-07-20T09:00:00+08:00",
        )
        after_late = after.replace(hour=10)
        self.assertEqual(
            compute_next_run({"type": "daily", "time": "09:00"}, after=after_late),
            "2026-07-21T09:00:00+08:00",
        )

    def test_once_converts_old_utc_value(self) -> None:
        result = compute_next_run({"type": "once", "next_run_at": "2026-07-20T01:00:00Z"})
        self.assertEqual(result, "2026-07-20T09:00:00+08:00")

    def test_is_due_compares_aware_times(self) -> None:
        now = datetime(2026, 7, 20, 9, 0, tzinfo=BEIJING)
        self.assertTrue(is_due("2026-07-20T08:59:59+08:00", now=now))
        self.assertFalse(is_due("2026-07-20T09:00:01+08:00", now=now))
        self.assertFalse(is_due("", now=now))


class CronServiceTests(unittest.TestCase):
    def test_time_plan_flat_output_becomes_flat_once_task(self) -> None:
        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, name, input_data):
                self.name = name
                self.input_data = input_data
                return SimpleNamespace(data={
                    "action": "create",
                    "title": "once",
                    "prompt": "do it",
                    "type": "once",
                    "next_run_at": "2026-07-20T09:00:00+08:00",
                })

        with tempfile.TemporaryDirectory() as directory, patch("cron.service.AgentRunner", Runner):
            task = generate_cron_task(
                root=Path(directory),
                user="alice",
                user_request="明天执行",
                config={},
            )
        self.assertEqual(task["type"], "once")
        self.assertEqual(task["next_run_at"], "2026-07-20T09:00:00+08:00")
        self.assertEqual(task["exec_mode"], "agent")
        self.assertEqual(task["system_key"], "")
        self.assertNotIn("schedule", task)


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CronStore(self.root, "alice")

    def _create(self, task_type="recurring", **overrides):
        kwargs = {
            "title": "run",
            "prompt": "do work",
            "user": "alice",
            "type": task_type,
            "next_run_at": "2026-07-20T09:00:00+08:00",
        }
        if task_type == "recurring":
            kwargs["interval_seconds"] = 60
        kwargs.update(overrides)
        return self.store.create(normalize_task(**kwargs))

    def test_agent_mode_enters_handle_request(self) -> None:
        task = self._create()
        with patch("cron.executor.handle_request", return_value={"text": "ok"}) as handled:
            result = execute_cron_task(
                root=self.root, user="alice", task_id=task["task_id"], config={},
            )
        request = handled.call_args.args[0]
        self.assertEqual(request["source"], "cron")
        self.assertEqual(request["session_id"], "cron")
        self.assertEqual(request["prompt"], "do work")
        self.assertEqual(result["status"], "enabled")
        self.assertTrue(result["latest_run_at"].endswith("+08:00"))

    def test_once_completes_and_failure_has_no_result_fields(self) -> None:
        once = self._create("once")
        with patch("cron.executor.handle_request", return_value={"text": "ok"}):
            completed = execute_cron_task(
                root=self.root, user="alice", task_id=once["task_id"], config={},
            )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["next_run_at"], "")

        recurring = self._create()
        with patch("cron.executor.handle_request", side_effect=RuntimeError("boom")):
            failed = execute_cron_task(
                root=self.root, user="alice", task_id=recurring["task_id"], config={},
            )
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("last_error", failed)
        self.assertNotIn("last_result", failed)

    def test_cancel_before_run_reverts_claim(self) -> None:
        task = self._create()
        stopped = threading.Event()
        stopped.set()
        with patch("cron.executor.handle_request") as handled:
            result = execute_cron_task(
                root=self.root,
                user="alice",
                task_id=task["task_id"],
                config={},
                cancel_event=stopped,
            )
        handled.assert_not_called()
        self.assertEqual(result["status"], "enabled")

    def test_subagent_mode_bypasses_main_agent(self) -> None:
        task = self._create(
            exec_mode="subagent",
            system_key="memory_scan",
            prompt=json.dumps({
                "subagent": "memory_temporary_important",
                "input": {"trigger": "periodic_scan"},
            }),
        )

        runner = SimpleNamespace(
            run=MagicMock(return_value=SimpleNamespace(data={}))
        )
        with patch("cron.executor.AgentRunner", return_value=runner) as runner_type, patch(
            "cron.executor.handle_request"
        ) as handled:
            result = execute_cron_task(
                root=self.root, user="alice", task_id=task["task_id"], config={},
            )
        runner_type.assert_called_once()
        runner.run.assert_called_once_with(
            "memory_temporary_important",
            {"trigger": "periodic_scan"},
            cancel_event=None,
            task_id=task["task_id"],
        )
        handled.assert_not_called()
        self.assertEqual(result["status"], "enabled")

    def test_function_mode_uses_whitelisted_internal_function(self) -> None:
        task = self._create(
            exec_mode="function",
            system_key="memory_promotion",
            prompt=json.dumps({"function": "cron.review_due.scan_and_promote"}),
        )
        with patch(
            "cron.executor._execute_internal_function",
            return_value={"requested": 0},
        ) as called, patch("cron.executor.handle_request") as handled:
            result = execute_cron_task(
                root=self.root, user="alice", task_id=task["task_id"], config={},
            )
        called.assert_called_once()
        handled.assert_not_called()
        self.assertEqual(result["status"], "enabled")


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {
            "agents": {
                "important_memory_review_hours": 3,
                "daily_memory_review_time": "03:15",
            }
        }

    def test_system_tasks_are_flat_idempotent_and_self_describing(self) -> None:
        first = ensure_memory_maintenance_tasks(self.root, "alice", self.config)
        promotion = ensure_memory_promotion_task(self.root, "alice", self.config)
        second = ensure_memory_maintenance_tasks(self.root, "alice", self.config)
        self.assertEqual(first, second)
        self.assertEqual(len(CronStore(self.root, "alice").list_tasks()), 3)
        periodic = next(item for item in first if item["type"] == "recurring")
        daily = next(item for item in first if item["type"] == "daily")
        self.assertEqual(periodic["interval_seconds"], 10800)
        self.assertEqual(daily["time"], "03:15")
        self.assertEqual(periodic["exec_mode"], "subagent")
        self.assertEqual(json.loads(periodic["prompt"])["subagent"], "memory_temporary_important")
        self.assertEqual(promotion["interval_seconds"], 30)
        self.assertEqual(promotion["exec_mode"], "function")
        self.assertEqual(
            json.loads(promotion["prompt"])["function"],
            "cron.review_due.scan_and_promote",
        )
        self.assertTrue(all(task["system_key"] for task in [*first, promotion]))

    def test_previous_title_based_system_task_is_adopted_without_duplicate(self) -> None:
        store = CronStore(self.root, "alice")
        previous = normalize_task(
            title="临时重要记忆定时巡检",
            prompt="old main-agent prompt",
            user="alice",
            type="recurring",
            interval_seconds=10800,
            next_run_at="2026-07-20T09:00:00+08:00",
        )
        previous.pop("exec_mode")
        previous.pop("system_key")
        directory = self.root / "users" / "alice" / "task_cron"
        directory.mkdir(parents=True)
        (directory / f"{previous['task_id']}.json").write_text(
            json.dumps(previous, ensure_ascii=False),
            "utf-8",
        )

        ensured = ensure_memory_maintenance_tasks(self.root, "alice", self.config)
        periodic = next(task for task in ensured if task["type"] == "recurring")
        self.assertEqual(periodic["task_id"], previous["task_id"])
        self.assertEqual(periodic["exec_mode"], "subagent")
        self.assertTrue(periodic["system_key"])
        self.assertEqual(len(store.list_tasks()), 2)

    def test_scan_executes_due_task(self) -> None:
        store = CronStore(self.root, "alice")
        task = store.create(normalize_task(
            title="due", prompt="run", user="alice", type="recurring",
            interval_seconds=60,
            next_run_at=(datetime.now(BEIJING) - timedelta(seconds=1)).isoformat(),
        ))
        with patch("cron.scheduler.execute_cron_task", return_value=task) as execute:
            count = CronScheduler(self.root).scan_once()
        self.assertEqual(count, 1)
        execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
