"""Shell receipts are bounded, durable, scoped, and acknowledged after model IO."""
import json
import threading
from unittest.mock import Mock

import pytest

from run.tools.background_jobs import prepare_background_job, update_background_job, read_background_job
from run.tools.background_notifications import ShellInbox
from run.tools.background_output import OutputTail
from run.tools.background_worker import _finish, _capture_stream
from run.conversation.provider_tasks import settle_shell


@pytest.fixture
def root(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    (tmp_path / "users/bob").mkdir()
    return tmp_path


def job(root, *, user="alice", source="web", session="s1", parent="main-1",
        owner="", mode="required", done=True):
    record, _ = prepare_background_job(
        root, user, source=source, session_id=session, working_dir=str(root),
        shell_type="auto", command_digest="not-a-command",
        ownership={"root_task_id": parent, "owner_task_id": owner},
        completion_mode=mode, label="test",
    )
    if done:
        _finish(root, user, record["job_id"], exit_code=0,
                output_tail=["[stdout] done"], output_tail_complete=True)
    return record["job_id"]

def receipt(inbox):
    value = inbox.take()
    assert value is not None
    return value


def test_receipts_stay_unhandled_until_successful_provider_ack(root):
    jid = job(root)
    inbox = ShellInbox(root, "alice", "web", "s1", root_task_id="main-1")
    assert jid in receipt(inbox)["content"]
    assert inbox.take() is None
    assert not read_background_job(root, "alice", jid)["notification_handled"]
    retried = ShellInbox(root, "alice", "web", "s1", root_task_id="main-1")
    assert jid in receipt(retried)["content"]
    retried.acknowledge()
    assert read_background_job(root, "alice", jid)["notification_handled"]
    assert ShellInbox(root, "alice", "web", "s1").take() is None


def test_receipts_isolate_user_source_session_and_live_subagent(root):
    own = job(root)
    foreign = [job(root, user="bob"), job(root, source="cli"), job(root, session="s2"),
               job(root, owner="live-child")]
    content = receipt(ShellInbox(root, "alice", "web", "s1"))["content"]
    assert own in content
    assert all(jid not in content for jid in foreign)
    child = ShellInbox(root, "alice", "web", "s1", root_task_id="main-1", owner_task_id="live-child")
    assert foreign[-1] in receipt(child)["content"]


def test_required_barrier_is_per_root_and_detached_does_not_block(root):
    required = job(root, done=False)
    job(root, parent="other-main", done=False)
    detached = job(root, mode="detached", done=False)
    inbox = ShellInbox(root, "alice", "web", "s1", root_task_id="main-1")
    assert [r["job_id"] for r in inbox.pending()] == [required]
    _finish(root, "alice", required, exit_code=0)
    assert inbox.pending() == []
    _finish(root, "alice", detached, exit_code=0)
    receipt = inbox.take()
    assert receipt is not None and required in receipt["content"] and detached in receipt["content"]


def test_notifications_have_stable_order_and_bounded_batch(root):
    ids = [job(root) for _ in range(6)]
    for jid in ids:
        update_background_job(root, "alice", jid, lambda record: {**record, "finished_at": "2026-10-27T00:00:00+00:00"})
    inbox = ShellInbox(root, "alice", "web", "s1")
    content = receipt(inbox)["content"].split("\n", 1)[1]
    assert [r["job_id"] for r in json.loads(content)] == sorted(ids)[:4]
    assert len(json.loads(receipt(inbox)["content"].split("\n", 1)[1])) == 2


def test_empty_and_unreadable_records_cannot_claim_completion(root):
    inbox = ShellInbox(root, "alice", "web", "s1", root_task_id="main-1")
    assert inbox.take() is None
    assert not (root / "users/alice/runtime").exists()
    jid = job(root, done=False)
    (root / "users/alice/runtime/background_jobs" / f"{jid}.json").write_text("{broken", "utf-8")
    with pytest.raises(RuntimeError, match="损坏"):
        inbox.pending()


def test_capture_tail_is_last_five_observed_lines_and_redacts_common_secrets():
    tail = OutputTail()
    tail.feed("stdout", b"one\ntwo\nthree\n")
    tail.feed("stderr", b"four\n")
    tail.feed("stdout", "五\n六\n".encode())
    tail.feed("stderr", b"api_key=do-not-leak\n")
    lines = tail.snapshot()
    assert len(lines) == 5 and lines[0] == "[stdout] three"
    assert "do-not-leak" not in "\n".join(lines)
    assert "[stderr]" in lines[-1]
    tail.feed("stdout", b"x" * 10000)
    assert max(map(len, tail.snapshot())) <= 409


def test_log_limit_does_not_turn_completion_tail_into_the_start_of_the_log(tmp_path):
    import io
    tail = OutputTail()
    _capture_stream(io.BytesIO(b"first\n" + b"x" * 100 + b"\nlast\n"),
                    tmp_path / "out.log", max_bytes=50, tail=tail)
    assert tail.snapshot()[-1] == "[stdout] last"
    assert len((tmp_path / "out.log").read_bytes()) <= 50


def test_required_shell_waits_before_final_and_adds_only_receipt(root):
    jid = job(root, done=False)
    inbox = ShellInbox(root, "alice", "web", "s1", root_task_id="main-1")
    timer = threading.Timer(.05, lambda: _finish(root, "alice", jid, exit_code=0,
                                              output_tail=["[stdout] final"], output_tail_complete=True))
    timer.start()
    messages = []
    iterator = settle_shell(inbox, messages=messages, iteration_text=["preliminary"],
                            cancel_event=threading.Event(), commit_cancelled_round=Mock())
    assert next(iterator).metadata["status"] == "waiting_shell"
    with pytest.raises(StopIteration) as done:
        next(iterator)
    timer.join()
    assert done.value.value == "continue"
    assert jid in messages[-1]["content"]
    assert "not-a-command" not in messages[-1]["content"]


@pytest.mark.parametrize("status", ["timed_out", "tool_limit", "failed"])
def test_subagent_terminal_receipt_can_be_taken_over_by_main(root, monkeypatch, status):
    from run.agents.task_ledger import TaskLedger
    jid = job(root, owner="finished-child")
    ledger = TaskLedger(root, "alice")
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.touch()
    monkeypatch.setattr(TaskLedger, "get", lambda *_: {"status": status, "execution_live": True})
    inbox = ShellInbox(root, "alice", "web", "s1")
    assert inbox.take() is None
    monkeypatch.setattr(TaskLedger, "get", lambda *_: {"status": status, "execution_live": False})
    assert jid in receipt(inbox)["content"]


def test_resumed_owner_keeps_old_required_shell_in_new_root_barrier(root, monkeypatch):
    from run.agents.task_ledger import TaskLedger
    jid = job(root, owner="resumed-child", parent="old-main", done=False)
    ledger = TaskLedger(root, "alice")
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.touch()
    monkeypatch.setattr(TaskLedger, "get", lambda *_: {
        "status": "running", "execution_live": True, "root_task_id": "new-main"})
    child = ShellInbox(root, "alice", "web", "s1", root_task_id="new-main", owner_task_id="resumed-child")
    main = ShellInbox(root, "alice", "web", "s1", root_task_id="new-main")
    assert [r["job_id"] for r in child.pending()] == [jid]
    assert [r["job_id"] for r in main.pending()] == [jid]
    _finish(root, "alice", jid, exit_code=0)
    assert child.pending() == []
    assert jid in receipt(child)["content"]


def test_real_worker_flushes_five_line_snapshot_before_publishing_terminal(root):
    import sys
    import time
    from plugins.shell.tool import run
    result = run(
        command=f'"{sys.executable}" -u -c "for i in range(8): print(i, flush=True)"',
        background=True, label="snapshot worker", timeout=15,
        context={"root": str(root), "user": "alice", "source": "web", "session_id": "s1", "tool_timeout": 15,
                 "shell_ownership": {"root_task_id": "main-1", "owner_task_id": ""}},
    )
    jid = result["job_id"]
    deadline = time.monotonic() + 10
    record = read_background_job(root, "alice", jid)
    while time.monotonic() < deadline:
        record = read_background_job(root, "alice", jid)
        if record["status"] == "completed":
            break
        time.sleep(.05)
    assert record["status"] == "completed"
    assert record["output_tail_complete"] is True
    assert record["output_tail"] == [f"[stdout] {i}" for i in range(3, 8)]
    assert record["finished_at"]
    assert ShellInbox(root, "alice", "web", "s1").take() is not None


def test_tail_decodes_split_utf8_and_host_shell_encoding(monkeypatch):
    import locale
    text = "中文完成\n".encode("utf-8")
    tail = OutputTail()
    tail.feed("stdout", text[:2])
    tail.feed("stdout", text[2:])
    assert tail.snapshot() == ["[stdout] 中文完成"]
    monkeypatch.setattr(locale, "getpreferredencoding", lambda _: "gbk")
    other = OutputTail()
    other.feed("stdout", "中文完成\n".encode("gbk"))
    assert other.snapshot() == ["[stdout] 中文完成"]


def test_runtime_progress_event_round_trips_and_is_not_terminal():
    from events import RunEvent, TERMINAL_EVENTS
    event = RunEvent(type="background_job_progress", metadata={"status": "waiting_shell"})
    assert RunEvent.from_dict(event.to_dict()) == event
    assert event.type not in TERMINAL_EVENTS


def test_cancel_wait_requests_stop_without_fabricating_success(root):
    jid = job(root, done=False)
    cancel = threading.Event()
    cancel.set()
    inbox = ShellInbox(root, "alice", "web", "s1", root_task_id="main-1")
    assert inbox.wait(cancel) is False
    record = read_background_job(root, "alice", jid)
    assert record["cancel_requested"] is True
    assert record["status"] != "completed"
