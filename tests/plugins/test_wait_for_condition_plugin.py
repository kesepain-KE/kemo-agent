from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from plugins.wait_for_condition.tool import run
from run.infra.process_identity import process_snapshot
from run.tools.background_jobs import (
    _identity_alive,
    prepare_background_job,
    update_background_job,
)
from run.tools import discover_tools, execute_tool, resolve_tool_timeout


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class WaitForConditionPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "users" / "alice").mkdir(parents=True)

    def context(self, cancel_event: threading.Event | None = None) -> dict:
        return {
            "root": str(self.root),
            "user": "alice",
            "source": "test",
            "session_id": "space-a",
            "cancel_event": cancel_event or threading.Event(),
        }

    def test_duration_completes_as_triggered(self) -> None:
        result = run(
            "duration",
            1,
            check_interval=0.1,
            context=self.context(),
        )
        self.assertEqual(result["status"], "triggered")
        self.assertEqual(result["trigger"], "duration_elapsed")
        self.assertGreaterEqual(result["elapsed_seconds"], 0.9)

    def test_condition_timeout_is_a_normal_result(self) -> None:
        result = run(
            "path_exists",
            1,
            check_interval=0.1,
            path="never-created.txt",
            context=self.context(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["triggered"])

    def test_path_conditions_trigger_early(self) -> None:
        target = self.root / "result.txt"

        def create() -> None:
            time.sleep(0.15)
            target.write_text("ready", "utf-8")

        worker = threading.Thread(target=create)
        worker.start()
        try:
            created = run(
                "path_exists",
                2,
                check_interval=0.1,
                path="result.txt",
                context=self.context(),
            )
        finally:
            worker.join()
        self.assertEqual(created["status"], "triggered")
        self.assertLess(created["elapsed_seconds"], 1.5)
        self.assertEqual(created["observation"]["requested_path"], "result.txt")
        self.assertEqual(created["observation"]["resolved_path"], str(target))
        self.assertTrue(created["observation"]["path_was_relative"])
        self.assertEqual(created["observation"]["path_base"], str(self.root))

        missing = run(
            "path_missing",
            2,
            check_interval=0.1,
            path="missing.txt",
            context=self.context(),
        )
        self.assertEqual(missing["status"], "triggered")

    def test_path_changed_observes_size_or_mtime_change(self) -> None:
        target = self.root / "progress.txt"
        target.write_text("a", "utf-8")

        def update() -> None:
            time.sleep(0.15)
            target.write_text("changed", "utf-8")

        worker = threading.Thread(target=update)
        worker.start()
        try:
            result = run(
                "path_changed",
                2,
                check_interval=0.1,
                path=str(target),
                context=self.context(),
            )
        finally:
            worker.join()
        self.assertEqual(result["status"], "triggered")
        self.assertIn("initial_snapshot", result["observation"])

    def test_process_exit_waits_for_real_child_process(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            identity = process_snapshot(process.pid)
            result = run(
                "process_exit",
                3,
                check_interval=0.1,
                pid=process.pid,
                process_started_at=str(identity.get("process_started_at") or ""),
                context=self.context(),
            )
        finally:
            process.wait(timeout=3)
        self.assertEqual(result["status"], "triggered")
        self.assertEqual(result["trigger"], "process_exit")
        self.assertFalse(result["observation"]["process_exists"])
        self.assertTrue(result["observation"]["initial_exists"])
        self.assertTrue(result["observation"]["ever_observed_alive"])

    def test_process_exit_distinguishes_absent_and_replaced_targets(self) -> None:
        absent = run(
            "process_exit",
            1,
            check_interval=0.1,
            pid=2_147_483_647,
            context=self.context(),
        )
        self.assertEqual(absent["trigger"], "process_already_absent")
        self.assertFalse(absent["observation"]["initial_exists"])
        self.assertFalse(absent["observation"]["ever_observed_alive"])

        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            replaced = run(
                "process_exit",
                2,
                check_interval=0.1,
                pid=process.pid,
                process_started_at="definitely-not-this-process",
                context=self.context(),
            )
        finally:
            process.terminate()
            process.wait(timeout=3)
        self.assertEqual(replaced["trigger"], "process_replaced")
        self.assertFalse(replaced["observation"]["identity_match"])
        self.assertFalse(replaced["observation"]["ever_observed_alive"])

    def test_background_job_identity_uses_process_fields_for_live_process(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            snapshot = process_snapshot(process.pid)
            record = {
                "pid": process.pid,
                "process_started_at": snapshot.get("process_started_at"),
                "process_name": snapshot.get("process_name"),
                "started_at": "job-registration-time",
            }
            alive, observation = _identity_alive(record)
        finally:
            process.terminate()
            process.wait(timeout=3)
        self.assertTrue(alive)
        self.assertTrue(observation["identity_match"])

    def test_process_identity_access_denied_is_not_reported_as_match(self) -> None:
        denied = {
            "pid": 12345,
            "exists": True,
            "query_status": "access_denied",
            "identity_available": False,
        }
        with patch(
            "plugins.wait_for_condition.tool.process_snapshot",
            return_value=denied,
        ):
            result = run(
                "process_exit",
                1,
                check_interval=0.1,
                pid=12345,
                process_started_at="expected-start",
                context=self.context(),
            )
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["observation"]["identity_match"])
        self.assertEqual(result["observation"]["query_status"], "access_denied")

    def test_job_exit_returns_terminal_job_and_enforces_conversation_scope(self) -> None:
        record, _ = prepare_background_job(
            self.root,
            "alice",
            source="test",
            session_id="space-a",
            working_dir=str(self.root),
            shell_type="auto",
            command_digest="digest",
        )

        def complete(current: dict) -> dict:
            current["status"] = "completed"
            current["exit_code"] = 0
            return current

        update_background_job(self.root, "alice", record["job_id"], complete)
        result = run(
            "job_exit",
            1,
            check_interval=0.1,
            job_id=record["job_id"],
            context=self.context(),
        )
        self.assertEqual(result["status"], "triggered")
        self.assertEqual(result["trigger"], "job_exit")
        self.assertEqual(result["observation"]["status"], "completed")

        foreign_context = self.context()
        foreign_context["session_id"] = "space-b"
        with self.assertRaisesRegex(KeyError, "后台作业不存在"):
            run(
                "job_exit",
                1,
                job_id=record["job_id"],
                context=foreign_context,
            )

        missing_scope = self.context()
        missing_scope.pop("session_id")
        with self.assertRaisesRegex(KeyError, "后台作业不存在"):
            run(
                "job_exit",
                1,
                job_id=record["job_id"],
                context=missing_scope,
            )

    def test_tcp_open_and_closed(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        host, port = server.getsockname()
        try:
            opened = run(
                "tcp_open",
                2,
                check_interval=0.1,
                host=host,
                port=port,
                context=self.context(),
            )
        finally:
            server.close()
        self.assertEqual(opened["status"], "triggered")

        closed = run(
            "tcp_closed",
            2,
            check_interval=0.1,
            host=host,
            port=port,
            context=self.context(),
        )
        self.assertEqual(closed["status"], "triggered")

    def test_cancel_event_interrupts_wait(self) -> None:
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            result = run(
                "path_exists",
                5,
                check_interval=5,
                path="never-created.txt",
                context=self.context(cancel),
            )
        finally:
            timer.cancel()
        self.assertEqual(result["status"], "cancelled")
        self.assertLess(time.monotonic() - started, 0.8)

    def test_manifest_timeout_range_and_watchdog_grace(self) -> None:
        definition = discover_tools(PROJECT_ROOT, "alice").get("wait_for_condition")
        schema = definition.input_schema
        self.assertEqual(schema["required"], ["condition", "timeout"])
        self.assertEqual(schema["properties"]["timeout"]["maximum"], 7200)
        self.assertIn("job_exit", schema["properties"]["condition"]["enum"])
        self.assertIn("process_started_at", schema["properties"])
        self.assertEqual(definition.version, "1.1.0")
        self.assertEqual(definition.timeout_grace_seconds, 2)
        self.assertEqual(
            resolve_tool_timeout(
                definition,
                {"condition": "duration", "timeout": 7200},
                default_timeout=240,
            ),
            7202,
        )

    def test_execute_tool_finishes_duration_before_outer_watchdog(self) -> None:
        definition = discover_tools(PROJECT_ROOT, "alice").get("wait_for_condition")
        result = execute_tool(
            definition,
            {"condition": "duration", "timeout": 1, "check_interval": 0.1},
            context={"root": str(self.root), "user": "alice"},
            timeout=0.05,
        )
        self.assertEqual(result["status"], "triggered")

    def test_condition_specific_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "pid"):
            run("process_exit", 1, context=self.context())
        with self.assertRaisesRegex(ValueError, "job_id"):
            run("job_exit", 1, context=self.context())
        with self.assertRaisesRegex(ValueError, "path"):
            run("path_exists", 1, context=self.context())
        with self.assertRaisesRegex(ValueError, "host"):
            run("tcp_open", 1, port=80, context=self.context())
        with self.assertRaisesRegex(ValueError, "port"):
            run("tcp_open", 1, host="127.0.0.1", context=self.context())
        with self.assertRaisesRegex(ValueError, "无法解析 host"):
            run(
                "tcp_closed",
                1,
                host="invalid host name",
                port=80,
                context=self.context(),
            )
        with self.assertRaisesRegex(ValueError, "1..7200"):
            run("duration", 7201, context=self.context())


if __name__ == "__main__":
    unittest.main()
