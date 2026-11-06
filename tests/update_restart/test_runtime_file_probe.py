"""Native process-handle inspection must not crash or bypass updater guards."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from update import runtime_safety
from update import runtime_file_probe
from update._utils import UpdateError


@pytest.mark.parametrize("returncode,output", [
    (-1073741819, ""),
    (0, "invalid json"),
    (0, '{"writer_pid":99999}'),
    (0, '{}'),
])
def test_probe_crash_or_invalid_receipt_refuses_update(tmp_path, monkeypatch, returncode, output):
    runner = Mock(return_value=subprocess.CompletedProcess([], returncode, output))
    monkeypatch.setattr(subprocess, "run", runner)
    with pytest.raises(UpdateError, match="无法核实"):
        runtime_safety._find_database_writer([(123, 1.0)], tmp_path)
    kwargs = runner.call_args.kwargs
    assert kwargs["timeout"] <= 15
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert json.loads(kwargs["input"])["processes"] == [[123, 1.0]]


def test_probe_timeout_refuses_update(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired([], 15)))
    with pytest.raises(UpdateError, match="无法核实"):
        runtime_safety._find_database_writer([(123, 1.0)], tmp_path)


@pytest.mark.parametrize("writer", [None, 123])
def test_verified_probe_receipt_is_used(tmp_path, monkeypatch, writer):
    monkeypatch.setattr(subprocess, "run", Mock(return_value=subprocess.CompletedProcess(
        [], 0, json.dumps({"writer_pid": writer}),
    )))
    assert runtime_safety._find_database_writer([(123, 1.0)], tmp_path) == writer


def test_probe_checks_database_path_and_process_identity(tmp_path, monkeypatch):
    process = SimpleNamespace(
        create_time=lambda: 1.0, is_running=lambda: True,
        open_files=lambda: [SimpleNamespace(path=str(tmp_path / "users/alice/history.db-wal"))],
    )
    monkeypatch.setattr(runtime_file_probe.psutil, "Process", Mock(return_value=process))
    assert runtime_file_probe.find_writer({"root": str(tmp_path), "processes": [[123, 1.0]]}) == 123
    with pytest.raises(ValueError, match="identity"):
        runtime_file_probe.find_writer({"root": str(tmp_path), "processes": [[123, 2.0]]})
    assert runtime_file_probe.find_writer({
        "root": str(tmp_path / "other"), "processes": [[123, 1.0]],
    }) is None
