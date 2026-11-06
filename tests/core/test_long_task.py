"""Default long tasks use one Run, one mailbox and the standard stop control."""
from __future__ import annotations

import threading

import pytest

from events import RunEvent
from run.conversation import GuidanceInput
from tests.media_input_support import make_root
from web.service import WebRunService


@pytest.mark.parametrize("status", ["completed", "partial", "limited", "cancelled"])
def test_web_publishes_one_engine_terminal_without_automatic_new_runs(tmp_path, status):
    make_root(tmp_path)
    requests = []

    def source(request, **kwargs):
        requests.append(request)
        for index in range(90):
            yield RunEvent(type="tool_call_result", tool_call_id=str(index),
                           result={"ok": True}, metadata={"index": index})
        yield RunEvent(type="done", metadata={"status": status, "committed": True})

    service = WebRunService(tmp_path, event_source=source)
    events = list(service.stream_chat("alice", "default-long", "work",
                  run_id="run_default_long", cancel_event=threading.Event()))
    assert len(requests) == 1
    assert len(events) == 91 and events[-1].metadata["status"] == status
    assert all(event.metadata["run_id"] == "run_default_long" for event in events)
    assert not service._active_runs
    assert not requests[0]["_guidance_queue"].accepting


def test_web_unconsumed_guidance_is_reported_at_terminal_not_forwarded_to_a_new_run(tmp_path):
    make_root(tmp_path)
    requests = []

    def source(request, **kwargs):
        requests.append(request)
        for name in ("first", "second"):
            assert request["_guidance_queue"].offer(GuidanceInput(id=name, text=name))[0]
        yield RunEvent(type="done", metadata={"status": "completed", "committed": True})

    service = WebRunService(tmp_path, event_source=source)
    events = list(service.stream_chat("alice", "pending", "work", cancel_event=threading.Event()))
    details = events[-1].metadata["guidance_details"]
    assert len(requests) == 1
    assert [item["id"] for item in details] == ["first", "second"]
    assert all(item["status"] == "not_applied" for item in details)


def test_web_missing_terminal_is_an_explicit_failure(tmp_path):
    make_root(tmp_path)

    def source(request, **kwargs):
        yield RunEvent(type="text_delta", content="partial body")

    service = WebRunService(tmp_path, event_source=source)
    events = list(service.stream_chat("alice", "missing", "work", cancel_event=threading.Event()))
    assert events[-1].type == "error"
    assert events[-1].error["code"] == "RUN_MISSING_TERMINAL"
    assert not service._active_runs
