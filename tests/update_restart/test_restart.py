from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import restart


class RestartHelperTests(unittest.TestCase):
    def test_parent_is_stopped_before_same_port_instance_starts(self) -> None:
        calls: list[tuple[str, int]] = []
        process = SimpleNamespace(pid=9876)
        with (
            patch("restart.time.sleep"),
            patch("restart._terminate_process", side_effect=lambda pid: calls.append(("terminate", pid))),
            patch("restart._wait_for_port", side_effect=lambda port, **kwargs: calls.append(("wait_port", port))),
            patch("restart._start_web", side_effect=lambda port: calls.append(("start", port)) or process),
            patch("restart._wait_for_started_process", side_effect=lambda process, port: calls.append(("wait_started", port))),
            patch("restart._write_log"),
        ):
            result = restart.main(["--port=1360", "--parent-pid=1234"])

        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [("terminate", 1234), ("wait_port", 1360), ("start", 1360), ("wait_started", 1360)],
        )

    def test_free_port_mode_does_not_terminate_calling_shell(self) -> None:
        process = SimpleNamespace(pid=9876)
        with (
            patch("restart._terminate_process") as terminate,
            patch("restart._wait_for_port"),
            patch("restart._start_web", return_value=process),
            patch("restart._wait_for_started_process"),
            patch("restart._write_log"),
        ):
            result = restart.main(["--port=1360"])
        self.assertEqual(result, 0)
        terminate.assert_not_called()

    def test_unix_timeout_escalates_before_starting(self) -> None:
        calls: list[str] = []
        process = SimpleNamespace(pid=9876)
        with (
            patch("restart.sys.platform", "linux"),
            patch("restart.time.sleep"),
            patch("restart._terminate_process", side_effect=lambda pid: calls.append("terminate")),
            patch("restart._wait_for_port", side_effect=[TimeoutError("busy"), None]),
            patch("restart._force_terminate_process", side_effect=lambda pid: calls.append("force")),
            patch("restart._start_web", side_effect=lambda port: calls.append("start") or process),
            patch("restart._wait_for_started_process"),
            patch("restart._write_log"),
        ):
            result = restart.main(["--port=1360", "--parent-pid=1234"])

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["terminate", "force", "start"])

    def test_force_termination_is_platform_specific(self) -> None:
        with (
            patch("restart.sys.platform", "linux"),
            patch("restart.os.kill") as kill,
        ):
            restart._force_terminate_process(1234)
        kill.assert_called_once_with(1234, getattr(restart.signal, "SIGKILL", 9))

        with (
            patch("restart.sys.platform", "win32"),
            patch("restart._terminate_process") as terminate,
        ):
            restart._force_terminate_process(1234)
        terminate.assert_called_once_with(1234)

    def test_start_web_uses_platform_specific_detach_options(self) -> None:
        process = SimpleNamespace(pid=9876)
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "restart.log"
            with (
                patch("restart.LOG_PATH", log_path),
                patch("restart.sys.platform", "linux"),
                patch("restart.subprocess.Popen", return_value=process) as popen,
            ):
                restart._start_web(1360)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertNotIn("creationflags", popen.call_args.kwargs)

            with (
                patch("restart.LOG_PATH", log_path),
                patch("restart.sys.platform", "win32"),
                patch("restart.subprocess.Popen", return_value=process) as popen,
            ):
                restart._start_web(1360)
            self.assertIn("creationflags", popen.call_args.kwargs)
            self.assertNotIn("start_new_session", popen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
