"""R1 3.2: reject live writers before source replacement and state migration."""
from __future__ import annotations

from contextlib import contextmanager
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from message.state import ProcessedMessageStore
from run.history import connection
from run.infra import hidden_subprocess_kwargs
from update import cli
from update import runtime_safety


class FakeProcess:
    def __init__(self, root, command, *, pid=44123, name="python.exe", files=()):
        self.pid = pid
        self.info = {"pid": pid, "name": name}
        self.directory = str(root)
        self.command = command
        self.files = files

    def cwd(self):
        return self.directory

    def cmdline(self):
        return self.command

    def open_files(self):
        return [SimpleNamespace(path=str(value)) for value in self.files]

    def is_running(self):
        return True

    def create_time(self):
        return 1.0


@pytest.fixture
def offline_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_safety, "_find_database_writer", Mock(return_value=None))
    source = tmp_path / "remote"
    source.mkdir()
    version = {"version": "1.0.0", "components": {
        name: {"version": "1.0.0"} for name in ("core", "agents", "plugins", "web")
    }}
    for name, result in {
        "_print_platform_warning": None, "require_commands": None,
        "load_local_version_document": version, "load_remote_version_document": version,
        "should_update": True, "clone_latest": source,
        "load_source_version_document": version, "source_revision": "offline",
    }.items():
        monkeypatch.setattr(cli, name, Mock(return_value=result))

    @contextmanager
    def transaction(**kwargs):
        yield None

    monkeypatch.setattr(cli, "update_transaction", transaction)
    writes = {}
    for name in (
        "run_modules", "refresh_dependencies", "build_web_frontend",
        "backup_runtime_databases", "migrate_user_skeletons",
        "initialize_user_memory_databases", "initialize_runtime_state_databases",
        "finalize_version_document",
    ):
        writes[name] = Mock(return_value=[] if name == "run_modules" else None)
        monkeypatch.setattr(cli, name, writes[name])
    return writes


@pytest.mark.parametrize("command", [
    ["python", "start_web.py"],
    ["python", "-m", "uvicorn", "web.app:app"],
    ["python", "-c", "from run.scheduler import build_host; build_host().start()"],
    ["python", "-c", "from message.router import MessageRouter"],
])
def test_live_writer_prevents_update_and_preserves_processing_message(
    tmp_path, monkeypatch, offline_cli, capsys, command,
):
    (tmp_path / "users/alice").mkdir(parents=True)
    store = ProcessedMessageStore(tmp_path, "alice")
    assert store.claim("offline:pending")
    with connection(tmp_path, "alice") as db:
        before = list(db.execute("SELECT * FROM message_processed_messages"))
    process = FakeProcess(tmp_path, command)
    monkeypatch.setattr(psutil, "process_iter", Mock(return_value=[process]))
    assert cli.main(["--module", "core", "--yes"], root=tmp_path) == 1
    assert "请先停止服务" in capsys.readouterr().err
    for method in offline_cli.values():
        method.assert_not_called()
    with connection(tmp_path, "alice") as db:
        assert list(db.execute("SELECT * FROM message_processed_messages")) == before
        assert db.execute("SELECT status FROM message_processed_messages").fetchone()[0] == "processing"


def test_new_writer_after_build_is_rechecked_before_database_backup(tmp_path, monkeypatch, offline_cli):
    process = FakeProcess(tmp_path, ["python", "start_web.py"])
    scan = Mock(side_effect=[[], [process]])
    monkeypatch.setattr(psutil, "process_iter", scan)
    assert cli.main(["--module", "core", "--yes"], root=tmp_path) == 1
    offline_cli["run_modules"].assert_called_once()
    offline_cli["backup_runtime_databases"].assert_not_called()
    offline_cli["migrate_user_skeletons"].assert_not_called()
    offline_cli["finalize_version_document"].assert_not_called()
    assert scan.call_count == 2


@pytest.mark.parametrize("arguments", [
    ["--module", "core", "--check"],
    ["--module", "core", "--yes", "--dry-run"],
])
def test_read_only_update_paths_do_not_inspect_or_stop_processes(tmp_path, monkeypatch, offline_cli, arguments):
    scan = Mock(side_effect=AssertionError("read-only operation must not scan"))
    monkeypatch.setattr(psutil, "process_iter", scan)
    assert cli.main(arguments, root=tmp_path) == 0
    scan.assert_not_called()


def test_another_install_and_stale_pid_file_do_not_block_update(tmp_path, monkeypatch, offline_cli):
    marker = tmp_path / "runtime/web.pid"
    marker.parent.mkdir()
    marker.write_text(str(os.getpid()), "utf-8")
    other = tmp_path.parent / (tmp_path.name + "-other")
    process = FakeProcess(other, ["python", str(other / "start_web.py")])
    monkeypatch.setattr(psutil, "process_iter", Mock(return_value=[process]))
    assert cli.main(["--module", "core", "--yes"], root=tmp_path) == 0
    offline_cli["initialize_runtime_state_databases"].assert_called_once()
    assert marker.read_text("utf-8") == str(os.getpid())


def test_absolute_entrypoint_is_detected_even_with_another_working_directory(tmp_path, monkeypatch, offline_cli):
    process = FakeProcess(tmp_path.parent, ["python", str(tmp_path / "start_web.py"), "--token=do-not-log"])
    monkeypatch.setattr(psutil, "process_iter", Mock(return_value=[process]))
    assert cli.main(["--module", "core", "--yes"], root=tmp_path) == 1
    offline_cli["run_modules"].assert_not_called()


def test_open_user_database_identifies_a_custom_writer(tmp_path, monkeypatch, offline_cli):
    process = FakeProcess(
        tmp_path.parent, ["python", "custom-worker.py"],
        files=[tmp_path / "users/alice/history/history.sqlite3"],
    )
    monkeypatch.setattr(psutil, "process_iter", Mock(return_value=[process]))
    monkeypatch.setattr(runtime_safety, "_find_database_writer", Mock(return_value=process.pid))
    assert cli.main(["--module", "core", "--yes"], root=tmp_path) == 1
    offline_cli["backup_runtime_databases"].assert_not_called()


def test_unknown_python_process_inspection_fails_closed_without_secret_output(
    tmp_path, monkeypatch, offline_cli, capsys,
):
    process = FakeProcess(tmp_path, ["python", "start_web.py", "--token=do-not-log"])
    process.cwd = Mock(side_effect=psutil.AccessDenied(process.pid))
    process.cmdline = Mock(side_effect=psutil.AccessDenied(process.pid))
    monkeypatch.setattr(psutil, "process_iter", Mock(return_value=[process]))
    assert cli.main(["--module", "core", "--yes"], root=tmp_path) == 1
    output = capsys.readouterr()
    assert "do-not-log" not in output.out + output.err
    assert "请先停止服务" in output.err
    offline_cli["run_modules"].assert_not_called()


def test_process_exiting_during_inspection_does_not_block_update(tmp_path, monkeypatch, offline_cli):
    process = FakeProcess(tmp_path, ["python", "start_web.py"])
    process.cwd = Mock(side_effect=psutil.NoSuchProcess(process.pid))
    monkeypatch.setattr(psutil, "process_iter", Mock(return_value=[process]))
    assert cli.main(["--module", "core", "--yes"], root=tmp_path) == 0
    offline_cli["initialize_runtime_state_databases"].assert_called_once()


def test_native_probe_failure_prevents_all_update_writes(tmp_path, monkeypatch, offline_cli, capsys):
    from update._utils import UpdateError

    monkeypatch.setattr(psutil, "process_iter", Mock(return_value=[]))
    monkeypatch.setattr(runtime_safety, "_find_database_writer", Mock(
        side_effect=UpdateError("无法核实进程，请先停止服务"),
    ))
    assert cli.main(["--module", "core", "--yes"], root=tmp_path) == 1
    assert "无法核实" in capsys.readouterr().err
    for method in offline_cli.values():
        method.assert_not_called()


def test_real_isolated_python_writer_is_identified_without_a_pid_file(tmp_path, offline_cli, capsys):
    # This is an inert test child in a temporary installation, not a real Web,
    # cron or message service. The checker must identify its actual live PID.
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read(1)"],
        cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **hidden_subprocess_kwargs(),
    )
    stdout = process.stdout
    assert stdout is not None
    try:
        assert stdout.readline().strip() == "ready"
        assert cli.main(["--module", "core", "--yes"], root=tmp_path) == 1
        assert f"PID={process.pid}" in capsys.readouterr().err
        offline_cli["run_modules"].assert_not_called()
    finally:
        try:
            process.communicate("x", timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
    assert process.returncode == 0
