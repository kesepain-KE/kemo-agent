"""Session transitions use metadata-only writes and do not block unrelated page work."""
from contextlib import contextmanager
import asyncio
import json
import threading
import time
from unittest.mock import Mock, patch

import httpx
import pytest

from run.cards import CardStore
from run.history import (close_session, delete_session, find_record, queue_summary, reserve_session)
from run.history import registry_store
from run.history.store import mutate_registry_record, read_active_binding
from tests.card_history_support import seed_history
from web.app import create_app
from web.service import WebRunService


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.yaml").write_text('{"memory":{"extraction_mode":"compression_only"}}')
    for user in ("alice", "bob"):
        (tmp_path / f"users/{user}").mkdir(parents=True)
        (tmp_path / f"users/{user}/user_config.yaml").write_text('{}')
    return tmp_path


def archive(root):
    return seed_history(root, "alice", "web", "old", [message for n in range(12) for message in (
        {"role": "user", "content": f"Fixture {n} " + "x" * 20000},
        {"role": "assistant", "content": "Fixture response " + "y" * 20000})])


def test_close_and_summary_only_touch_target_metadata(root):
    for n in range(30):
        reserve_session(root, "alice", "web", f"fixture_{n}", future_marker=f"keep-{n}")
    reserve_session(root, "alice", "web", "old", active_key="interactive:alice:page")
    reserve_session(root, "alice", "app", "old", active_key="app:alice:device")
    reserve_session(root, "bob", "web", "old")
    assert close_session(root, "alice", "web", "old")["lifecycle"] == "closed"
    assert queue_summary(root, "alice", "web", "old")["status"] == "none"
    assert read_active_binding(root, "alice", "interactive:alice:page") is None
    assert read_active_binding(root, "alice", "app:alice:device")["session_id"] == "old"
    assert find_record(root, "alice", "web", "fixture_1")["future_marker"] == "keep-1"
    assert find_record(root, "alice", "app", "old")["lifecycle"] == "open"
    assert find_record(root, "bob", "web", "old")["lifecycle"] == "open"


def test_save_queues_memory_without_reading_raw_transcripts(root):
    history, statements = archive(root), []
    before = history.cards
    original = CardStore.db
    @contextmanager
    def trace(store, *, write=False):
        with original(store, write=write) as db:
            db.set_trace_callback(statements.append)
            yield db
    summary_waker, memory_waker = Mock(), Mock()
    service = WebRunService(root, summary_waker=summary_waker, memory_waker=memory_waker)
    with patch.object(CardStore, "db", trace):
        result = service.close_session("alice", "old")
    assert result["closed"] and result["memory"]["status"] == "queued"
    assert result["memory"]["target_seq"] == 24
    assert result["summary"]["status"] == "queued"
    assert not any("from context_cards" in sql.casefold() for sql in statements)
    assert history.cards == before and find_record(root, "alice", "web", "old")["memory_processed_seq"] == 0
    summary_waker.assert_called_once()
    memory_waker.assert_called_once()


def test_other_process_presence_defers_save_and_does_not_wake(root):
    archive(root)
    WebRunService(root).session_lease("alice", "old", "other_page")
    summary_waker, memory_waker = Mock(), Mock()
    service = WebRunService(root, summary_waker=summary_waker, memory_waker=memory_waker)
    result = service.close_session("alice", "old", client_id="saving_page")
    assert not result["closed"] and result["deferred"] and result["active_clients"] == 1
    summary_waker.assert_not_called()
    memory_waker.assert_not_called()
    with patch("run.history.web_lifecycle.time.time", return_value=time.time() + 91):
        assert service.close_session("alice", "old", client_id="saving_page")["closed"]
    summary_waker.assert_called_once()
    memory_waker.assert_called_once()


def test_empty_save_does_not_wake_workers(root):
    reserve_session(root, "alice", "web", "empty")
    summary_waker, memory_waker = Mock(), Mock()
    result = WebRunService(root, summary_waker=summary_waker, memory_waker=memory_waker).close_session("alice", "empty")
    assert result["closed"] and result["memory"]["status"] == "skipped"
    summary_waker.assert_not_called()
    memory_waker.assert_not_called()


def test_targeted_metadata_update_rolls_back_bindings_on_failure(root):
    reserve_session(root, "alice", "web", "old", active_key="interactive:alice")
    original = registry_store.write_record
    def fail(db, row, record, **kwargs):
        original(db, row, record, **kwargs)
        raise OSError("fixture failure")
    with patch.object(registry_store, "write_record", side_effect=fail), pytest.raises(OSError):
        close_session(root, "alice", "web", "old")
    assert find_record(root, "alice", "web", "old")["lifecycle"] == "open"
    assert read_active_binding(root, "alice", "interactive:alice")["session_id"] == "old"


def test_metadata_update_cannot_change_identity_or_revive_hidden_session(root):
    reserve_session(root, "alice", "web", "old")
    with pytest.raises(ValueError):
        mutate_registry_record(root, "alice", "web", "old", lambda r: {**r, "session_id": "different"})
    delete_session(root, "alice", "web", "old")
    assert close_session(root, "alice", "web", "old") is None
    assert queue_summary(root, "alice", "web", "old") is None


@pytest.mark.parametrize(("method", "path"), [
    ("sessions", "/api/users/alice/sessions"),
    ("overview", "/api/users/alice/overview?session_id=old"),
    ("history", "/api/users/alice/sessions/old/history"),
    ("tasks", "/api/users/alice/tasks?session_id=old"),
])
def test_slow_page_reads_do_not_block_creating_a_session(root, method, path):
    service = WebRunService(root)
    app = create_app(service=service)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked_read(*args, **kwargs):
        started.set()
        try:
            release.wait(20)
            return ({"user": "alice", "summary": {"active_plans": 0, "waiting_plans": 0, "enabled_crons": 0, "completed_plans": 0}, "plans": [], "cron_tasks": [], "executions": []} if method == "tasks" else {"fixture": "complete"})
        finally:
            finished.set()

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            slow_request = asyncio.create_task(client.get(path))
            try:
                assert await asyncio.to_thread(started.wait, 6)
                assert not finished.is_set(), "Slow read blocked the ASGI event loop"
                created = await asyncio.wait_for(client.post("/api/users/alice/sessions",
                    json={"client_id": "fixture_client_123"}), timeout=6)
                assert created.status_code == 200, created.text
                assert created.json()["created"] is True
                assert created.json()["active_clients"] == 1
                assert not finished.is_set(), "Creation waited for unrelated page aggregation"
            finally:
                release.set()
                await slow_request
    with patch.object(service, method, blocked_read):
        asyncio.run(check())
