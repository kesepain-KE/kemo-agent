"""Real Linux process groups behind the POSIX foreground Shell boundary."""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace
import multiprocessing
from multiprocessing import util as mp_util
from multiprocessing.connection import wait as wait_for_connections
import os
from pathlib import Path
import shlex
import signal
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from plugins.shell.tool import run as run_shell
from run.cards import CardStore, IntegrationStore
from run.infra import process_execution as isolation
from run.infra.process_execution import _isolated_worker
import run.tools as tools


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="需要 Linux /proc 和真实 POSIX 进程组")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROLES = ("root", "branch", "leaf")
_TREE = """
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

directory, mode, role = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if role == "leaf":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
state = Path("/proc/self/stat").read_text().rsplit(") ", 1)[1].split()
marker = directory / (role + ".pid")
temp = marker.with_suffix(".tmp")
temp.write_text(f"{os.getpid()} {state[19]} {os.getpgrp()}")
temp.rename(marker)
if mode == "natural":
    print("正常完成")
    print("stderr 保留", file=sys.stderr)
elif mode == "foreground":
    print("等待解除前", flush=True)
    while not (directory / "release").exists():
        time.sleep(0.02)
    print(f"解除后继续运行：{os.getpid()}")
    print("stderr 保留", file=sys.stderr)
elif mode == "background":
    while not (directory / "release").exists():
        time.sleep(0.02)
    print("后台完成")
elif mode in {"tree", "timeout-output"} and role != "leaf":
    if mode == "timeout-output" and role == "root":
        print("超时前 stdout", flush=True)
        print("超时前 stderr", file=sys.stderr, flush=True)
    child = "branch" if role == "root" else "leaf"
    subprocess.Popen([sys.executable, __file__, str(directory), mode, child]).wait()
elif mode == "stdin":
    print(sys.stdin.read())
else:
    time.sleep(60)
"""

_COOPERATIVE_TOOL = """
import os
from pathlib import Path
import shlex
import subprocess
import time

def run(command, *, context):
    subprocess.Popen(shlex.split(command), stdin=subprocess.DEVNULL)
    if context["test_mode"] == "cooperate":
        context["cancel_event"].wait(10)
        return {"ok": True}
    if context["test_mode"] == "ignore":
        time.sleep(60)
    release = Path(context["root"]) / "release"
    while not release.exists():
        time.sleep(0.005)
    os._exit(23)
"""


def _state(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None


def _alive(record):
    pid, start, _ = record
    state = _state(pid)
    return state is not None and int(state[19]) == start and state[0] not in {"Z", "X"}


def _wait_until(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "等待真实进程状态超时"
        time.sleep(0.01)


def _records(root, roles=ROLES):
    paths = [root / f"{role}.pid" for role in roles]
    _wait_until(lambda: all(path.is_file() for path in paths))
    return [tuple(map(int, path.read_text().split())) for path in paths]


def _assert_group_exited(records):
    group, = {record[2] for record in records}
    assert group != os.getpgrp()  # pyright: ignore[reportAttributeAccessIssue]

    def exited():
        if _state(group) is not None or any(_alive(record) for record in records):
            return False
        # 检查整个组，避免漏掉没有写 PID 标记的 Shell 解释器。
        for item in Path("/proc").glob("[0-9]*/stat"):
            state = _state(item.parent.name)
            if state is not None and state[2] == str(group) and state[0] not in {"Z", "X"}:
                return False
        return True

    _wait_until(exited)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "users" / "alice").mkdir(parents=True)
    (tmp_path / "tree.py").write_text(_TREE, encoding="utf-8")
    tool = tools.discover_tools(PROJECT_ROOT, "alice").get("shell")
    context = {"root": str(tmp_path), "user": "alice", "source": "test", "session_id": "shell-group"}
    yield tmp_path, tool, context
    # Failure cleanup uses an open pidfd plus start time, never a stale PID/PGID.
    for path in tmp_path.glob("*.pid"):
        record = tuple(map(int, path.read_text().split()))
        try:
            fd = os.pidfd_open(record[0])  # pyright: ignore[reportAttributeAccessIssue]
        except ProcessLookupError:
            continue
        try:
            if _alive(record):
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
                except ProcessLookupError:
                    pass
        finally:
            os.close(fd)


def _command(root, mode, *, real_shell=True):
    args = {"command": shlex.join([sys.executable, str(root / "tree.py"), str(root), mode, "root"])}
    return {**args, "shell_type": "sh"} if real_shell else args


def _fixture_tool(root, tool):
    (root / "harness.py").write_text(_COOPERATIVE_TOOL, encoding="utf-8")
    return replace(tool, directory=root, entrypoint="harness.py:run")


def test_multiprocessing_event_reaches_real_shell(workspace):
    root, tool, context = workspace
    call = isolation.start_isolated_tool(
        module_path=tool.directory / "tool.py", function_name="run",
        arguments=_command(root, "tree"),
        context={**context, "tool_timeout": 30}, own_shell_group=True,
    )
    try:
        assert not isinstance(call.cancel_event, threading.Event)
        records = _records(root)
        for pid, *_ in records:
            # After stripping PID and the parenthesized comm field, pgrp is index 2.
            state = _state(pid)
            assert state is not None and int(state[2]) == call.process.pid
        call.request_cancel()
        payload = call.receive(2)
        if payload is None:  # receive() can consume only the pending startup notice.
            payload = call.receive(2)
        assert payload is not None and payload["ok"] is True
        result = payload["value"]
        assert result["cancelled"] is True and result["exit_code"] == -1
        assert call.wait(2)
        assert _alive(records[-1])  # Leaf holds the pipes; only the host owns group cleanup.
    finally:
        try:
            call.terminate()
        finally:
            call.close()
    _assert_group_exited(records)


@pytest.mark.parametrize(
    ("mode", "hold_notice"),
    [("single", False), ("tree", False), ("tree", True)],
    ids=["single", "tree", "before-notice"],
)
def test_cancel_real_shell_reaps_all_descendants(workspace, monkeypatch, mode, hold_notice):
    root, tool, context = workspace
    cancel = threading.Event()
    workers = []
    start = tools.start_isolated_tool
    terminate = isolation.IsolatedProcessCall.terminate

    def observe(call):
        if hold_notice:
            assert call._shell_group is None and call.connection.poll(0)
        return terminate(call)

    def start_and_record_pid(**kwargs):
        call = start(**kwargs)
        workers.append(call.process.pid)
        if hold_notice:
            cancel.wait(10)  # Let the real command start before the host sees any IPC.
        return call

    monkeypatch.setattr(isolation.IsolatedProcessCall, "terminate", observe)
    monkeypatch.setattr(tools, "start_isolated_tool", start_and_record_pid)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(tools.execute_tool, tool, _command(root, mode),
                             context=context, timeout=20, cancel_event=cancel)
        try:
            records = _records(root, ("root",) if mode == "single" else ROLES)
            assert all(_alive(record) for record in records)
            for pid, *_ in records:
                state = _state(pid)
                assert state is not None and int(state[2]) == workers[0]
        finally:
            cancel.set()
        with pytest.raises(tools.ToolCancelledError):
            future.result(8)
    _assert_group_exited(records)


@pytest.mark.parametrize(("mode", "exits"), [("cooperate", True), ("ignore", False)])
def test_host_cleans_group_with_or_without_cooperative_exit(workspace, monkeypatch, mode, exits):
    root, tool, context = workspace
    tool = _fixture_tool(root, tool)
    cancel = threading.Event()
    worker_exited = []
    terminate = isolation.IsolatedProcessCall.terminate

    def observe(call):
        worker_exited.append(call.wait(0))
        return terminate(call)

    monkeypatch.setattr(isolation.IsolatedProcessCall, "terminate", observe)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(tools.execute_tool, tool, _command(root, "tree", real_shell=False),
                             context={**context, "test_mode": mode}, timeout=20, cancel_event=cancel)
        try:
            records = _records(root)
            assert all(_alive(record) for record in records)
        finally:
            cancel.set()
        with pytest.raises(tools.ToolCancelledError):
            future.result(8)
    assert worker_exited == [exits]
    _assert_group_exited(records)


@pytest.mark.parametrize(
    ("mode", "output"),
    [("tree", "命令超时 (2s)"), ("timeout-output", "超时前 stdout\n\n超时前 stderr")],
    ids=["no-output", "partial-output"],
)
def test_real_shell_internal_timeout_returns_original_result_and_cleans_group(workspace, mode, output):
    root, tool, context = workspace
    # Give the real plugin deadline time to return before the outer watchdog.
    tool = replace(tool, timeout_grace_seconds=5)
    result = tools.execute_tool(tool, {**_command(root, mode), "timeout": 2},
                                context=context, timeout=20)
    assert result == {
        "ok": False, "output": output, "exit_code": -1, "timed_out": True,
        "truncated": False, "shell_type": "sh", "cwd": str(root), "session_id": "",
    }
    _assert_group_exited(_records(root))


def test_nonisolated_cancellable_shell_creates_its_own_group(workspace):
    root, _, context = workspace
    cancel = threading.Event()
    arguments = _command(root, "single")
    with ThreadPoolExecutor() as pool:
        future = pool.submit(run_shell, command=arguments["command"], shell_type=arguments["shell_type"],
                             context={**context, "tool_timeout": 20, "cancel_event": cancel})
        try:
            records = _records(root, ("root",))
            pid, _, group = records[0]
            assert _alive(records[0])
            state = _state(pid)
            assert state is not None
            assert int(state[2]) == group != os.getpgrp()  # pyright: ignore[reportAttributeAccessIssue]
        finally:
            cancel.set()
        assert future.result(8)["cancelled"] is True
    _assert_group_exited(records)


@pytest.mark.parametrize("method", ["spawn", "fork"])
def test_concurrent_process_start_cannot_hide_or_reap_abnormal_exit(workspace, monkeypatch, method):
    root, tool, context = workspace
    tool = _fixture_tool(root, tool)
    entered, resume = threading.Event(), threading.Event()
    blocked = []
    terminate = isolation.IsolatedProcessCall.terminate
    other = multiprocessing.get_context(method).Process(target=time.sleep, args=(30,))

    if method == "fork":
        spawn = mp_util.spawnv_passfds

        def fork_while_starting(executable, args, fds):
            pid = spawn(executable, args, fds)
            if any("from multiprocessing.spawn import spawn_main" in os.fsdecode(arg) for arg in args):
                other.start()  # Inherit the sentinel writer before spawn closes its host copy.
            return pid

        monkeypatch.setattr(mp_util, "spawnv_passfds", fork_while_starting)

    def pause_cleanup(call):
        assert call.wait(2)
        if method == "fork":
            assert not wait_for_connections([call.process.sentinel], 0)
        blocked.append(call.process)
        entered.set()
        assert resume.wait(10)
        return terminate(call)

    monkeypatch.setattr(isolation.IsolatedProcessCall, "terminate", pause_cleanup)
    try:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(tools.execute_tool, tool, _command(root, "tree", real_shell=False),
                                 context={**context, "test_mode": "abnormal"}, timeout=20)
            try:
                records = _records(root)
                (root / "release").touch()
                assert entered.wait(8)
                worker, = blocked
                pid = worker.pid
                assert pid == records[0][2]
                flags = os.WEXITED | os.WNOWAIT | os.WNOHANG  # pyright: ignore[reportAttributeAccessIssue]
                info = os.waitid(os.P_PID, pid, flags)  # pyright: ignore[reportAttributeAccessIssue]
                assert info is not None and info.si_pid == pid
                if method == "spawn":
                    other.start()  # CPython Process.start() calls _cleanup() in this thread.
                multiprocessing.active_children()
                worker.join(0)
                assert worker.exitcode is None
                assert worker.is_alive()
                with pytest.raises(ValueError):
                    worker.close()
                info = os.waitid(os.P_PID, pid, flags)  # pyright: ignore[reportAttributeAccessIssue]
                assert info is not None and info.si_pid == pid
                assert other.is_alive()
            finally:
                resume.set()
            with pytest.raises(tools.ToolProcessError) as error:
                future.result(8)
            assert error.value.remote_exception_type == (
                "IsolatedProcessDisconnected" if method == "spawn" else "ToolProcessExited"
            )
            _assert_group_exited(records)
            assert other.is_alive()
            state = _state(other.pid)
            assert state is not None and state[0] not in {"Z", "X"}
    finally:
        if other.pid is not None:
            other.terminate()
            other.join(5)
            other.close()


def _setsid_fails_in_worker(*args):
    with patch.object(os, "setsid", side_effect=PermissionError("setsid denied")):
        _isolated_worker(*args)


def test_setsid_failure_does_not_execute_or_signal_unowned_group(workspace, monkeypatch):
    root, tool, context = workspace
    pids = []
    start = tools.start_isolated_tool

    def observe(**kwargs):
        call = start(**kwargs)
        pids.append(call.process.pid)
        return call

    monkeypatch.setattr(tools, "start_isolated_tool", observe)
    monkeypatch.setattr(isolation, "_isolated_worker", _setsid_fails_in_worker)
    monkeypatch.setattr(os, "killpg", lambda *_: pytest.fail("不得向未确认的进程组发信号"))
    with pytest.raises(tools.ToolProcessError, match="setsid"):
        tools.execute_tool(tool, _command(root, "natural"), context=context, timeout=15)
    assert not (root / "root.pid").exists()
    pid, = pids
    assert _state(pid) is None


def test_natural_exit_returns_the_same_fields_as_direct_shell(workspace):
    root, tool, context = workspace
    arguments = _command(root, "natural")
    original = run_shell(command=arguments["command"], shell_type=arguments["shell_type"],
                         context={**context, "tool_timeout": 15})
    assert original["ok"] and original["exit_code"] == 0
    assert original["output"] == "STDOUT:\n正常完成\n\nSTDERR:\nstderr 保留"
    assert tools.execute_tool(tool, arguments, context=context, timeout=15) == original
    _assert_group_exited(_records(root, ("root",)))


def test_isolated_shell_forwards_stdin_unchanged(workspace):
    root, tool, context = workspace
    arguments = {**_command(root, "stdin"), "stdin": "标准输入\n第二行\n"}
    original = run_shell(command=arguments["command"], shell_type=arguments["shell_type"],
                         stdin=arguments["stdin"], context={**context, "tool_timeout": 15})
    assert original["output"] == "标准输入\n第二行"
    assert tools.execute_tool(tool, arguments, context=context, timeout=15) == original


def _worker_with_inherited_stdin(module_path, function_name, arguments, context, *rest):
    with (Path(context["root"]) / "stdin.txt").open("rb") as stream:
        os.dup2(stream.fileno(), 0)
        _isolated_worker(module_path, function_name, arguments, context, *rest)


def test_isolated_shell_inherits_stdin_when_omitted(workspace, monkeypatch):
    root, tool, context = workspace
    (root / "stdin.txt").write_bytes(b"inherited stdin\n")
    monkeypatch.setattr(isolation, "_isolated_worker", _worker_with_inherited_stdin)
    result = tools.execute_tool(tool, _command(root, "stdin"), context=context, timeout=15)
    assert result["ok"] and result["output"] == "inherited stdin"


def test_explicit_background_is_still_alive_after_submission(workspace):
    root, tool, context = workspace
    started = tools.execute_tool(tool, {**_command(root, "background"), "background": True},
                                 context=context, timeout=20)
    assert started["ok"] and started["job_id"]
    records = _records(root, ("root",))
    assert records[0][2] == started["pid"] and _alive(records[0])
    (root / "release").touch()
    final = tools.execute_tool(tool, {"action": "status", "job_id": started["job_id"], "wait": True, "timeout": 10},
                               context=context, timeout=20)
    assert final["wait_outcome"] == "finished" and final["status"] == "completed"
    assert final["job_succeeded"] and final["exit_code"] == 0
    _assert_group_exited(records)


@pytest.fixture
def observed_calls(monkeypatch):
    calls = []
    start = tools.start_isolated_tool

    def observe(**kwargs):
        call = start(**kwargs)
        calls.append((call, threading.current_thread().name))
        return call

    monkeypatch.setattr(tools, "start_isolated_tool", observe)
    return calls


def _start_owned_shell(tool, context, arguments):
    integration = IntegrationStore(
        CardStore(Path(context["root"]), context["user"]),
        context["source"], context["session_id"], "shell-execution",
    )
    integration.start()
    runtime = tools.ToolRuntime(integration)
    call = SimpleNamespace(name="shell", call_id="callid_shell_execution", arguments=arguments)
    execution_id = runtime.start(call, tool, context=context, timeout=20)
    owner = runtime.shell_execution(execution_id)
    assert owner is not None
    return runtime, execution_id, owner


def test_unsubscribed_shell_completes_once_and_keeps_its_original_receipt(workspace, observed_calls):
    root, tool, context = workspace
    runtime, execution_id, owner = _start_owned_shell(tool, context, _command(root, "foreground"))
    waiter = owner.subscribe()
    try:
        records = _records(root, ("root",))
        pid = records[0][0]
        (call, thread_name), = observed_calls
        assert thread_name == f"card-tool-{execution_id}"
        assert _alive(records[0]) and not call.connection.closed
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(waiter.wait_reply, 8)
            try:
                with pytest.raises(FutureTimeoutError):
                    pending.result(0.1)
            finally:
                waiter.unsubscribe()
            assert pending.result(2) is None
        assert _alive(records[0]) and not call.connection.closed
        assert not owner.is_set() and not call.cancel_event.is_set()
        assert runtime.integration.tool_execution_status(execution_id) == "running"

        # No raw reply waiter remains when the real result arrives.
        (root / "release").touch()
        assert runtime.wait_for_initial_result(execution_id, timeout=8) == "completed"
        expected = {
            "ok": True,
            "output": f"STDOUT:\n等待解除前\n解除后继续运行：{pid}\n\nSTDERR:\nstderr 保留",
            "exit_code": 0, "timed_out": False, "truncated": False, "shell_type": "sh",
            "cwd": str(root), "session_id": "",
        }
        event, = [event for event in runtime.events() if event.type == "tool_call_result"]
        assert event.result == {"ok": True, "result": expected}
        assert event.metadata["execution_live"] is False
        reply = owner.subscribe()
        try:
            assert reply.wait_reply(0) == expected
        finally:
            reply.unsubscribe()
        assert _records(root, ("root",)) == records
        assert call.connection.closed and len(observed_calls) == 1
        _assert_group_exited(records)
    finally:
        waiter.unsubscribe()
        owner.close()


@pytest.mark.parametrize("stop", ["owner", "run"])
def test_cancel_owned_shell_after_unsubscribe(workspace, observed_calls, stop):
    root, tool, context = workspace
    runtime, execution_id, owner = _start_owned_shell(tool, context, _command(root, "tree"))
    waiter = owner.subscribe()
    try:
        records = _records(root)
        waiter.unsubscribe()
        assert all(_alive(record) for record in records)
        (call, _), = observed_calls
        assert not call.cancel_event.is_set() and not call.connection.closed
        if stop == "owner":
            owner.request_cancel()
            assert not runtime.cancel_event.is_set()
        else:
            runtime.cancel_event.set()
        reply = owner.subscribe()
        try:
            with pytest.raises(tools.ToolCancelledError):
                reply.wait_reply(8)
        finally:
            reply.unsubscribe()
        assert call.cancel_event.is_set() and call.connection.closed
        _assert_group_exited(records)
        assert runtime.wait_for_initial_result(execution_id, timeout=8) == "cancelled"
    finally:
        waiter.unsubscribe()
        owner.close()


@pytest.mark.parametrize("outcome", ["timeout", "abnormal"])
def test_unsubscribed_shell_still_handles_timeout_or_worker_exit(workspace, observed_calls, outcome):
    root, tool, context = workspace
    if outcome == "timeout":
        tool = replace(tool, timeout_grace_seconds=5)
        arguments = {**_command(root, "timeout-output"), "timeout": 3}
    else:
        tool = _fixture_tool(root, tool)
        context = {**context, "test_mode": "abnormal"}
        arguments = _command(root, "tree", real_shell=False)
    runtime, execution_id, owner = _start_owned_shell(tool, context, arguments)
    waiter = owner.subscribe()
    try:
        records = _records(root)
        waiter.unsubscribe()
        if outcome == "abnormal":
            (root / "release").touch()  # The actual worker calls os._exit(23).
        assert runtime.wait_for_initial_result(execution_id, timeout=10) == "failed"
        event, = [event for event in runtime.events() if event.type == "tool_call_result"]
        if outcome == "timeout":
            assert event.result["result"] == {
                "ok": False, "output": "超时前 stdout\n\n超时前 stderr", "exit_code": -1,
                "timed_out": True, "truncated": False, "shell_type": "sh",
                "cwd": str(root), "session_id": "",
            }
        else:
            assert event.result["error"]["exception_type"] == "IsolatedProcessDisconnected"
        (call, _), = observed_calls
        assert call.connection.closed
        _assert_group_exited(records)
    finally:
        waiter.unsubscribe()
        owner.close()
