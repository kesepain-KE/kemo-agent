from __future__ import annotations

import unittest
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
            patch("restart._wait_for_process_exit", side_effect=lambda pid: calls.append(("wait_process", pid))),
            patch("restart._wait_for_port", side_effect=lambda port: calls.append(("wait_port", port))),
            patch("restart._start_web", side_effect=lambda port: calls.append(("start", port)) or process),
        ):
            result = restart.main(["--port=1360", "--parent-pid=1234"])

        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [("terminate", 1234), ("wait_process", 1234), ("wait_port", 1360), ("start", 1360)],
        )

    def test_free_port_mode_does_not_terminate_calling_shell(self) -> None:
        process = SimpleNamespace(pid=9876)
        with (
            patch("restart._terminate_process") as terminate,
            patch("restart._wait_for_port"),
            patch("restart._start_web", return_value=process),
        ):
            result = restart.main(["--port=1360"])
        self.assertEqual(result, 0)
        terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
