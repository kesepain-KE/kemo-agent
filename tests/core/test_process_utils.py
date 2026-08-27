from __future__ import annotations

import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from run.infra import hidden_subprocess_kwargs, terminate_pid_tree


class HiddenSubprocessKwargsTests(unittest.TestCase):
    def test_background_processes_are_hidden_on_windows_only(self) -> None:
        kwargs = hidden_subprocess_kwargs()
        if sys.platform != "win32":
            self.assertEqual(kwargs, {})
            return

        self.assertTrue(
            kwargs["creationflags"]
            & getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        startupinfo = kwargs["startupinfo"]
        self.assertTrue(startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(startupinfo.wShowWindow, subprocess.SW_HIDE)


class PersistedProcessTerminationTests(unittest.TestCase):
    def test_missing_creation_identity_is_never_terminated(self) -> None:
        with patch("run.infra.process_utils.subprocess.run") as run_process:
            self.assertFalse(terminate_pid_tree(12345))
        run_process.assert_not_called()

    def test_windows_fallback_rechecks_identity_before_os_kill(self) -> None:
        snapshots = iter(
            [
                {"exists": True, "process_started_at": "start", "process_name": "python.exe"},
                {"exists": True, "process_started_at": "start", "process_name": "python.exe"},
                {"exists": True, "process_started_at": "other", "process_name": "python.exe"},
            ]
        )
        def matches(snapshot, *, process_started_at="", process_name=""):
            return snapshot.get("process_started_at") == process_started_at

        with (
            patch("run.infra.process_utils.sys.platform", "win32"),
            patch("run.infra.process_utils.hidden_subprocess_kwargs", return_value={}),
            patch("run.infra.process_identity.process_snapshot", side_effect=lambda pid: next(snapshots)),
            patch("run.infra.process_identity.process_identity_matches", side_effect=matches),
            patch(
                "run.infra.process_utils.subprocess.run",
                return_value=SimpleNamespace(returncode=1),
            ),
            patch("run.infra.process_utils.os.kill") as kill,
        ):
            result = terminate_pid_tree(
                12345,
                expected_process_started_at="start",
                expected_process_name="python.exe",
            )
        self.assertFalse(result)
        kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
