"""Isolated pin/favorite storage, API permissions and cross-page ordering."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from run.history import reserve_session
from run.history.registry_store import mutate_registry_record
from run.history.store_core import connection
from web.app import create_app
from web.service import WebRunService


@pytest.fixture
def service(tmp_path: Path) -> WebRunService:
    for user in ("alice", "bob"):
        (tmp_path / "users" / user / "history").mkdir(parents=True)
    return WebRunService(tmp_path)


def seed(service, source="web", session_id="shared", user="alice", timestamp="2026-10-20T10:00:00+00:00"):
    reserve_session(service.root, user, source, session_id)
    with connection(service.root, user, write=True) as db:
        row = db.execute("SELECT record_json FROM history_sessions WHERE source=? AND session_id=?",
                         (source, session_id)).fetchone()
        record = json.loads(row[0])
        record["updated_at"] = timestamp
        db.execute("UPDATE history_sessions SET updated_at=?, record_json=? WHERE source=? AND session_id=?",
                   (timestamp, json.dumps(record), source, session_id))


def request(service, body, source="web", session_id="shared"):
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service=service)),
                                     base_url="http://test") as client:
            return await client.patch(f"/api/users/alice/sessions/{session_id}/marks",
                                      params={"source": source}, json=body)
    return asyncio.run(invoke())


def test_marks_survive_run_upserts_and_do_not_change_lifecycle_or_time(service):
    seed(service)
    before = service.sessions("alice")["sessions"][0]
    marked = service.mark_session("alice", "shared", pinned=True, favorite=True)["session"]
    assert marked["pinned"] and marked["favorite"]
    assert marked["updated_at"] == before["updated_at"]
    assert marked["state"] == before["state"]
    assert marked["summary_status"] == before["summary_status"]
    assert service.mark_session("alice", "shared", pinned=True)["session"]["pinned_at"] == marked["pinned_at"]
    mutate_registry_record(service.root, "alice", source="web", session_id="shared",
                           updated_at=before["updated_at"], mutator=lambda record: {**record, "title": "normal write"})
    restarted = WebRunService(service.root)
    assert restarted.sessions("alice")["sessions"][0]["favorite"]
    assert restarted.sessions("alice")["sessions"][0]["pinned_at"] == marked["pinned_at"]
    assert not restarted.mark_session("alice", "shared", pinned=False)["session"]["pinned"]
    assert restarted.sessions("alice")["sessions"][0]["favorite"]


def test_filters_and_order_apply_before_pagination_and_counts_are_global(service):
    for index in range(107):
        seed(service, session_id=f"s-{index:03}")
    for session_id in ("s-000", "s-050", "s-106"):
        service.mark_session("alice", session_id, favorite=True)
    service.mark_session("alice", "s-000", pinned=True)
    service.mark_session("alice", "s-050", pinned=True)
    found, cursor = [], ""
    while True:
        page = service.sessions("alice", source="all", before=cursor, limit=7)
        found.extend(item["session_id"] for item in page["sessions"])
        assert page["favorites_count"] == 3
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert len(found) == len(set(found)) == 107
    assert found[:2] == ["s-050", "s-000"]
    favorites = service.sessions("alice", favorites_only=True, limit=2)
    assert [item["session_id"] for item in favorites["sessions"]] == ["s-050", "s-000"]
    assert favorites["has_more"]
    assert service.sessions("alice", favorites_only=True, before=favorites["next_cursor"])["sessions"][0]["session_id"] == "s-106"
    assert service.sessions("alice", archive_date="2026-10-21", favorites_only=True)["sessions"] == []
    assert service.sessions("alice", archive_date="2026-10-21")["favorites_count"] == 3
    assert service.sessions("alice", query="s-106", favorites_only=True)["sessions"][0]["session_id"] == "s-106"
    service.mark_session("alice", "s-000", pinned=False)
    assert service.sessions("alice", limit=3)["sessions"][1]["session_id"] == "s-106"


@pytest.mark.parametrize("source", ["web", "app", "cli", "message:nya_desk_pet", "background:cron:cron_1234abcd"])
def test_marks_allow_readonly_sources_but_are_scoped_and_cascade_deleted(service, source):
    seed(service, source)
    seed(service, user="bob", source=source)
    response = request(service, {"pinned": True, "favorite": True}, source)
    assert response.status_code == 200, response.text
    assert response.json()["session"]["favorite"]
    assert not service.sessions("bob", source="all")["sessions"][0]["favorite"]
    service.delete_all_sessions("alice", source="all")
    assert request(service, {"favorite": True}, source).status_code == 404
    assert service.sessions("alice", source="all")["favorites_count"] == 0
    with connection(service.root, "alice") as db:
        assert db.execute("SELECT COUNT(*) FROM history_session_marks").fetchone()[0] == 0
    seed(service, source)
    assert not service.sessions("alice", source="all")["sessions"][0]["pinned"]


@pytest.mark.parametrize("body", [{}, {"favorite": None}, {"favorite": "true"},
                                {"pinned": 1}, {"favorite": True, "title": "bypass"}])
def test_invalid_marks_payloads_rejected(service, body):
    seed(service)
    assert request(service, body).status_code == 400
    assert not service.sessions("alice")["sessions"][0]["favorite"]


def test_source_identity_and_user_isolation(service):
    seed(service, "web")
    seed(service, "cli")
    service.mark_session("alice", "shared", source="cli", favorite=True)
    page = service.sessions("alice", source="all", favorites_only=True)
    assert len(page["sessions"]) == 1 and page["sessions"][0]["source"] == "cli"
    assert request(service, {"favorite": True}, "all").status_code == 400
