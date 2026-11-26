"""Real process-exit cleanup for an isolated Shell with no reply waiter."""

import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

from run.infra.process_identity import process_identity_matches, process_snapshot
from run.infra.process_utils import terminate_pid_tree
from run.tools.background_jobs import read_background_job


PROJECT_ROOT = Path(__file__).resolve().parents[2]

_HOST = """
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from run.cards import CardStore, IntegrationStore
from run.tools import ToolRuntime, discover_tools

root = Path(sys.argv[1])
integration = IntegrationStore(CardStore(root, "alice"), "test", "shutdown", "shutdown")
integration.start()
runtime = ToolRuntime(integration)
tool = discover_tools(Path.cwd(), "alice").get("shell")
arguments = {"command": sys.argv[2], "shell_type": sys.argv[3]}
call = SimpleNamespace(name="shell", call_id="callid_shutdown", arguments=arguments)
execution_id = runtime.start(
    call, tool, context={"root": str(root), "user": "alice"}, timeout=45,
)
owner = runtime.shell_execution(execution_id)
owner.subscribe().unsubscribe()
if sys.argv[4] == "True":
    limit = time.monotonic() + 20
    while owner._command_started is None:
        assert time.monotonic() < limit
        time.sleep(.02)
    receipt = runtime.handoff_shell(execution_id)
    assert receipt["status"] == "handed_off"
    tmp = root / "job_id.tmp"
    tmp.write_text(receipt["job_id"])
    tmp.replace(root / "job_id.txt")
while not (root / "exit_host").exists():
    time.sleep(0.02)
# Return from the main thread normally: no explicit request_cancel() or join.
"""

_COMMAND = """
import os
from pathlib import Path
import subprocess
import sys
import time

root, role = Path(sys.argv[1]), int(sys.argv[2])
if role == 0:
    subprocess.Popen([sys.executable, __file__, str(root), "1"])
marker = root / f"command-{role}.pid"
tmp = marker.with_suffix(".tmp")
tmp.write_text(str(os.getpid()))
tmp.rename(marker)
time.sleep(60)
"""


def _wait_until(predicate, timeout=12):
    limit = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < limit, "真实进程未在预期时间内改变状态"
        time.sleep(0.02)


def _matches(record):
    return process_identity_matches(
        process_snapshot(record["pid"]), process_started_at=record["process_started_at"],
    )


@pytest.mark.parametrize("handed_off", [False, True], ids=["foreground", "handed-off"])
def test_orderly_host_exit_cleans_shell_without_a_waiter(tmp_path, handed_off):
    (tmp_path / "users" / "alice").mkdir(parents=True)
    script = tmp_path / "command.py"
    script.write_text(_COMMAND, encoding="utf-8")
    args = [sys.executable, str(script), str(tmp_path), "0"]
    if os.name == "nt":
        command = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in args)
        shell_type = "powershell"
    else:
        command, shell_type = shlex.join(args), "sh"
    records = []
    job_id = None
    host = subprocess.Popen(
        [sys.executable, "-c", _HOST, str(tmp_path), command, shell_type, str(handed_off)],
        cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        paths = [tmp_path / f"command-{i}.pid" for i in range(2)]
        job_path = tmp_path / "job_id.txt"
        _wait_until(
            lambda: host.poll() is not None or (
                all(path.exists() for path in paths) and (not handed_off or job_path.exists())
            ),
            timeout=22 if handed_off else 12,
        )
        assert host.poll() is None, host.communicate(timeout=3)
        if handed_off:
            job_id = job_path.read_text()
            assert read_background_job(tmp_path, "alice", job_id)["status"] == "running"
        records = [process_snapshot(int(path.read_text())) for path in paths]
        assert all(record["exists"] and record["identity_available"] for record in records)
        (tmp_path / "exit_host").touch()
        stdout, stderr = host.communicate(timeout=12)
        assert host.returncode == 0, stdout + stderr
        assert not stderr, stderr
        _wait_until(lambda: all(_matches(record) is False for record in records), timeout=5)
        if handed_off:
            assert job_id is not None
            terminal = read_background_job(tmp_path, "alice", job_id)
            assert terminal["status"] == "cancelled" and terminal["finished_at"]
    finally:
        if host.poll() is None:
            host.kill()
        host.communicate(timeout=5)
        for record in records:
            if record.get("process_started_at") and _matches(record) is True:
                terminate_pid_tree(
                    record["pid"], expected_process_started_at=record["process_started_at"],
                )
