from __future__ import annotations

import subprocess
import sys
import unittest

from run.process_utils import hidden_subprocess_kwargs


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


if __name__ == "__main__":
    unittest.main()
