from __future__ import annotations

import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from run.infra import (
    cancellable_subprocess_kwargs,
    detached_subprocess_kwargs,
    hidden_subprocess_kwargs,
    terminate_pid_tree,
    visible_subprocess_kwargs,
)
import run.infra.process_execution as process_execution


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

    def test_visible_subprocess_options_are_explicit_and_do_not_hide(self) -> None:
        with patch("run.infra.process_utils.sys.platform", "win32"):
            visible = visible_subprocess_kwargs()
            cancellable = cancellable_subprocess_kwargs(show_terminal=True)
            detached = detached_subprocess_kwargs(show_terminal=True)

        new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        detached_flag = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        self.assertEqual(visible["creationflags"], new_console)
        self.assertEqual(cancellable["creationflags"], new_console | new_group)
        self.assertEqual(detached["creationflags"], new_console | new_group)
        for options in (visible, cancellable, detached):
            self.assertNotIn("startupinfo", options)
            self.assertFalse(options["creationflags"] & no_window)
            self.assertFalse(options["creationflags"] & detached_flag)

    def test_show_terminal_is_ignored_on_non_windows(self) -> None:
        with patch("run.infra.process_utils.sys.platform", "linux"):
            self.assertEqual(visible_subprocess_kwargs(), {})
            self.assertEqual(cancellable_subprocess_kwargs(show_terminal=True), {
                "start_new_session": True
            })
            self.assertEqual(detached_subprocess_kwargs(show_terminal=True), {
                "start_new_session": True
            })


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


class IsolatedProcessExecutionTests(unittest.TestCase):
    def test_windows_spawn_context_uses_pythonw_when_available(self) -> None:
        context = SimpleNamespace(set_executable=Mock())
        with patch(
            "run.infra.process_execution._windows_pythonw_executable",
            return_value="C:/Python/pythonw.exe",
        ):
            executable = process_execution._configure_isolated_process_context(context)
        self.assertEqual(executable, "C:/Python/pythonw.exe")
        context.set_executable.assert_called_once_with("C:/Python/pythonw.exe")

    def test_spawn_context_keeps_default_executable_when_pythonw_is_missing(self) -> None:
        context = SimpleNamespace(set_executable=Mock())
        with patch(
            "run.infra.process_execution._windows_pythonw_executable",
            return_value=None,
        ):
            executable = process_execution._configure_isolated_process_context(context)
        self.assertIsNone(executable)
        context.set_executable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
