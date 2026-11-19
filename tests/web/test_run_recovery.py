import json
import threading
import time

import pytest

from tests.type_assertions import require_present

from events import RunEvent
from web.errors import NotFoundError, TooManyChatsError
from web.service import WebRunService
from web.services.run_events import RunEventJournal


def test_replay_watermark_overflow_and_compacted_snapshot():
    journal = RunEventJournal(max_bytes=400)
    journal.publish(RunEvent(type="text_delta", content="already saved"))
    journal.publish(RunEvent(type="tool_call_start", tool_call_id="call"), history_seq=3)
    for _ in range(30):
        journal.publish(RunEvent(type="text_delta", content='new\n"片段"'))
    batch = journal.read(0, history_seq=0, wait=0)
    assert batch["reset"]
    snapshot = batch["snapshot"]
    assert snapshot["history_seq"] == 3
    assert [event["content"] for event in snapshot["events"]] == ['new\n"片段"' * 30]
    assert journal.snapshot_size == len(json.dumps(
        snapshot["events"][0], ensure_ascii=False, separators=(",", ":")
    ).encode())
    journal.publish(RunEvent(type="text_delta", content="tail"))
    increment = journal.read(snapshot["cursor"], history_seq=3, wait=0)
    assert not increment["reset"]
    assert [event["content"] for event in increment["events"]] == ["tail"]


def test_finished_replay_batches_and_bounded_snapshot_keep_true_terminal():
    journal = RunEventJournal(snapshot_bytes=300)
    for index in range(600):
        journal.publish(RunEvent(type="text_delta", item_id=str(index), content="x" * 100))
    journal.publish(RunEvent(type="done", metadata={"status": "cancelled", "cancelled": True}))
    assert journal.snapshot()["snapshot_incomplete"]
    assert journal.snapshot_size <= 300
    cursor, types = 0, []
    while True:
        result = journal.read(cursor, history_seq=0, wait=0)
        assert not result["reset"]
        types.extend(event["type"] for event in result["events"])
        cursor = result["cursor"]
        if result["finished"]:
            break
        assert "done" not in types
    assert len(types) == 601 and types.count("done") == 1
    assert journal.snapshot()["terminal"]["metadata"]["status"] == "cancelled"


def test_terminal_snapshot_keeps_integration_identity_without_response_body():
    journal = RunEventJournal()
    journal.publish(RunEvent(type="done", usage={"total_tokens": 12}, metadata={
        "run_id": "run_one", "integration_id": "integration_one", "status": "completed",
        "text": "private response", "reasoning": "private reasoning",
    }))
    terminal = journal.snapshot()["terminal"]
    assert terminal["metadata"] == {
        "run_id": "run_one", "integration_id": "integration_one", "status": "completed",
        "web_event_seq": 1,
    }
    assert terminal["usage"] == {"total_tokens": 12}


def test_disconnect_preserves_run_guidance_quota_and_scoped_recovery(tmp_path):
    for user in ("alice", "bob"):
        (tmp_path / "users" / user).mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "global_config.yaml").write_text(json.dumps({
        "web": {"max_concurrent_chats": 1, "max_pending_chats": 0},
    }))
    release = threading.Event()
    seen = []

    def source(request, *, cancel_event, **_):
        yield RunEvent(type="text_delta", content="before disconnect")
        release.wait(3)
        seen.append(request["_guidance_queue"].get(timeout=1))
        yield RunEvent(type="text_delta", content="after disconnect")
        yield RunEvent(type="done", metadata={"status": "completed"})

    service = WebRunService(tmp_path, event_source=source)
    stream = service.stream_chat("alice", "s1", "go", run_id="run_resume",
                                 cancel_event=threading.Event())
    try:
        first = next(stream)
        stream.close()
        assert not service._active_runs["run_resume"].cancel_event.is_set()
        assert service.chat_gate_status()["alice"]["active_chats"] == 1
        snapshot = require_present(service.current_run("alice", "s1")["run"])
        assert snapshot["run_id"] == "run_resume"
        assert snapshot["events"][0]["content"] == "before disconnect"
        assert service.current_run("bob", "s1")["run"] is None
        for user, source_name, session in (("bob", "web", "s1"), ("alice", "app", "s1"), ("alice", "web", "s2")):
            with pytest.raises(NotFoundError):
                service.run_snapshot(user, "run_resume", source=source_name, session_id=session)
        assert service.submit_guidance("alice", "run_resume", "continue carefully", session_id="s1")["ok"]
        with pytest.raises(TooManyChatsError):
            service.stream_chat("alice", "s2", "new", run_id="run_extra",
                                cancel_event=threading.Event())
        release.set()
        deadline = time.monotonic() + 3
        while "run_resume" in service._active_runs and time.monotonic() < deadline:
            time.sleep(.005)
        assert "run_resume" not in service._active_runs
        assert service.chat_gate_status()["alice"]["active_chats"] == 0
        result = service.run_events("alice", "run_resume", session_id="s1",
                                    after=first.metadata["web_event_seq"])
        assert [event["type"] for event in result["events"]] == ["text_delta", "done"]
        assert result["finished"] and seen == ["continue carefully"]
    finally:
        release.set()
