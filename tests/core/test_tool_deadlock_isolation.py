"""R4 J1: retain live executions without allowing them to exhaust other roles."""
import os
import threading
from pathlib import Path

import pytest

from run.tools import (
    ConsecutiveToolFailureTracker,
    ToolDefinition,
    ToolTimeoutError,
    ToolExecutionCapacityError,
    execute_tool,
)
from run.tools import execution_watchdog as watchdog


def tool_at(path, name="probe", **kwargs):
    return ToolDefinition(
        name=name, description="probe", input_schema={"type": "object"}, version="1",
        enabled=True, entrypoint="tool.py:run", source="test", directory=path, **kwargs,
    )


def test_eight_abandoned_threads_use_process_for_ninth_independent_call(tmp_path, monkeypatch):
    gate = tmp_path / "release"
    (tmp_path / "tool.py").write_text(
        "import os, threading, time\nfrom pathlib import Path\n"
        "def run(context):\n"
        "    if threading.current_thread().name != 'MainThread':\n"
        "        while not Path(context['root'], 'release').exists(): time.sleep(.01)\n"
        "    return {'ok': True, 'pid': os.getpid()}\n", "utf-8",
    )
    tool = tool_at(tmp_path)
    monkeypatch.setattr("run.tools._TOOL_TIMEOUT_CLEANUP_GRACE", .01)
    pending = []
    try:
        for index in range(8):
            with pytest.raises(ToolTimeoutError) as raised:
                execute_tool(tool, {}, context={"root": str(tmp_path), "user": "a",
                             "session_id": str(index), "source": "web"}, timeout=.03)
            assert raised.value.still_running
            pending.append(raised.value.completion_future)
        # The old session remains protected, even when the bucket is full.
        with pytest.raises(ToolExecutionCapacityError):
            execute_tool(tool, {}, context={"root": str(tmp_path), "user": "a",
                         "session_id": "0", "source": "web"}, timeout=5)
        result = execute_tool(tool, {}, context={"root": str(tmp_path), "user": "a",
                              "session_id": "9", "source": "web"}, timeout=5)
        assert result["ok"] and result["pid"] != os.getpid()
        assert all(not future.done() for future in pending)
        # A different role can still start normal cooperative thread work.
        other = tool_at(tmp_path, _callable=lambda: {"ok": True})
        assert execute_tool(other, {}, context={"root": str(tmp_path), "user": "b"},
                            timeout=1)["ok"]
    finally:
        gate.touch()
        for future in pending:
            future.result(timeout=5)


def test_aged_deadlock_degrades_new_call_but_keeps_original_registration(tmp_path, monkeypatch):
    release = threading.Event()
    tool = tool_at(tmp_path, _callable=lambda: release.wait(10))
    (tmp_path / "tool.py").write_text("import os\ndef run(context): return {'ok': True, 'pid': os.getpid()}\n", "utf-8")
    monkeypatch.setattr("run.tools._TOOL_TIMEOUT_CLEANUP_GRACE", .01)
    raised: pytest.ExceptionInfo[ToolTimeoutError] | None = None
    try:
        with pytest.raises(ToolTimeoutError) as raised:
            execute_tool(tool, {}, context={"user": "a", "session_id": "old"}, timeout=.01)
        monkeypatch.setattr(watchdog, "DEADLOCK_SECONDS", 0)
        disk_tool = tool_at(tmp_path)
        assert execute_tool(disk_tool, {}, context={"user": "a", "session_id": "new"},
                            timeout=5)["pid"] != os.getpid()
        assert not raised.value.completion_future.done()
        with pytest.raises(ToolExecutionCapacityError):
            execute_tool(disk_tool, {}, context={"user": "a", "session_id": "old"}, timeout=5)
    finally:
        release.set()
        if raised is not None:
            raised.value.completion_future.result(timeout=5)


@pytest.mark.parametrize("name,arguments", [
    ("file", {"action": "write"}), ("shell", {"action": "run"}),
])
def test_side_effect_tool_cannot_opt_into_thread_mode(tmp_path, name, arguments):
    (tmp_path / "tool.py").write_text(
        "import os\ndef run(context, **kwargs): return {'ok': True, 'pid': os.getpid()}\n", "utf-8")
    assert execute_tool(tool_at(tmp_path, name), arguments, context={}, timeout=5)["pid"] != os.getpid()


@pytest.mark.parametrize("name,arguments", [
    ("file", {"action": "write", "path": "probe.txt"}),
    ("shell", {"action": "run"}),
])
def test_memory_injected_side_effect_tool_still_uses_process_boundary(
    tmp_path, name, arguments,
):
    (tmp_path / "tool.py").write_text(
        "import os\ndef run(context, **kwargs): return {'ok': True, 'pid': os.getpid()}\n",
        "utf-8",
    )
    tool = tool_at(
        tmp_path,
        name,
        _callable=lambda **_kwargs: {"ok": True, "pid": os.getpid()},
    )
    result = execute_tool(tool, arguments, context={}, timeout=5)
    assert result["pid"] != os.getpid()


def test_thread_mode_context_dependency_does_not_fallback_to_process(tmp_path, monkeypatch):
    release = threading.Event()
    tool = tool_at(
        tmp_path,
        "subagent_dispatch",
        _callable=lambda context: release.wait(10),
    )
    monkeypatch.setattr("run.tools._TOOL_TIMEOUT_CLEANUP_GRACE", .01)
    pending = []
    try:
        for index in range(8):
            with pytest.raises(ToolTimeoutError) as raised:
                execute_tool(
                    tool,
                    {},
                    context={
                        "user": "a", "session_id": str(index),
                        "event_callback": lambda _event: None,
                    },
                    timeout=.01,
                )
            pending.append(raised.value.completion_future)
        with pytest.raises(ToolExecutionCapacityError, match="不能安全迁移|容量"):
            execute_tool(
                tool,
                {},
                context={
                    "user": "a", "session_id": "ninth",
                    "event_callback": lambda _event: None,
                },
                timeout=5,
            )
    finally:
        release.set()
        for future in pending:
            future.result(timeout=5)


def test_real_subagent_dispatch_context_stays_thread_bound_at_capacity(tmp_path, monkeypatch):
    from plugins.subagent_dispatch import tool as dispatch

    release = threading.Event()
    calls = []

    def blocked_public(_root, _user):
        calls.append(1)
        release.wait(5)
        return []

    monkeypatch.setattr(dispatch, "_public", blocked_public)
    monkeypatch.setattr(dispatch, "load_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(dispatch, "discover_external_agents", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("run.tools._TOOL_TIMEOUT_CLEANUP_GRACE", .01)
    directory = Path(__file__).resolve().parents[2] / "plugins" / "subagent_dispatch"
    tool = tool_at(
        directory,
        "subagent_dispatch",
        _callable=dispatch.run,
        execution_mode="thread",
    )
    context = {
        "root": str(tmp_path),
        "user": "alice",
        "source": "cron",
        "session_id": "session-1",
        "task_ownership_factory": lambda: {},
    }
    pending = []
    try:
        for index in range(8):
            scoped = {**context, "session_id": f"session-{index}"}
            with pytest.raises(ToolTimeoutError) as raised:
                execute_tool(tool, {"action": "list"}, context=scoped, timeout=.03)
            assert raised.value.still_running
            pending.append(raised.value.completion_future)

        with pytest.raises(ToolExecutionCapacityError):
            execute_tool(tool, {"action": "list"}, context=context, timeout=5)
        assert len(calls) == 8
        assert all(not future.done() for future in pending)
    finally:
        release.set()
        for future in pending:
            future.result(timeout=5)


def test_capacity_limited_attempts_do_not_trip_same_run_failure_limit():
    failures = ConsecutiveToolFailureTracker(limit=3)
    assert failures.record("subagent_dispatch", succeeded=False) == 1
    assert failures.record("subagent_dispatch", succeeded=False) == 2

    assert failures.record_capacity_limited("subagent_dispatch") == 2
    assert failures.record_capacity_limited("subagent_dispatch") == 2
    assert not failures.is_unavailable("subagent_dispatch")
    assert failures.record("subagent_dispatch", succeeded=True) == 0
    assert not failures.is_unavailable("subagent_dispatch")


@pytest.mark.parametrize("loaded_callable", [False, True])
def test_pickleable_entrypoint_subagent_is_still_thread_bound_at_capacity(tmp_path, monkeypatch, loaded_callable):
    from run.tools import isolation_policy

    (tmp_path / "tool.py").write_text("def run(context): return {'ok': True}\n", "utf-8")
    definition = tool_at(tmp_path, "subagent_dispatch", execution_mode="thread")
    if loaded_callable:
        definition._callable = lambda context: {"ok": True}
        definition._loaded_from_entrypoint = True
    context = {"root": str(tmp_path), "user": "alice", "session_id": "new"}
    assert isolation_policy._context_can_cross_process({}, context)

    def full_bucket(*args, **kwargs):
        raise watchdog.ExecutionIsolationRequired("bucket full")

    monkeypatch.setattr(isolation_policy, "reserve_execution", full_bucket)
    with pytest.raises(ToolExecutionCapacityError, match="不能安全迁移"):
        isolation_policy.reserve_tool_execution(definition, {}, context)
    # Only the subagent identity changes: an independent entrypoint can migrate.
    definition.name = "independent_probe"
    assert isolation_policy.reserve_tool_execution(definition, {}, context) is None


def test_external_message_isolates_only_output_and_preserves_receipt(tmp_path):
    from message.plugin import FileMessageTransport, MessagePluginConfig
    from message.transport import TransportRegistry
    from tests.message.test_message_runtime import _write_file_plugin

    (tmp_path / "users/alice").mkdir(parents=True)
    directory = _write_file_plugin(tmp_path)
    (directory / "output.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def send(message):\n"
        "    Path(__file__).with_name('sent-pid').write_text(str(os.getpid()))\n"
        "    return True\n", "utf-8",
    )
    transport = FileMessageTransport(MessagePluginConfig.load(tmp_path, directory))
    transport._running = True
    registry = TransportRegistry()
    registry.register(transport, transport.policy).state = "running"
    tool = tool_at(Path(__file__).resolve().parents[2] / "plugins/external_message", "external_message")
    result = execute_tool(tool, {"action": "send_message", "platform": "filedemo",
                          "target": "test", "chat_type": "private", "message": "probe"},
                          context={"root": str(tmp_path), "user": "alice",
                                   "transport_registry": registry}, timeout=10)
    assert result["ok"] is True
    assert int((directory / "sent-pid").read_text()) != os.getpid()
    assert transport._input_thread is None
    assert transport._read_state()["messages_sent_today"] == 1
