"""Import probes use fresh interpreters so earlier tests cannot hide cycles."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


MODULES = (
    "run.infra.cli",
    "run.engine",
    "run.conversation",
    "run.history",
    "run.history.commit_ops",
    "run.history.runtime_window",
    "run.history.session_api",
)


@pytest.mark.parametrize("modules", [(module,) for module in MODULES] + [MODULES, MODULES[::-1]])
def test_runtime_modules_import_in_isolated_interpreter(modules: tuple[str, ...]) -> None:
    script = (
        "import importlib, sys\n"
        "for name in sys.argv[1:]:\n"
        "    module = importlib.import_module(name)\n"
        "    assert not getattr(module.__spec__, '_initializing', False), name\n"
        "print('imports-ready')\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, *modules],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "imports-ready"
