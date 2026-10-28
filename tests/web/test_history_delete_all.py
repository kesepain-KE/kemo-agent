"""Bulk history deletion uses isolated users, never the running instance."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import threading
from unittest.mock import patch

import httpx
import pytest

from run.conversation import session_lock
from run.history import (
    commit_window, empty_window, load_window, reserve_session,
    runtime_window_path, window_exists,
)
from run.history.bulk_deletion import delete_user_history
from run.history.store_core import connection, database_path
from run.history.web_lifecycle import touch_session_lease
from web.app import create_app
from web.errors import ConflictError
from web.service import ActiveRun, WebRunService


@pytest.fixture
def service(tmp_path: Path) -> WebRunService:
    for user in ("alice", "bob"):
        (tmp_path / "users" / user / "history").mkdir(parents=True)
    return WebRunService(tmp_path)


def seed(root: Path, user: str, source: str, name: str, session_id: str = "shared") -> Path:
    directory = root / "users" / user / "history" / name
    window = empty_window(user, source, session_id)
    window["text"]["messages"] = [
        {"role": "user", "content": f"isolated {name}"},
        {"role": "assistant", "content": "isolated response"},
    ]
    window["data"]["rounds"] = 1
    window["think"]["rounds"] = [{"round": 1, "content": "isolated reasoning"}]
    window["tool"]["rounds"] = [{"round": 1, "calls": []}]
    commit_window(directory, window)
    commit_window(runtime_window_path(directory), window)
    return directory


def request(service: WebRunService, url: str, method: str = "DELETE") -> httpx.Response:
    async def invoke() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(service=service))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, url)
    return asyncio.run(invoke())


def test_all_deletes_every_source_and_reservation_with_exact_counts(service):
    root = service.root
    sources = ["web", "app", "cli", "message:nya_desk_pet", "background:cron:cron_1234abcd", "direct_api"]
    windows = [seed(root, "alice", source, f"window-{i}") for i, source in enumerate(sources)]
    seed(root, "alice", "web", "second-window")
    reserve_session(root, "alice", "cli", "reserved-only", active_key="cli:alice")
    bob = seed(root, "bob", "web", "bob-window")
    attachment = root / "users/alice/download/keep.txt"
    attachment.parent.mkdir()
    attachment.write_text("keep", encoding="utf-8")
    memory = root / "users/alice/improve/memory.sqlite3"
    memory.parent.mkdir()
    memory.write_bytes(b"isolated memory sentinel")
    cron = root / "cron/isolated-task.json"
    cron.parent.mkdir()
    cron.write_text('{"enabled":false}', encoding="utf-8")
    with connection(root, "alice", write=True) as database:
        database.execute("""INSERT INTO history_context_summaries(
            window_name, source, session_id, schema_version, summary_json, created_at, updated_at
            ) VALUES('window-0', 'web', 'shared', 1, '{}', datetime('now'), datetime('now'))""")
    service._session_leases[("alice", "web", "shared")] = {}
    service._session_leases[("bob", "web", "shared")] = {}

    response = request(service, "/api/users/alice/sessions?source=all&client_id=browser-own")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "user": "alice", "source": "all", "deleted": True,
        "deleted_sessions": 7, "deleted_windows": 14,
    }
    assert service.sessions("alice", source="all")["sessions"] == []
    assert window_exists(bob)
    assert attachment.read_text("utf-8") == "keep"
    assert memory.read_bytes() == b"isolated memory sentinel"
    assert cron.read_text("utf-8") == '{"enabled":false}'
    assert ("alice", "web", "shared") not in service._session_leases
    assert ("bob", "web", "shared") in service._session_leases
    with connection(root, "alice") as database:
        for table in ("history_sessions", "history_active_sessions", "history_windows",
                      "history_messages", "history_rounds", "history_context_summaries",
                      "history_web_leases"):
            assert database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM history_deleted_sessions").fetchone()[0] == 7
        assert database.execute("SELECT COUNT(*) FROM history_deleted_windows").fetchone()[0] == 14
    for window in windows:
        assert not window_exists(window)


def test_default_still_only_deletes_web_and_readonly_single_mutation_is_rejected(service):
    web = seed(service.root, "alice", "web", "web-window")
    readonly = seed(service.root, "alice", "message:nya_desk_pet", "readonly-window")
    response = request(service, "/api/users/alice/sessions")
    assert response.status_code == 200
    assert response.json()["source"] == "web"
    assert not window_exists(web) and window_exists(readonly)
    rejected = request(service, "/api/users/alice/sessions/shared?source=message:nya_desk_pet")
    assert rejected.status_code == 400
    assert window_exists(readonly)


def test_only_readonly_and_more_than_one_page_are_deleted(service):
    for i in range(103):
        reserve_session(service.root, "alice", "message:nya_desk_pet", f"session-{i}")
    page = service.sessions("alice", source="all", limit=50)
    assert len(page["sessions"]) == 50 and page["has_more"]
    result = service.delete_all_sessions("alice", source="all")
    assert result["deleted_sessions"] == 103
    assert service.sessions("alice", source="all")["sessions"] == []


@pytest.mark.parametrize("source", ["app", "cli", "message:nya_desk_pet", "background:cron:cron_1234abcd"])
def test_active_readonly_run_rejects_entire_batch(service, source):
    web = seed(service.root, "alice", "web", "web-window")
    readonly = seed(service.root, "alice", source, "readonly-window")
    service._active_runs["busy"] = ActiveRun("busy", "alice", "shared", source=source)
    response = request(service, "/api/users/alice/sessions?source=all")
    assert response.status_code == 409
    assert window_exists(web) and window_exists(readonly)


@pytest.mark.parametrize("field,value", [
    ("run_state", "running"), ("run_state", "queued"), ("run_state", "pending"),
    ("run_state", "stopping"), ("memory_status", "processing"), ("summary_status", "processing"),
])
def test_durable_other_process_state_is_rechecked_before_any_delete(service, field, value):
    web = seed(service.root, "alice", "web", "web-window")
    readonly = seed(service.root, "alice", "cli", "readonly-window")
    with connection(service.root, "alice", write=True) as database:
        database.execute(f"UPDATE history_sessions SET {field}=? WHERE source='cli'", (value,))
    response = request(service, "/api/users/alice/sessions?source=all")
    assert response.status_code == 409
    assert window_exists(web) and window_exists(readonly)
    with connection(service.root, "alice") as database:
        assert database.execute("SELECT COUNT(*) FROM history_deleted_sessions").fetchone()[0] == 0


@pytest.mark.parametrize("source", ["web", "app"])
def test_other_live_browser_or_device_lease_blocks_all_even_same_session_id(service, source):
    web = seed(service.root, "alice", "web", "web-window")
    app = seed(service.root, "alice", "app", "app-window")
    assert touch_session_lease(service.root, "alice", "web", "shared", "browser-own")
    other = "browser-other" if source == "web" else "browser-own"
    assert touch_session_lease(service.root, "alice", source, "shared", other)
    response = request(service, "/api/users/alice/sessions?source=all&client_id=browser-own")
    assert response.status_code == 409
    assert window_exists(web) and window_exists(app)


def test_current_browser_and_expired_other_lease_can_be_deleted(service):
    seed(service.root, "alice", "web", "web-window")
    assert touch_session_lease(service.root, "alice", "web", "shared", "browser-own")
    assert touch_session_lease(service.root, "alice", "web", "shared", "expired", now=0)
    assert request(service, "/api/users/alice/sessions?source=all&client_id=browser-own").status_code == 200
    result = request(service, "/api/users/alice/sessions?source=all&client_id=browser-own")
    assert result.json()["deleted_sessions"] == 0


def test_busy_execution_lock_does_not_wait_or_partially_delete(service):
    web = seed(service.root, "alice", "web", "web-window")
    readonly = seed(service.root, "alice", "cli", "readonly-window")
    ready, release = threading.Event(), threading.Event()

    def hold_lock():
        with session_lock(service.root, "alice", "cli", "shared"):
            ready.set()
            release.wait(10)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    try:
        assert ready.wait(3)
        with pytest.raises(ConflictError):
            service.delete_all_sessions("alice", source="all")
        assert window_exists(web) and window_exists(readonly)
    finally:
        release.set()
        thread.join(3)


def test_sql_failure_rolls_back_windows_registry_leases_and_tombstones(service):
    web = seed(service.root, "alice", "web", "web-window")
    readonly = seed(service.root, "alice", "cli", "readonly-window")
    assert touch_session_lease(service.root, "alice", "web", "shared", "browser-own")
    with connection(service.root, "alice", write=True) as database:
        database.execute("""CREATE TRIGGER isolated_fail_delete BEFORE DELETE ON history_sessions
            BEGIN SELECT RAISE(ABORT, 'isolated failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="isolated failure"):
        delete_user_history(service.root, "alice", client_id="browser-own")
    assert window_exists(web) and window_exists(readonly)
    with connection(service.root, "alice") as database:
        assert database.execute("SELECT COUNT(*) FROM history_deleted_sessions").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM history_deleted_windows").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM history_web_leases").fetchone()[0] == 1
    with connection(service.root, "alice", write=True) as database:
        database.execute("DROP TRIGGER isolated_fail_delete")
    assert service.delete_all_sessions("alice", source="all", client_id="browser-own")["deleted_sessions"] == 2


def test_late_writer_cannot_resurrect_deleted_readonly_or_reserved_only_session(service):
    directory = seed(service.root, "alice", "message:nya_desk_pet", "readonly-window")
    stale = load_window(directory)
    record = reserve_session(service.root, "alice", "cli", "reserved-only")
    service.delete_all_sessions("alice", source="all")
    # Terminal writers intentionally finish quietly; their writes are fenced
    # rather than resurrecting the deleted identity.
    with pytest.raises(RuntimeError, match="已删除|冲突"):
        commit_window(directory, stale)
    assert not window_exists(directory)
    reserved = empty_window("alice", "cli", "reserved-only",
                            session_generation=record["session_generation"])
    with pytest.raises(RuntimeError, match="已删除|冲突"):
        commit_window(directory.with_name("late-window"), reserved)
    assert not window_exists(directory.with_name("late-window"))
    assert service.sessions("alice", source="all")["sessions"] == []


def test_all_sources_are_selected_inside_write_transaction_not_from_earlier_window_list(service):
    seed(service.root, "alice", "web", "web-window")
    original = delete_user_history

    def insert_then_delete(root, user, *, client_id=""):
        seed(root, user, "message:nya_desk_pet", "newly-arrived")
        return original(root, user, client_id=client_id)

    with patch("run.history.bulk_deletion.delete_user_history", side_effect=insert_then_delete):
        result = service.delete_all_sessions("alice", source="all")
    assert result["deleted_sessions"] == 2
    assert service.sessions("alice", source="all")["sessions"] == []


def test_empty_user_does_not_create_history_database_and_unknown_user_is_rejected(service):
    assert not database_path(service.root, "alice").exists()
    response = request(service, "/api/users/alice/sessions?source=all")
    assert response.status_code == 200 and response.json()["deleted_sessions"] == 0
    assert not database_path(service.root, "alice").exists()
    assert request(service, "/api/users/unknown/sessions?source=all").status_code == 404
