from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from plugins.wait_for_condition.tool import run
from run.tools import discover_tools, execute_tool, resolve_tool_timeout


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class WaitForConditionPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def context(self, cancel_event: threading.Event | None = None) -> dict:
        return {
            "root": str(self.root),
            "user": "alice",
            "source": "test",
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
            result = run(
                "process_exit",
                3,
                check_interval=0.1,
                pid=process.pid,
                context=self.context(),
            )
        finally:
            process.wait(timeout=3)
        self.assertEqual(result["status"], "triggered")
        self.assertFalse(result["observation"]["process_exists"])

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
