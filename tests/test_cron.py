from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from provider.schema import ChatResponse, Usage
from run.cron_store import (
    CronConflictError,
    CronError,
    CronNotFoundError,
    CronStore,
    CronValidationError,
    normalize_task,
)
from cron.schedule import compute_next_run, is_due, _parse_utc
from cron.executor import execute_cron_task
from cron.scheduler import CronScheduler, recover_all


CONFIG = {
    "provider": {
        "type": "kemo",
        "base_url": "http://127.0.0.1:1/v1",
        "api_key_env": "TEST_CRON_KEY",
        "model": "mock",
        "stream": False,
    },
    "tools": {"enabled": True, "timeout": 5},
    "cron": {"enabled": True, "poll_interval": 1},
}


class MockProvider:
    def __init__(self, response_text: str = "OK") -> None:
        self.response_text = response_text

    def chat(self, request):
        return ChatResponse(
            text=self.response_text,
            usage=Usage(1, 1, 2, source="mock"),
            model=request.model,
        )

    def chat_stream(self, request):
        raise AssertionError("stream not expected")


def _make_root(users: list[str]) -> tuple[tempfile.TemporaryDirectory, Path]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    (root / "config").mkdir()
    (root / "config" / "global_config.json").write_text(
        json.dumps(CONFIG), "utf-8"
    )
    (root / "config" / "global_soul.md").write_text("SOUL", "utf-8")
    (root / "agents.md").write_text("AGENTS", "utf-8")
    for user in users:
        (root / "users" / user / "task_cron").mkdir(parents=True)
        (root / "users" / user / "user_config.json").write_text(
            json.dumps({"schema_version": 1, "provider": CONFIG["provider"]}),
            "utf-8",
        )
    return temporary, root


class CronStoreTests(unittest.TestCase):
    def test_create_read_list_update_delete(self) -> None:
        _, root = _make_root(["alice"])
        store = CronStore(root, "alice")
        task = normalize_task(
            title="Daily Brief",
            prompt="Summarize today",
            user="alice",
            schedule={"type": "daily", "time": "09:00", "timezone": "Asia/Shanghai"},
            next_run_at="2026-07-18T01:00:00Z",
        )
        created = store.create(task)
        self.assertEqual(created["status"], "enabled")
        self.assertEqual(created["revision"], 1)

        read = store.read(created["task_id"])
        self.assertEqual(read["task_id"], created["task_id"])

        tasks = store.list_tasks()
        self.assertEqual(len(tasks), 1)

        updated = store.update(created["task_id"], lambda t: {**t, "status": "paused"})
        self.assertEqual(updated["status"], "paused")
        self.assertEqual(updated["revision"], 2)

        self.assertTrue(store.delete(created["task_id"]))
        with self.assertRaises(CronNotFoundError):
            store.read(created["task_id"])

    def test_duplicate_task_id_conflict(self) -> None:
        _, root = _make_root(["alice"])
        store = CronStore(root, "alice")
        task = normalize_task(
            title="Test", prompt="go", user="alice",
            schedule={"type": "recurring", "interval_seconds": 120},
        )
        store.create(task)
        with self.assertRaises(CronConflictError):
            store.create(task)

    def test_bad_time_rejected(self) -> None:
        with self.assertRaises(CronValidationError):
            normalize_task(
                title="Bad", prompt="x", user="alice",
                schedule={"type": "daily", "time": "25:00", "timezone": "UTC"},
            )
        with self.assertRaises(CronValidationError):
            normalize_task(
                title="Bad", prompt="x", user="alice",
                schedule={"type": "daily", "time": "09:60", "timezone": "UTC"},
            )

    def test_low_interval_rejected(self) -> None:
        with self.assertRaises(CronValidationError):
            normalize_task(
                title="Bad", prompt="x", user="alice",
                schedule={"type": "recurring", "interval_seconds": 30},
            )

    def test_missing_timezone_rejected(self) -> None:
        with self.assertRaises(CronValidationError):
            normalize_task(
                title="Bad", prompt="x", user="alice",
                schedule={"type": "daily", "time": "09:00"},
            )

    def test_once_missing_start_at(self) -> None:
        with self.assertRaises(CronValidationError):
            normalize_task(
                title="Bad", prompt="x", user="alice",
                schedule={"type": "once"},
            )

    def test_multi_user_isolation(self) -> None:
        _, root = _make_root(["alice", "bob"])
        alice = CronStore(root, "alice")
        bob = CronStore(root, "bob")
        alice.create(normalize_task(
            title="A", prompt="a", user="alice",
            schedule={"type": "recurring", "interval_seconds": 60},
        ))
        bob.create(normalize_task(
            title="B", prompt="b", user="bob",
            schedule={"type": "recurring", "interval_seconds": 60},
        ))
        self.assertEqual(len(alice.list_tasks()), 1)
        self.assertEqual(len(bob.list_tasks()), 1)

    def test_recover_interrupted(self) -> None:
        _, root = _make_root(["alice"])
        store = CronStore(root, "alice")
        task = store.create(normalize_task(
            title="A", prompt="go", user="alice",
            schedule={"type": "recurring", "interval_seconds": 60},
        ))
        store.update(task["task_id"], lambda t: {**t, "status": "running"})
        recovered = store.recover_interrupted()
        self.assertEqual(len(recovered), 1)
        task = store.read(task["task_id"])
        self.assertEqual(task["status"], "enabled")

    def test_corrupt_file_skipped(self) -> None:
        _, root = _make_root(["alice"])
        store = CronStore(root, "alice")
        task = store.create(normalize_task(
            title="A", prompt="go", user="alice",
            schedule={"type": "recurring", "interval_seconds": 60},
        ))
        (store._dir / f"{task['task_id']}.json").write_text("bad", "utf-8")
        self.assertEqual(len(store.list_tasks()), 0)


class ScheduleTests(unittest.TestCase):
    def test_once(self) -> None:
        result = compute_next_run({"type": "once", "start_at": "2026-07-18T01:00:00Z"})
        self.assertIn("2026-07-18T01:00:00", result)

    def test_daily_shanghai(self) -> None:
        now = datetime(2026, 7, 17, 10, 0, 0, tzinfo=timezone.utc)  # 18:00 Shanghai
        result = compute_next_run(
            {"type": "daily", "time": "09:00", "timezone": "Asia/Shanghai"},
            after=now,
        )
        # 09:00 Shanghai next day = 01:00 UTC next day
        self.assertIn("2026-07-18T01:00:00", result)

    def test_daily_already_passed_today(self) -> None:
        now = datetime(2026, 7, 17, 1, 30, 0, tzinfo=timezone.utc)  # 09:30 Shanghai
        result = compute_next_run(
            {"type": "daily", "time": "09:00", "timezone": "Asia/Shanghai"},
            after=now,
        )
        self.assertIn("2026-07-18T01:00:00", result)

    def test_recurring(self) -> None:
        now = datetime(2026, 7, 17, 10, 0, 0, tzinfo=timezone.utc)
        result = compute_next_run(
            {"type": "recurring", "interval_seconds": 300}, after=now
        )
        self.assertIn("2026-07-17T10:05:00", result)

    def test_is_due_past(self) -> None:
        now = datetime(2026, 7, 17, 10, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(is_due("2026-07-17T09:00:00Z", now=now))

    def test_is_due_future(self) -> None:
        now = datetime(2026, 7, 17, 10, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(is_due("2026-07-17T11:00:00Z", now=now))

    def test_is_due_empty(self) -> None:
        self.assertFalse(is_due("", now=datetime.now(timezone.utc)))


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.root = _make_root(["alice"])
        self.addCleanup(self._tmp.cleanup)

    def _create_task(self, schedule: dict, **kwargs) -> dict:
        store = CronStore(self.root, "alice")
        task = normalize_task(
            title=kwargs.get("title", "Test"),
            prompt=kwargs.get("prompt", "hello"),
            user="alice",
            schedule=schedule,
            next_run_at=kwargs.get("next_run_at", "2000-01-01T00:00:00Z"),
        )
        return store.create(task)

    def test_execute_recurring_success(self) -> None:
        task = self._create_task(
            {"type": "recurring", "interval_seconds": 60},
        )
        with patch.dict(os.environ, {"TEST_CRON_KEY": "x"}, clear=False):
            result = execute_cron_task(
                root=self.root, user="alice", task_id=task["task_id"],
                config=CONFIG,
                provider_factory=lambda _: MockProvider("done"),
            )
        self.assertEqual(result["status"], "enabled")
        self.assertEqual(result["run_count"], 1)
        self.assertIsNotNone(result["last_result"])
        self.assertIsNone(result["last_error"])
        # next_run_at should be updated
        self.assertNotEqual(result["next_run_at"], "2000-01-01T00:00:00Z")

    def test_execute_once_completes(self) -> None:
        task = self._create_task(
            {"type": "once", "start_at": "2026-07-18T01:00:00Z"},
        )
        with patch.dict(os.environ, {"TEST_CRON_KEY": "x"}, clear=False):
            result = execute_cron_task(
                root=self.root, user="alice", task_id=task["task_id"],
                config=CONFIG,
                provider_factory=lambda _: MockProvider("done"),
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["run_count"], 1)
        self.assertEqual(result["next_run_at"], "")

    def test_execute_failure_marks_failed(self) -> None:
        task = self._create_task(
            {"type": "recurring", "interval_seconds": 60},
        )
        from run.engine import EngineError

        def fail_provider(cfg):
            class P:
                def chat(self, req):
                    raise EngineError("boom")

                def chat_stream(self, req):
                    raise AssertionError()
            return P()

        with patch.dict(os.environ, {"TEST_CRON_KEY": "x"}, clear=False):
            result = execute_cron_task(
                root=self.root, user="alice", task_id=task["task_id"],
                config=CONFIG, provider_factory=fail_provider,
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["run_count"], 1)
        self.assertIsNotNone(result["last_error"])

    def test_cancelled_before_execution(self) -> None:
        task = self._create_task(
            {"type": "recurring", "interval_seconds": 60},
        )
        cancel = threading.Event()
        cancel.set()
        with patch.dict(os.environ, {"TEST_CRON_KEY": "x"}, clear=False):
            result = execute_cron_task(
                root=self.root, user="alice", task_id=task["task_id"],
                config=CONFIG, cancel_event=cancel,
                provider_factory=lambda _: MockProvider("done"),
            )
        self.assertEqual(result["status"], "enabled")

    def test_cannot_claim_paused_task(self) -> None:
        task = self._create_task(
            {"type": "recurring", "interval_seconds": 60},
        )
        store = CronStore(self.root, "alice")
        store.update(task["task_id"], lambda t: {**t, "status": "paused"})
        with patch.dict(os.environ, {"TEST_CRON_KEY": "x"}, clear=False):
            with self.assertRaises(CronError):
                execute_cron_task(
                    root=self.root, user="alice", task_id=task["task_id"],
                    config=CONFIG,
                    provider_factory=lambda _: MockProvider("done"),
                )


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.root = _make_root(["alice"])
        self.addCleanup(self._tmp.cleanup)

    def test_scan_once_executes_due_task(self) -> None:
        store = CronStore(self.root, "alice")
        task = store.create(normalize_task(
            title="Due Task", prompt="hello", user="alice",
            schedule={"type": "recurring", "interval_seconds": 60},
            next_run_at="2000-01-01T00:00:00Z",  # past = due
        ))
        executed = []

        def on_executed(user, task_id, result):
            executed.append((user, task_id))

        sched = CronScheduler(
            self.root,
            on_task_executed=on_executed,
            provider_factory=lambda _: MockProvider("done"),
        )
        with patch.dict(os.environ, {"TEST_CRON_KEY": "x"}, clear=False):
            count = sched.scan_once()

        self.assertGreaterEqual(count, 1)
        self.assertEqual(len(executed), 1)
        # Task should be enabled with updated next_run_at
        task = store.read(task["task_id"])
        self.assertEqual(task["status"], "enabled")
        self.assertNotEqual(task["next_run_at"], "2000-01-01T00:00:00Z")

    def test_scan_skips_non_due(self) -> None:
        store = CronStore(self.root, "alice")
        store.create(normalize_task(
            title="Future", prompt="hello", user="alice",
            schedule={"type": "recurring", "interval_seconds": 60},
            next_run_at="2099-01-01T00:00:00Z",
        ))
        sched = CronScheduler(self.root)
        with patch.dict(os.environ, {"TEST_CRON_KEY": "x"}, clear=False):
            count = sched.scan_once()
        self.assertEqual(count, 0)

    def test_scan_skips_paused(self) -> None:
        store = CronStore(self.root, "alice")
        task = store.create(normalize_task(
            title="Paused", prompt="hello", user="alice",
            schedule={"type": "recurring", "interval_seconds": 60},
            next_run_at="2000-01-01T00:00:00Z",
        ))
        store.update(task["task_id"], lambda t: {**t, "status": "paused"})
        sched = CronScheduler(self.root)
        with patch.dict(os.environ, {"TEST_CRON_KEY": "x"}, clear=False):
            count = sched.scan_once()
        self.assertEqual(count, 0)

    def test_start_stop_lifecycle(self) -> None:
        sched = CronScheduler(self.root, poll_interval=0.1)
        self.assertFalse(sched.running)
        sched.start()
        self.assertTrue(sched.running)
        sched.stop(timeout=5)
        self.assertFalse(sched.running)

    def test_recover_all(self) -> None:
        store = CronStore(self.root, "alice")
        task = store.create(normalize_task(
            title="Interrupted", prompt="hello", user="alice",
            schedule={"type": "recurring", "interval_seconds": 60},
        ))
        store.update(task["task_id"], lambda t: {**t, "status": "running"})
        recovered = recover_all(self.root)
        self.assertEqual(len(recovered), 1)
        task = store.read(task["task_id"])
        self.assertEqual(task["status"], "enabled")

    def test_scan_skips_completed_once(self) -> None:
        store = CronStore(self.root, "alice")
        store.create(normalize_task(
            title="Done", prompt="hello", user="alice",
            schedule={"type": "once", "start_at": "2000-01-01T00:00:00Z"},
            next_run_at="",
            status="completed",
        ))
        sched = CronScheduler(self.root)
        with patch.dict(os.environ, {"TEST_CRON_KEY": "x"}, clear=False):
            count = sched.scan_once()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
