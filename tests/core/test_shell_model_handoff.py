"""Fake-provider main/subagent loops must process Shell receipts before final delivery."""
import json
from pathlib import Path
import threading
import time
import pytest

from provider.schema import ChatResponse, ToolCall
from provider.protocol.models import ToolCallItem, ToolResultItem
from run.agents import AgentRunner
from run.engine import iter_request_events
from run.tools.background_jobs import prepare_background_job, read_background_job
from run.tools.background_worker import _finish
from tests.media_input_support import make_root, MediaProvider
from tests.agents.test_general_worker import OUTPUT, workspace  # noqa: F401
from tests.agents.test_subagent_continuation import Provider

STARTED = {}


def spawn(context):
    root = Path(context["root"])
    record, _ = prepare_background_job(
        root, context["user"], source=context["source"], session_id=context["session_id"],
        working_dir=str(root), shell_type="auto", command_digest="not-stored-command",
        ownership=context["shell_ownership"],
    )
    STARTED[str(root)] = record["job_id"]
    return {"job_id": record["job_id"], "status": "starting"}


def plugin(root):
    directory = root / "plugins/shell"
    directory.mkdir(parents=True)
    manifest = {
        "name": "shell", "description": "local managed test job", "version": "1",
        "enabled": True, "entrypoint": "tool.py:run", "execution_mode": "thread",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }
    (directory / "SKILL.md").write_text(
        "# shell\nTest\n\n## Tool\n```json\n" + json.dumps(manifest) + "\n```\n", "utf-8")
    (directory / "tool.py").write_text(
        "from tests.core.test_shell_model_handoff import spawn\n"
        "def run(*, context): return spawn(context)\n", "utf-8")


def eventually(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    assert predicate()


def finish(root) -> str:
    jid = STARTED.get(str(root))
    if jid:
        _finish(root, "alice", jid, exit_code=0, output_tail=["[stdout] verified-five-line-tail"],
                output_tail_complete=True)
    return jid or ""


def test_main_loop_waits_then_processes_receipt_without_replaying_tool(tmp_path):
    make_root(tmp_path)
    plugin(tmp_path)
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall(id="call_shell", name="shell", arguments={})]),
        ChatResponse(text="preliminary"),
        ChatResponse(text="final after Shell completion"),
    ])
    events, errors = [], []
    def run():
        try:
            for event in iter_request_events({
                "user": "alice", "source": "web", "session_id": "s1",
                "run_id": "run-shell-test", "prompt": "perform work",
            }, root=tmp_path, provider_factory=lambda _: provider):
                events.append(event)
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        eventually(lambda: any(e.type == "background_job_progress" for e in events))
        assert thread.is_alive() and len(provider.requests) == 2
        assert not any(e.type == "done" for e in events)
    finally:
        jid = finish(tmp_path)
    thread.join(8)
    assert not thread.is_alive() and not errors
    assert len(provider.requests) == 3
    final = provider.requests[-1].model_dump_json()
    assert "verified-five-line-tail" in final
    assert "not-stored-command" not in final
    assert read_background_job(tmp_path, "alice", jid)["notification_handled"]
    assert not any(e.type == "error" for e in events)
    assert any(e.type == "done" and e.metadata.get("committed") for e in events)


def test_subagent_waits_for_own_shell_and_receives_only_tail(workspace):
    root, config = workspace
    plugin(root)
    provider = Provider([
        [ToolCallItem(id="call_shell", call_id="callid_shell", name="shell", arguments={})],
        OUTPUT, OUTPUT,
    ])
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    results, errors = [], []
    def run():
        try:
            results.append(runner.run("general_worker", {"task": "work"}, source="web",
                                      session_id="s1", task_id="sub-shell-test"))
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        eventually(lambda: len(provider.requests) == 2)
        assert thread.is_alive()
        jid = STARTED[str(root)]
        assert read_background_job(root, "alice", jid)["owner_task_id"] == "sub-shell-test"
    finally:
        jid = finish(root)
    thread.join(8)
    assert not thread.is_alive() and not errors
    assert len(results) == 1 and results[0].data == OUTPUT
    assert len(provider.requests) == 3
    final = provider.requests[-1]
    assert "verified-five-line-tail" in final.model_dump_json()
    calls = [i.call_id for i in final.input if isinstance(i, ToolCallItem)]
    paired = [i.call_id for i in final.input if isinstance(i, ToolResultItem)]
    assert calls == paired
    assert read_background_job(root, "alice", jid)["notification_handled"]


@pytest.mark.parametrize("structured", [False, True])
def test_subagent_last_model_budget_still_processes_required_shell(workspace, structured):
    root, config = workspace
    plugin(root)
    config["tools"]["max_iterations"] = 1
    submit = ToolCallItem(id="call_submit", call_id="callid_submit",
                          name="submit_structured_output", arguments=OUTPUT)
    provider = Provider([
        [ToolCallItem(id="call_shell", call_id="callid_shell", name="shell", arguments={})],
        [submit] if structured else OUTPUT,
    ])
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    results, errors = [], []
    def run():
        try:
            results.append(runner.run("general_worker", {"task": "work"}, source="web",
                session_id="s1", task_id="sub-last-budget", structured_output_tool=structured))
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        eventually(lambda: str(root) in STARTED)
        assert thread.is_alive() and len(provider.requests) == 1
    finally:
        jid = finish(root)
    thread.join(8)
    assert not thread.is_alive() and not errors
    assert results[0].data == OUTPUT and len(provider.requests) == 2
    assert "verified-five-line-tail" in provider.requests[-1].model_dump_json()
    assert read_background_job(root, "alice", jid)["notification_handled"]
