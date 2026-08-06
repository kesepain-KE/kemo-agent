from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from cron.executor import execute_cron_task
from cron.schedule import compute_next_run, is_due
from cron.scheduler import (
    CronScheduler,
    _append_system_execution,
    _system_result_summary,
    cleanup_old_system_tasks,
    ensure_expand_task,
    ensure_memory_maintenance_tasks,
    ensure_memory_promotion_task,
    ensure_perception_task,
)
from cron.service import generate_cron_task
from run.cron_store import CronStore, CronValidationError, normalize_task
from run.log_store import LogStore


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
                "status", "created_at", "exec_mode",
            },
        )
        self.assertTrue(created["created_at"].endswith("+08:00"))
        self.assertEqual(created["exec_mode"], "agent")
        self.assertNotIn("system_key", created)
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
        self.assertNotIn("system_key", migrated)
        self.assertNotIn("schedule", migrated)
        self.assertEqual(json.loads(path.read_text("utf-8")), migrated)

    def test_previous_flat_schema_gains_execution_defaults(self) -> None:
        task = self._task()
        task.pop("exec_mode")
        directory = self.root / "users" / "alice" / "task_cron"
        directory.mkdir(parents=True)
        path = directory / f"{task['task_id']}.json"
        path.write_text(json.dumps(task, ensure_ascii=False), "utf-8")
        migrated = self.store.read(task["task_id"])
        self.assertEqual(migrated["exec_mode"], "agent")
        self.assertNotIn("system_key", migrated)

    def test_system_store_accepts_empty_user_and_prompt(self) -> None:
        store = CronStore(self.root, "__system__", system=True)
        task = normalize_task(
            task_id="memory_promotion",
            title="promotion",
            prompt="",
            user="",
            type="recurring",
            interval_seconds=30,
            next_run_at="2026-07-20T10:00:00+08:00",
            exec_mode="system",
            action="memory_promotion",
        )
        stored = store.create(task)
        self.assertEqual(stored["action"], "memory_promotion")
        self.assertTrue((self.root / "cron" / "task_cron_system" / "memory_promotion.json").is_file())

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
        self.assertNotIn("system_key", task)
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
        transport_registry = object()
        with patch("cron.executor.handle_request", return_value={"text": "ok"}) as handled:
            result = execute_cron_task(
                root=self.root, user="alice", task_id=task["task_id"], config={},
                transport_registry=transport_registry,
            )
        request = handled.call_args.args[0]
        self.assertEqual(request["source"], f"background:cron:{task['task_id']}")
        self.assertTrue(request["session_id"].startswith("conv_"))
        self.assertEqual(request["prompt"], "do work")
        self.assertIs(request["_transport_registry"], transport_registry)
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

    def test_system_memory_review_exposes_persisted_update_metadata(self) -> None:
        update = {
            "featured": ["important.md"],
            "reconciled": [
                {
                    "action": "drop_duplicate",
                    "filename": "duplicate.md",
                    "permanent_filename": "canonical.md",
                }
            ],
        }
        runner = SimpleNamespace(
            run=MagicMock(
                return_value=SimpleNamespace(
                    data={"content": "private model output"},
                    metadata={"important_memory_update": update},
                )
            )
        )
        with patch("cron.executor.AgentRunner", return_value=runner):
            result = execute_cron_task(
                root=self.root,
                user="alice",
                task_id="memory_periodic_scan",
                config={},
                system_task={
                    "task_id": "memory_periodic_scan",
                    "exec_mode": "system",
                    "action": "periodic_scan",
                },
            )

        self.assertEqual(result["memory_update"], update)
        runner.run.assert_called_once_with(
            "memory_temporary_important",
            {"trigger": "periodic_scan"},
            cancel_event=None,
            task_id="memory_periodic_scan",
        )

    def test_function_mode_uses_whitelisted_internal_function(self) -> None:
        task = self._create(
            exec_mode="function",
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

    def test_system_module_updates_support_update_and_main(self) -> None:
        modules = (
            ("global_sense", "sense.json", "update_entry", "update"),
            ("global_sense", "sense.json", "main_entry", "main"),
            ("global_expand", "expand.json", "expand_entry", "main"),
        )
        for parent, manifest, name, function_name in modules:
            directory = self.root / parent / name
            directory.mkdir(parents=True)
            (directory / manifest).write_text(
                json.dumps({"start_update": "data_update.py"}),
                "utf-8",
            )
            (directory / "data_update.py").write_text(
                "from pathlib import Path\n"
                f"def {function_name}():\n"
                "    Path(__file__).with_name('updated.txt').write_text('ok', 'utf-8')\n",
                "utf-8",
            )

        sense = execute_cron_task(
            root=self.root,
            user="__system__",
            task_id="perception_update",
            config={},
            system_task={
                "task_id": "perception_update",
                "exec_mode": "system",
                "action": "perception_update",
            },
        )
        expand = execute_cron_task(
            root=self.root,
            user="__system__",
            task_id="expand_update",
            config={},
            system_task={
                "task_id": "expand_update",
                "exec_mode": "system",
                "action": "expand_update",
            },
        )

        self.assertEqual(sense["status"], "completed")
        self.assertEqual(set(sense["updated"]), {"main_entry", "update_entry"})
        self.assertEqual(expand["updated"], ["global/expand_entry"])
        for parent, _, name, _ in modules:
            self.assertEqual(
                (self.root / parent / name / "updated.txt").read_text("utf-8"),
                "ok",
            )

    def test_system_module_update_reports_unsafe_and_invalid_entries(self) -> None:
        sense = self.root / "global_sense"
        outside = self.root / "outside.py"
        outside.write_text("def update():\n    raise AssertionError('must not run')\n", "utf-8")

        escape = sense / "escape"
        escape.mkdir(parents=True)
        (escape / "sense.json").write_text(
            json.dumps({"start_update": "../../outside.py"}),
            "utf-8",
        )
        invalid = sense / "invalid_json"
        invalid.mkdir()
        (invalid / "sense.json").write_text("{", "utf-8")
        missing_callable = sense / "missing_callable"
        missing_callable.mkdir()
        (missing_callable / "sense.json").write_text(
            json.dumps({"start_update": "data_update.py"}),
            "utf-8",
        )
        (missing_callable / "data_update.py").write_text("VALUE = 1\n", "utf-8")

        result = execute_cron_task(
            root=self.root,
            user="__system__",
            task_id="perception_update",
            config={},
            system_task={
                "task_id": "perception_update",
                "exec_mode": "system",
                "action": "perception_update",
            },
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            set(result["failed"]),
            {"escape", "invalid_json", "missing_callable"},
        )
        reasons = {item["module"]: item["reason"] for item in result["errors"]}
        self.assertIn("相对路径", reasons["escape"])
        self.assertIn("有效的 sense.json", reasons["invalid_json"])
        self.assertIn("update() 或 main()", reasons["missing_callable"])

    def test_expand_updates_are_scoped_to_system_roots_or_one_user(self) -> None:
        modules = (
            (self.root / "global_expand" / "global_weather", "global"),
            (self.root / "shared_expand" / "shared_weather", "shared"),
            (self.root / "users" / "alice" / "expand" / "alice_weather", "alice"),
            (self.root / "users" / "bob" / "expand" / "bob_weather", "bob"),
        )
        for directory, marker in modules:
            directory.mkdir(parents=True)
            (directory / "expand.json").write_text(
                json.dumps({
                    "start_update": "data_update.py",
                    "input_health": "异常",
                }),
                "utf-8",
            )
            (directory / "data_update.py").write_text(
                "from pathlib import Path\n"
                "def update():\n"
                f"    Path(__file__).with_name('updated.txt').write_text('{marker}', 'utf-8')\n"
                "    return {'ok': True, 'resources': [{"
                "'path': 'updated.txt', 'kind': 'document', 'label': '采集结果'}]}\n",
                "utf-8",
            )

        task = {
            "task_id": "expand_update",
            "exec_mode": "system",
            "action": "expand_update",
        }
        system_result = execute_cron_task(
            root=self.root,
            user="__system__",
            task_id="expand_update",
            config={},
            system_task=task,
        )
        self.assertEqual(
            system_result["updated"],
            ["global/global_weather", "shared/shared_weather"],
        )
        self.assertTrue((modules[0][0] / "updated.txt").is_file())
        self.assertTrue((modules[1][0] / "updated.txt").is_file())
        global_runtime = json.loads((modules[0][0] / "_runtime.json").read_text("utf-8"))
        self.assertEqual(global_runtime["update"]["status"], "completed")
        self.assertEqual(global_runtime["update"]["resources"][0]["path"], "updated.txt")
        self.assertFalse((modules[2][0] / "updated.txt").exists())
        self.assertFalse((modules[3][0] / "updated.txt").exists())

        alice_result = execute_cron_task(
            root=self.root,
            user="alice",
            task_id="expand_update",
            config={},
            system_task=task,
        )
        self.assertEqual(alice_result["updated"], ["alice_weather"])
        self.assertEqual(alice_result["user"], "alice")
        self.assertTrue((modules[2][0] / "updated.txt").is_file())
        self.assertFalse((modules[3][0] / "updated.txt").exists())

    def test_module_failure_result_is_not_reported_as_success(self) -> None:
        module = self.root / "users" / "alice" / "expand" / "broken"
        module.mkdir(parents=True)
        manifest_path = module / "expand.json"
        manifest_path.write_text(
            json.dumps({
                "start_update": "data_update.py",
                "input_health": "正常",
                "recent_update": "2026-07-20 08:00:00",
            }),
            "utf-8",
        )
        (module / "data_update.py").write_text(
            "def update():\n"
            "    return {'ok': False, 'error': 'sensor offline'}\n",
            "utf-8",
        )

        result = execute_cron_task(
            root=self.root,
            user="alice",
            task_id="expand_update",
            config={},
            system_task={
                "task_id": "expand_update",
                "exec_mode": "system",
                "action": "expand_update",
            },
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed"], ["broken"])
        self.assertIn("sensor offline", result["errors"][0]["reason"])
        manifest = json.loads(manifest_path.read_text("utf-8"))
        self.assertEqual(manifest["input_health"], "异常")
        self.assertEqual(manifest["recent_update"], "2026-07-20 08:00:00")

    def test_module_update_timeout_marks_module_failed(self) -> None:
        module = self.root / "users" / "alice" / "expand" / "slow"
        module.mkdir(parents=True)
        manifest_path = module / "expand.json"
        manifest_path.write_text(
            json.dumps({
                "start_update": "data_update.py",
                "input_health": "正常",
            }),
            "utf-8",
        )
        (module / "data_update.py").write_text(
            "import time\n"
            "def update():\n"
            "    time.sleep(1)\n",
            "utf-8",
        )

        result = execute_cron_task(
            root=self.root,
            user="alice",
            task_id="expand_update",
            config={"task_cron_system": {"module_update_timeout": 0.05}},
            system_task={
                "task_id": "expand_update",
                "exec_mode": "system",
                "action": "expand_update",
            },
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"][0]["exception_type"], "TimeoutExpired")
        self.assertIn("0.05 秒", result["errors"][0]["reason"])
        self.assertEqual(
            json.loads(manifest_path.read_text("utf-8"))["input_health"],
            "异常",
        )

    def test_user_expand_rejects_linked_user_directory(self) -> None:
        user_root = self.root / "users" / "alice"
        user_root.mkdir(parents=True)
        with patch(
            "cron.executor._is_link_or_junction",
            side_effect=lambda path: path == user_root,
        ):
            result = execute_cron_task(
                root=self.root,
                user="alice",
                task_id="expand_update",
                config={},
                system_task={
                    "task_id": "expand_update",
                    "exec_mode": "system",
                    "action": "expand_update",
                },
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed"], ["alice"])
        self.assertIn("符号链接或目录联接", result["errors"][0]["reason"])


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {
            "agents": {
                "important_memory_review_hours": 3,
                "daily_memory_review_time": "03:15",
            },
            "task_cron_system": {
                "sense_update_rate": 5,
                "expand_update_rate": 7,
            },
        }

    def test_system_tasks_are_flat_idempotent_and_self_describing(self) -> None:
        first = ensure_memory_maintenance_tasks(self.root, self.config)
        promotion = ensure_memory_promotion_task(self.root)
        perception = ensure_perception_task(self.root, self.config)
        expand = ensure_expand_task(self.root, self.config)
        second = ensure_memory_maintenance_tasks(self.root, self.config)
        self.assertEqual(perception, ensure_perception_task(self.root, self.config))
        self.assertEqual(expand, ensure_expand_task(self.root, self.config))
        self.assertEqual(first, second)
        system_store = CronStore(self.root, "__system__", system=True)
        self.assertEqual(len(system_store.list_tasks()), 5)
        self.assertEqual(CronStore(self.root, "alice").list_tasks(), [])
        periodic = next(item for item in first if item["type"] == "recurring")
        daily = next(item for item in first if item["type"] == "daily")
        self.assertEqual(periodic["interval_seconds"], 10800)
        self.assertEqual(daily["time"], "03:15")
        self.assertEqual(periodic["exec_mode"], "system")
        self.assertEqual(periodic["action"], "periodic_scan")
        self.assertEqual(periodic["prompt"], "")
        self.assertEqual(periodic["user"], "")
        self.assertEqual(promotion["interval_seconds"], 30)
        self.assertEqual(promotion["exec_mode"], "system")
        self.assertEqual(promotion["action"], "memory_promotion")
        self.assertEqual(perception["interval_seconds"], 5)
        self.assertEqual(perception["action"], "perception_update")
        self.assertEqual(expand["interval_seconds"], 7)
        self.assertEqual(expand["action"], "expand_update")
        self.assertTrue(
            all("system_key" not in task for task in [*first, promotion, perception, expand])
        )

    def test_system_summary_keeps_bounded_important_memory_update_diagnostics(self) -> None:
        summary = _system_result_summary(
            {
                "status": "completed",
                "action": "periodic_scan",
                "memory_update": {
                    "featured": ["a.md", "b.md"],
                    "reconciled": [
                        {
                            "action": "drop_duplicate",
                            "filename": "duplicate.md",
                            "permanent_filename": "canonical.md",
                            "content": "must not enter logs",
                        }
                    ],
                },
            }
        )

        self.assertEqual(summary["memory_update"]["featured"], ["a.md", "b.md"])
        self.assertEqual(
            summary["memory_update"]["reconciled"],
            [
                {
                    "action": "drop_duplicate",
                    "filename": "duplicate.md",
                    "permanent_filename": "canonical.md",
                }
            ],
        )
        self.assertNotIn("must not enter logs", json.dumps(summary))

    def test_system_execution_is_written_to_sqlite(self) -> None:
        executed_at = datetime(2026, 7, 27, 20, 30, tzinfo=BEIJING)

        _append_system_execution(
            self.root,
            user="alice",
            task_id="memory_periodic_scan",
            executed_at=executed_at,
            duration_ms=18,
            result={"status": "completed"},
        )

        store = LogStore(self.root)
        self.assertEqual(len(store.list_cron("alice")), 1)

    def test_global_update_rates_default_fallback_and_recalibrate(self) -> None:
        perception = ensure_perception_task(self.root, {})
        expand = ensure_expand_task(
            self.root,
            {"task_cron_system": {"expand_update_rate": False}},
        )
        self.assertEqual(perception["interval_seconds"], 5)
        self.assertEqual(expand["interval_seconds"], 5)

        updated = ensure_perception_task(
            self.root,
            {"task_cron_system": {"sense_update_rate": 12}},
        )
        self.assertEqual(updated["interval_seconds"], 12)
        self.assertGreater(
            datetime.fromisoformat(updated["next_run_at"]),
            datetime.now(BEIJING),
        )

    def test_old_user_system_tasks_are_cleaned_before_new_tasks(self) -> None:
        directory = self.root / "users" / "alice" / "task_cron"
        directory.mkdir(parents=True)
        path = directory / "cron_1234abcd.json"
        path.write_text(
            json.dumps({
                "task_id": "cron_1234abcd",
                "title": "临时重要记忆定时巡检",
                "prompt": "old",
                "user": "alice",
                "type": "recurring",
                "interval_seconds": 10800,
                "next_run_at": "2026-07-20T09:00:00+08:00",
                "latest_run_at": "",
                "status": "enabled",
                "created_at": "2026-07-20T08:00:00+08:00",
                "exec_mode": "subagent",
                "system_key": "memory_temporary_important.periodic_scan",
            }, ensure_ascii=False),
            "utf-8",
        )
        self.assertEqual(cleanup_old_system_tasks(self.root, "alice"), 1)
        self.assertFalse(path.exists())
        ensure_memory_maintenance_tasks(self.root, self.config)
        self.assertEqual(CronStore(self.root, "alice").list_tasks(), [])

    def test_scan_executes_due_task(self) -> None:
        store = CronStore(self.root, "alice")
        task = store.create(normalize_task(
            title="due", prompt="run", user="alice", type="recurring",
            interval_seconds=60,
            next_run_at=(datetime.now(BEIJING) - timedelta(seconds=1)).isoformat(),
        ))
        transport_registry = object()
        with patch("cron.scheduler.execute_cron_task", return_value=task) as execute:
            count = CronScheduler(
                self.root,
                transport_registry=transport_registry,
            ).scan_once()
        self.assertEqual(count, 1)
        execute.assert_called_once()
        self.assertIs(
            execute.call_args.kwargs["transport_registry"],
            transport_registry,
        )

    def test_scan_system_task_runs_for_every_user_then_advances_once(self) -> None:
        for user in ("alice", "bob"):
            (self.root / "users" / user).mkdir(parents=True)
        store = CronStore(self.root, "__system__", system=True)
        task = store.create(normalize_task(
            task_id="memory_promotion",
            title="promotion",
            prompt="",
            user="",
            type="recurring",
            interval_seconds=30,
            next_run_at=(datetime.now(BEIJING) - timedelta(seconds=1)).isoformat(),
            exec_mode="system",
            action="memory_promotion",
        ))
        with patch("cron.scheduler.execute_cron_task", return_value={"status": "completed"}) as execute:
            count = CronScheduler(self.root).scan_once()
        self.assertEqual(count, 2)
        self.assertEqual({call.kwargs["user"] for call in execute.call_args_list}, {"alice", "bob"})
        self.assertTrue(all(call.kwargs["system_task"]["task_id"] == task["task_id"] for call in execute.call_args_list))
        advanced = store.read("memory_promotion")
        self.assertTrue(advanced["latest_run_at"])
        self.assertGreater(datetime.fromisoformat(advanced["next_run_at"]), datetime.now(BEIJING))
        records = LogStore(self.root).list_cron("alice") + LogStore(self.root).list_cron("bob")
        self.assertEqual({record["user"] for record in records}, {"alice", "bob"})
        self.assertTrue(all(record["task_id"] == "memory_promotion" for record in records))
        self.assertTrue(all(record["status"] == "success" for record in records))

    def test_global_system_task_runs_once_and_keeps_diagnostics(self) -> None:
        for user in ("alice", "bob"):
            (self.root / "users" / user).mkdir(parents=True)
        store = CronStore(self.root, "__system__", system=True)
        task = store.create(normalize_task(
            task_id="perception_update",
            title="sense",
            prompt="",
            user="",
            type="recurring",
            interval_seconds=5,
            next_run_at=(datetime.now(BEIJING) - timedelta(seconds=1)).isoformat(),
            exec_mode="system",
            action="perception_update",
        ))
        result = {
            "status": "partial",
            "category": "sense",
            "updated": ["good"],
            "failed": ["bad"],
            "errors": [{"module": "bad", "reason": "boom"}],
        }
        with patch("cron.scheduler.execute_cron_task", return_value=result) as execute:
            count = CronScheduler(self.root).scan_once()

        self.assertEqual(count, 1)
        self.assertEqual(execute.call_args.kwargs["user"], "__system__")
        self.assertEqual(execute.call_args.kwargs["config"], {})
        self.assertEqual(execute.call_args.kwargs["system_task"]["task_id"], task["task_id"])
        record = LogStore(self.root).list_cron("__system__")[0]
        self.assertEqual(record["user"], "__system__")
        self.assertEqual(record["status"], "partial")
        self.assertEqual(record["result"]["category"], "sense")
        self.assertEqual(record["result"]["failed"], ["bad"])
        self.assertEqual(record["result"]["errors"][0]["reason"], "boom")

    def test_expand_system_task_runs_shared_roots_then_each_user(self) -> None:
        for user in ("alice", "bob"):
            (self.root / "users" / user).mkdir(parents=True)
        store = CronStore(self.root, "__system__", system=True)
        task = store.create(normalize_task(
            task_id="expand_update",
            title="expand",
            prompt="",
            user="",
            type="recurring",
            interval_seconds=5,
            next_run_at=(datetime.now(BEIJING) - timedelta(seconds=1)).isoformat(),
            exec_mode="system",
            action="expand_update",
        ))
        with patch(
            "cron.scheduler.execute_cron_task",
            return_value={"status": "completed", "category": "expand"},
        ) as execute:
            count = CronScheduler(self.root, config=self.config).scan_once()

        self.assertEqual(count, 3)
        calls = {call.kwargs["user"]: call.kwargs for call in execute.call_args_list}
        self.assertEqual(set(calls), {"__system__", "alice", "bob"})
        self.assertIs(calls["__system__"]["config"], self.config)
        self.assertNotIn("config", calls["alice"])
        self.assertNotIn("config", calls["bob"])
        self.assertTrue(
            all(call.kwargs["system_task"]["task_id"] == task["task_id"] for call in execute.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
