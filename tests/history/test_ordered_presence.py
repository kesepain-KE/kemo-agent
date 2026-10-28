"""Ordered page transitions in isolated stores: no live server or model."""
from __future__ import annotations

import json
import sqlite3

import pytest

from run.history import reserve_session
from run.history import store_core
from run.history.lease_presence import other_presence, update_presence
from run.history.web_lifecycle import active_session_lease_clients, touch_session_lease
from web.service import WebRunService
from web.app import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.json").write_text(
        json.dumps({"memory": {"extraction_mode": "compression_only"}}), "utf-8")
    (tmp_path / "users/alice").mkdir(parents=True)
    (tmp_path / "users/alice/user_config.json").write_text("{}", "utf-8")
    reserve_session(tmp_path, "alice", "web", "conversation")
    return tmp_path


def test_normal_refresh_releases_old_document_but_preserves_real_peer(root):
    service = WebRunService(root)
    service.session_lease("alice", "conversation", "web_old_document", lease_sequence=1)
    service.release_session_lease("alice", "conversation", "web_old_document", lease_sequence=2)
    service.session_lease("alice", "conversation", "web_new_document", lease_sequence=1)
    service.session_lease("alice", "conversation", "web_other_window", lease_sequence=1)
    blocked = service.close_session("alice", "conversation", client_id="web_new_document")
    assert not blocked["closed"] and blocked["active_clients"] == 1
    assert 1 <= blocked["retry_after_seconds"] <= 90
    service.release_session_lease("alice", "conversation", "web_other_window", lease_sequence=2)
    assert service.close_session("alice", "conversation", client_id="web_new_document")["closed"]


def test_reordered_heartbeat_release_and_bfcache_resume_across_workers(root):
    one, two = WebRunService(root), WebRunService(root)
    one.session_lease("alice", "conversation", "web_document", lease_sequence=1)
    two.release_session_lease("alice", "conversation", "web_document", lease_sequence=3)
    old = one.session_lease("alice", "conversation", "web_document", lease_sequence=2)
    assert old["ignored"] and not old["leased"]
    assert not active_session_lease_clients(root, "alice", "web", "conversation")
    # A BFCache/StrictMode reactivation is newer, even if the old release arrives later.
    one.session_lease("alice", "conversation", "web_document", lease_sequence=5)
    late = two.release_session_lease("alice", "conversation", "web_document", lease_sequence=4)
    assert late["ignored"] and not late["released"]
    assert active_session_lease_clients(root, "alice", "web", "conversation") == ["web_document"]
    # Process one's stale in-memory entry must not override worker two's release.
    two.release_session_lease("alice", "conversation", "web_document", lease_sequence=6)
    assert one.close_session("alice", "conversation", client_id="web_another_document")["closed"]


def test_release_before_first_heartbeat_fences_pending_acquire(root):
    update_presence(root, "alice", "web", "conversation", "web_pending", 2, release=True)
    result = update_presence(root, "alice", "web", "conversation", "web_pending", 1)
    assert result == {"exists": True, "accepted": False, "active": False}
    assert not touch_session_lease(root, "alice", "web", "conversation", "web_pending")
    assert other_presence(root, "alice", "web", "conversation")["active_clients"] == 0
    with store_core.connection(root, "alice") as db:
        assert tuple(db.execute(
            "SELECT sequence,released FROM history_web_leases WHERE client_id='web_pending'"
        ).fetchone()) == (2, 1)


def test_lost_release_expires_naturally_and_other_source_remains_isolated(root):
    reserve_session(root, "alice", "app", "conversation")
    update_presence(root, "alice", "web", "conversation", "same_client", 1, now=100)
    update_presence(root, "alice", "app", "conversation", "same_client", 1, now=100)
    update_presence(root, "alice", "web", "conversation", "same_client", 2, release=True, now=110)
    assert other_presence(root, "alice", "web", "conversation", now=120)["active_clients"] == 0
    assert other_presence(root, "alice", "app", "conversation", now=120)["active_clients"] == 1
    assert other_presence(root, "alice", "app", "conversation", now=191)["active_clients"] == 0


def test_legacy_release_is_conservative_and_cannot_erase_modern_presence(root):
    service = WebRunService(root)
    service.session_lease("alice", "conversation", "legacy_client")
    service.release_session_lease("alice", "conversation", "legacy_client")
    assert active_session_lease_clients(root, "alice", "web", "conversation") == ["legacy_client"]
    service.session_lease("alice", "conversation", "modern_client", lease_sequence=1)
    service.release_session_lease("alice", "conversation", "modern_client")
    assert "modern_client" in active_session_lease_clients(root, "alice", "web", "conversation")


def test_tombstone_is_not_scheduler_presence_and_closed_session_cannot_revive(root):
    from run.scheduler.session_sweep import _session_lease_active
    service = WebRunService(root)
    service.session_lease("alice", "conversation", "web_document", lease_sequence=1)
    service.release_session_lease("alice", "conversation", "web_document", lease_sequence=2)
    import time
    assert not _session_lease_active(root, "alice", "web", "conversation", time.time())
    assert service.close_session("alice", "conversation", client_id="web_another")["closed"]
    assert not update_presence(root, "alice", "web", "conversation", "web_document", 3)["exists"]


def test_released_fence_expires_under_existing_bounded_cleanup(root):
    from run.history import cleanup_empty_web_sessions
    update_presence(root, "alice", "web", "conversation", "web_document", 1, release=True, now=100)
    cleanup_empty_web_sessions(root, "alice", now=191)
    with store_core.connection(root, "alice") as db:
        assert db.execute("SELECT COUNT(*) FROM history_web_leases").fetchone()[0] == 0


def test_single_delete_ignores_released_cross_worker_memory(root):
    one, two = WebRunService(root), WebRunService(root)
    one.session_lease("alice", "conversation", "web_document", lease_sequence=1)
    two.release_session_lease("alice", "conversation", "web_document", lease_sequence=2)
    assert one.delete_session("alice", "conversation")["deleted"]
    assert not update_presence(root, "alice", "web", "conversation", "web_document", 3)["exists"]


def test_bulk_delete_ignores_released_rows_but_protects_live_app(root):
    from run.history import delete_all_sessions, HistoryError
    reserve_session(root, "alice", "app", "conversation")
    update_presence(root, "alice", "web", "conversation", "web_document", 1, release=True)
    update_presence(root, "alice", "app", "conversation", "app_device", 1)
    with pytest.raises(HistoryError, match="其他页面或设备"):
        delete_all_sessions(root, "alice", None)
    update_presence(root, "alice", "app", "conversation", "app_device", 2, release=True)
    assert delete_all_sessions(root, "alice", None)[0] == 2
    with store_core.connection(root, "alice") as db:
        assert db.execute("SELECT COUNT(*) FROM history_web_leases").fetchone()[0] == 0


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "3", 9_007_199_254_740_992])
def test_http_rejects_invalid_sequence_without_writing(root, value):
    with TestClient(create_app(service=WebRunService(root)), base_url="http://127.0.0.1",
                    client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/users/alice/sessions/conversation/lease",
                               json={"client_id": "web_invalid", "lease_sequence": value})
        assert response.status_code == 400
    assert active_session_lease_clients(root, "alice", "web", "conversation") == []


def test_http_ignores_stale_request_without_reporting_missing_session(root):
    with TestClient(create_app(service=WebRunService(root)), base_url="http://127.0.0.1",
                    client=("127.0.0.1", 50000)) as client:
        base = "/api/users/alice/sessions/conversation/lease"
        assert client.post(base + "/release", json={
            "client_id": "web_document", "lease_sequence": 2}).json()["released"]
        response = client.post(base, json={"client_id": "web_document", "lease_sequence": 1})
        assert response.status_code == 200
        assert response.json()["ignored"] and not response.json()["leased"]


@pytest.mark.parametrize("fail", [False, True])
def test_v7_migration_backs_up_lease_rows_and_rolls_back_atomically(tmp_path, monkeypatch, fail):
    path = tmp_path / "users/alice/history/history.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE history_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO history_meta VALUES('schema_version','7')")
        db.execute("CREATE TABLE history_web_leases(session_id TEXT NOT NULL,client_id TEXT NOT NULL,"
                   "expires_at REAL NOT NULL,PRIMARY KEY(session_id,client_id))")
        db.execute("INSERT INTO history_web_leases VALUES('keep','legacy',1234567890)")
    if fail:
        def broken(*args):
            raise RuntimeError("injected migration failure")
        monkeypatch.setattr(store_core, "_initialize_schema", broken)
        with pytest.raises(RuntimeError, match="injected"):
            store_core._ensure_database(path)
    else:
        store_core._ensure_database(path)
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT * FROM history_web_leases").fetchone()
        assert row == (("keep", "legacy", 1234567890.0) if fail
                       else ("keep", "legacy", 1234567890.0, 0, 0))
        assert db.execute("SELECT value FROM history_meta WHERE key='schema_version'").fetchone()[0] == ("7" if fail else "8")
    backups = list(path.parent.glob(path.name + ".bak.*"))
    assert backups
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT * FROM history_web_leases").fetchone() == ("keep", "legacy", 1234567890.0)
