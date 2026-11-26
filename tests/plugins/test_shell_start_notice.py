"""Start notices on the existing foreground Shell result channel."""

from dataclasses import replace
from multiprocessing import Pipe
import os
from pathlib import Path
import shlex
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from plugins.shell.tool import run as run_shell
from run.cards import CardStore, IntegrationStore
from run.infra import process_execution as isolation
from run.infra.process_identity import process_identity_matches, process_snapshot
import run.tools as tools
from run.tools.runtime import _ShellExecution


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("include_controls", [True, False], ids=["controls-first", "result-first"])
def test_receive_classifies_every_frame(include_controls):
    reader, writer = Pipe(duplex=False)
    with reader, writer:
        # Exercise the group identity check with a matching worker PID.
        process = Mock(spec=isolation._ShellGroupProcess, pid=321)
        notices = []
        call = isolation.IsolatedProcessCall(
            process, reader, threading.Event(), _on_shell_command_started=notices.append,
        )
        starts = [
            {"kind": "shell_command_started", "pid": pid, "cwd": ".", "shell_type": "sh",
             "started_at": 1.0, "deadline_at": 2.0}
            for pid in (322, 323)
        ] if include_controls else []
        if include_controls:
            writer.send({"shell_group_ready": process.pid})
        for notice in starts:
            writer.send(notice)
        reply = {"ok": True, "value": {"ok": True, "output": "completed"}}
        writer.send(reply)

        # Also cover the nonblocking drain after the worker has exited.
        assert call.receive(0) == reply
        assert notices == starts
        assert call._shell_group == (process.pid if include_controls else None)
        assert call.receive(0) is None


@pytest.fixture
def shell(tmp_path):
    (tmp_path / "users" / "alice").mkdir(parents=True)
    context = {"root": str(tmp_path), "user": "alice", "source": "test", "session_id": "shell-start"}
    integration = IntegrationStore(CardStore(tmp_path, "alice"), "test", "shell-start", "shell-start")
    integration.start()
    runtime = tools.ToolRuntime(integration)
    tool = tools.discover_tools(PROJECT_ROOT, "alice").get("shell")
    return runtime, tool, context


def _start(shell, arguments, *, grace=0):
    runtime, tool, context = shell
    if grace:
        tool = replace(tool, timeout_grace_seconds=grace)
    call = SimpleNamespace(name="shell", call_id="callid_shell_start", arguments=arguments)
    return runtime.start(call, tool, context=context, timeout=20)


def _result(runtime):
    event, = [event for event in runtime.events() if event.type == "tool_call_result"]
    assert event.metadata["execution_live"] is False
    return event.result


@pytest.fixture
def notice_log(monkeypatch):
    notices = []
    command_started = _ShellExecution.command_started

    def observe(owner, notice):
        command_started(owner, notice)
        notices.append(notice)

    monkeypatch.setattr(_ShellExecution, "command_started", observe)
    return notices


@pytest.mark.parametrize(("command", "native", "ok"), [
    ("echo ready", True, True),
    ("kemo_4c1_command_that_does_not_exist", True, False),
    ("cd .", False, True),
], ids=["fast-exit", "interpreter-started-command-missing", "builtin"])
def test_shell_start_notice_stays_out_of_original_reply(shell, notice_log, command, native, ok):
    runtime, _, context = shell
    arguments = {"command": command}
    if native:
        arguments["shell_type"] = "cmd" if os.name == "nt" else "sh"
    original = run_shell(**arguments, context={**context, "tool_timeout": 20})

    execution_id = _start(shell, arguments)
    assert runtime.wait_for_initial_result(execution_id, timeout=12) == ("completed" if ok else "failed")
    result = _result(runtime)
    assert result["ok"] is original["ok"] is ok
    assert result["result"] == original
    assert len(notice_log) == int(native)


def test_real_popen_error_has_no_start_notice(shell, notice_log, tmp_path):
    runtime, _, _ = shell
    # Interpreter lookup succeeds, but the OS rejects this executable image.
    executable = tmp_path / ("cmd.exe" if os.name == "nt" else "sh")
    executable.write_text("not an executable image\n", encoding="utf-8")
    executable.chmod(0o755)
    if os.name == "nt":
        arguments = {"shell_type": "cmd", "env": {"COMSPEC": str(executable)}}
    else:
        arguments = {"shell_type": "sh", "env": {"PATH": str(tmp_path)}}

    execution_id = _start(shell, {"command": "echo must not run", **arguments})
    assert runtime.wait_for_initial_result(execution_id, timeout=12) == "failed"
    result = _result(runtime)
    assert result["ok"] is False and result["error"]["exception_type"] == "OSError"
    assert not notice_log


def _wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "等待真实进程状态超时"
        time.sleep(0.01)


@pytest.mark.parametrize("terminal", ["cancel", "timeout"])
def test_real_start_wakes_runtime_and_keeps_cleanup(shell, tmp_path, monkeypatch, terminal):
    runtime, _, _ = shell
    pid_path = tmp_path / "interpreter.pid"
    temp = pid_path.with_suffix(".tmp")
    if os.name == "nt":
        pid_arg = "'" + str(pid_path).replace("'", "''") + "'"
        temp_arg = "'" + str(temp).replace("'", "''") + "'"
        command = (
            f"[IO.File]::WriteAllText({temp_arg}, [string]$PID); "
            f"[IO.File]::Move({temp_arg}, {pid_arg}); Start-Sleep -Seconds 30"
        )
        shell_type = "powershell"
    else:
        command = (
            f'printf "%s\n" "$$" > {shlex.quote(str(temp))}; '
            f"mv {shlex.quote(str(temp))} {shlex.quote(str(pid_path))}; sleep 30; :"
        )
        shell_type = "sh"

    # Clear the ordinary tool_call_start wake before the worker is allowed to run.
    permit_start = threading.Event()
    start = tools.start_isolated_tool
    calls = []

    def gated_start(**kwargs):
        assert permit_start.wait(12)
        call = start(**kwargs)
        calls.append(call)
        return call

    boundary = "request_cancel" if terminal == "cancel" else "wait"
    original = getattr(isolation.IsolatedProcessCall, boundary)
    claims = []

    def observe_claim(call, *args):
        if not args or args[0] > 0:  # Ignore non-reaping liveness probes.
            with owner._changed:
                claims.append((owner._terminal_claimed, owner._reply is None))
        return original(call, *args)

    monkeypatch.setattr(tools, "start_isolated_tool", gated_start)
    monkeypatch.setattr(isolation.IsolatedProcessCall, boundary, observe_claim)
    timeout = 20 if terminal == "cancel" else 5
    execution_id = _start(shell, {"command": command, "shell_type": shell_type, "timeout": timeout}, grace=5)
    owner = runtime.shell_execution(execution_id)
    assert owner is not None
    try:
        assert [event.type for event in runtime.events()] == ["tool_call_start"]
        assert not runtime.changed.is_set()
        permit_start.set()
        assert runtime.changed.wait(12)
        with owner._changed:
            notice = owner._command_started
            assert not owner._terminal_claimed
        assert notice == {
            "kind": "shell_command_started", "pid": notice["pid"], "cwd": str(tmp_path),
            "shell_type": shell_type, "started_at": notice["started_at"],
            "deadline_at": pytest.approx(notice["started_at"] + timeout, rel=0, abs=1e-6),
        }
        assert not runtime.has_events()  # The notice is internal, not a result card.
        _wait_until(pid_path.is_file)
        assert int(pid_path.read_text()) == notice["pid"]
        snapshot = process_snapshot(notice["pid"])
        assert snapshot["exists"] and snapshot["identity_available"]
        if terminal == "cancel":
            owner.request_cancel()

        assert runtime.wait_for_initial_result(execution_id, timeout=12) == (
            "cancelled" if terminal == "cancel" else "failed"
        )
        result = _result(runtime)
        if terminal == "cancel":
            assert result["error"]["exception_type"] == "ToolCancelledError"
        else:
            assert result["result"]["timed_out"] is True and result["result"]["exit_code"] == -1
        assert claims and all(claimed and waiting for claimed, waiting in claims)
        call, = calls
        assert call.connection.closed
        _wait_until(lambda: process_identity_matches(
            process_snapshot(snapshot["pid"]), process_started_at=snapshot["process_started_at"],
        ) is False)
    finally:
        permit_start.set()
        owner.close()
