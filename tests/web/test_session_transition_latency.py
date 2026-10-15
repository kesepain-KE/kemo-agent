from __future__ import annotations

import asyncio
import copy
import json
import threading
from unittest.mock import Mock, patch

import httpx
import pytest

from run.history import (
    close_session, commit_window, empty_window, find_record, load_window,
    queue_summary, reserve_session,
)
from run.history import registry_store, store_core
from run.history.store import mutate_registry_record, read_active_binding, write_registry
from web.app import create_app
from web.service import WebRunService


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "global_config.json").write_text(
        json.dumps({"memory": {"extraction_mode": "compression_only"}}), encoding="utf-8")
    for user in ("alice", "bob"):
        (tmp_path / "users" / user).mkdir(parents=True)
        (tmp_path / "users" / user / "user_config.json").write_text("{}", encoding="utf-8")
    return tmp_path


def archive(root, session="old"):
    window = empty_window("alice", "web", session)
    for n in range(12):
        window["text"]["messages"].extend([
            {"role": "user", "content": f"Fixture {n} " + "x" * 20_000},
            {"role": "assistant", "content": "Fixture response " + "y" * 20_000},
        ])
    window["data"].update(rounds=12, memory_status="deferred", memory_processed_round=0)
    path = root / "users" / "alice" / "history" / f"conv_{session}"
    commit_window(path, window)
    return path


def test_close_and_summary_only_touch_the_target_registry_row(root):
    base = reserve_session(root, "alice", "web", "old", active_key="interactive:alice:page")
    records = {f"web:fixture_{i}": {**copy.deepcopy(base), "session_id": f"fixture_{i}",
               "conversation_id": f"fixture_{i}", "future_marker": f"keep-{i}"}
               for i in range(250)}
    records["web:old"] = base
    write_registry(root, "alice", records, {
        "interactive:alice:page": {"source": "web", "session_id": "old"},
        "interactive:alice:other": {"source": "web", "session_id": "fixture_1"},
    }, updated_at=base["updated_at"])
    reserve_session(root, "alice", "app", "old", active_key="app:alice:device")
    reserve_session(root, "bob", "web", "old", active_key="interactive:bob")
    with patch("run.history.session_ops._load_index_unlocked",
               side_effect=AssertionError("Full registry scan is forbidden")):
        record = close_session(root, "alice", "web", "old")
        assert record["lifecycle"] == "closed"
        assert queue_summary(root, "alice", "web", "old")["reason"] == "no_archive_rounds"
    assert read_active_binding(root, "alice", "interactive:alice:page") is None
    assert read_active_binding(root, "alice", "interactive:alice:other")["session_id"] == "fixture_1"
    assert read_active_binding(root, "alice", "app:alice:device")["session_id"] == "old"
    assert find_record(root, "alice", "web", "fixture_1")["future_marker"] == "keep-1"
    assert find_record(root, "alice", "app", "old")["lifecycle"] == "open"
    assert find_record(root, "bob", "web", "old")["lifecycle"] == "open"


def test_save_queues_memory_without_reading_archive_transcripts(root):
    path = archive(root)
    statements = []
    configure = store_core._configure

    def trace(database, **kwargs):
        configure(database, **kwargs)
        database.set_trace_callback(statements.append)

    summary_waker = Mock()
    service = WebRunService(root, summary_waker=summary_waker)
    with (
        patch.object(store_core, "_configure", trace),
        patch("run.history.session_api.load_window", side_effect=AssertionError("No full archive load")),
        patch("run.history.commit_ops.load_stored_window", side_effect=AssertionError("No transcript reload")),
        patch("run.history.session_ops._load_index_unlocked", side_effect=AssertionError("No registry scan")),
        patch("web.services.run_control.extract_memory_backlog") as extract,
    ):
        result = service.close_session("alice", "old")
    assert result["closed"] is True
    assert result["memory"]["status"] == "queued"
    assert result["memory"]["target_round"] == 12
    assert result["summary"]["status"] == "queued"
    assert result["session"]["summary_status"] == "queued"
    extract.assert_not_called()
    summary_waker.assert_called_once()
    selects = [sql.lower() for sql in statements if sql.lstrip().lower().startswith("select")]
    assert not any("from history_messages" in sql or "from history_rounds" in sql for sql in selects)
    assert not any("text_json" in sql or "items_json" in sql for sql in selects)
    saved = load_window(path)
    assert len(saved["text"]["messages"]) == 24
    assert saved["text"]["messages"][0]["content"].endswith("x" * 20_000)
    assert saved["data"]["memory_status"] == "queued"
    assert saved["data"]["memory_target_round"] == 12
    assert saved["data"]["memory_processed_round"] == 0


def test_targeted_session_update_rolls_back_on_failure(root):
    reserve_session(root, "alice", "web", "old", active_key="interactive:alice")
    original = registry_store._upsert_session_row

    def fail_after_write(database, record):
        original(database, record)
        raise OSError("Fixture failure")

    with patch.object(registry_store, "_upsert_session_row", fail_after_write):
        with pytest.raises(OSError, match="Fixture failure"):
            close_session(root, "alice", "web", "old")
    assert find_record(root, "alice", "web", "old")["lifecycle"] == "open"
    assert read_active_binding(root, "alice", "interactive:alice")["session_id"] == "old"


def test_targeted_update_cannot_change_identity_or_revive_deleted_session(root):
    reserve_session(root, "alice", "web", "old")
    with pytest.raises(ValueError, match="source/session_id"):
        mutate_registry_record(root, "alice", "web", "old",
                               lambda r: {**r, "session_id": "different"}, updated_at="")
    assert find_record(root, "alice", "web", "old")["lifecycle"] == "open"
    assert find_record(root, "alice", "web", "different") is None
    from run.history import delete_session
    delete_session(root, "alice", "web", "old")
    assert close_session(root, "alice", "web", "old") is None
    assert queue_summary(root, "alice", "web", "old")["reason"] == "session_not_found"


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
            release.wait(5)
            return {"fixture": "complete"}
        finally:
            finished.set()

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            slow_request = asyncio.create_task(client.get(path))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                assert not finished.is_set(), "Slow read blocked the ASGI event loop"
                created = await asyncio.wait_for(client.post("/api/users/alice/sessions",
                    json={"client_id": "fixture_client_123"}), timeout=2)
                assert created.status_code == 200, created.text
                assert created.json()["created"] is True
                assert created.json()["active_clients"] == 1
                assert not finished.is_set(), "Creation waited for unrelated page aggregation"
            finally:
                release.set()
                await slow_request
    with patch.object(service, method, blocked_read):
        asyncio.run(check())
