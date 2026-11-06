"""Bulk deletion hides CardStream identities atomically and preserves RAW."""
from __future__ import annotations

import asyncio
import sqlite3

import httpx
import pytest

from run.cards import CardSessionClosedError, CardStore, IntegrationStore, session_key
from run.history import delete_all_sessions, find_record, reserve_session
from run.history.web_lifecycle import touch_session_lease
from web.app import create_app
from web.service import ActiveRun, WebRunService


@pytest.fixture
def service(tmp_path):
    for user in ("alice", "bob"):
        (tmp_path / "users" / user).mkdir(parents=True)
    return WebRunService(tmp_path)


def seed(service, source="web", session_id="shared", user="alice"):
    reserve_session(service.root, user, source, session_id)
    store = CardStore(service.root, user)
    integration = IntegrationStore(store, source, session_id, "i_" + source + "_" + session_id)
    integration.start()
    store.append(source, session_id, integration.id, "user_text",
                 {"text": f"isolated {source} {session_id}"}, level="initial")
    integration.request_started()
    integration.request_finished(accepted_seq=store.snapshot(source, session_id).last_seq)
    integration.finish("completed", final_payload={"text": "isolated response"})
    return store


def request(service, url):
    async def invoke():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(service=service)),
            base_url="http://test",
        ) as client:
            return await client.delete(url)
    return asyncio.run(invoke())


def test_all_hides_every_source_and_reservation_but_preserves_raw_and_user_files(service):
    sources = ["web", "app", "cli", "message:nya_desk_pet",
               "background:cron:cron_1234abcd", "direct_api"]
    stores = [seed(service, source) for source in sources]
    reserve_session(service.root, "alice", "cli", "reserved-only", active_key="cli:alice")
    bob = seed(service, user="bob")
    before = {source: stores[0].raw_page(source, "shared") for source in sources}
    sentinels = {
        service.root / "users/alice/history/history.sqlite3": b"do not open old history",
        service.root / "users/alice/history/window.json": b'{"legacy":"keep"}',
        service.root / "users/alice/download/keep.txt": b"keep attachment",
        service.root / "users/alice/improve/memory.sqlite3": b"keep memory",
        service.root / "cron/isolated-task.json": b'{"enabled":false}',
    }
    for path, body in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    service._session_leases[("alice", "web", "shared")] = {}
    service._session_leases[("bob", "web", "shared")] = {}

    response = request(service, "/api/users/alice/sessions?source=all&client_id=browser-own-123")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "user": "alice", "source": "all", "deleted": True,
        "deleted_sessions": 7, "raw_preserved": True,
    }
    assert service.sessions("alice", source="all")["sessions"] == []
    assert bob.session("web", "shared")["lifecycle"] == "open"
    for source in sources:
        assert stores[0].raw_page(source, "shared") == before[source]
        assert stores[0].session(source, "shared")["lifecycle"] == "deleted"
    for path, body in sentinels.items():
        assert path.read_bytes() == body
    assert ("alice", "web", "shared") not in service._session_leases
    assert ("bob", "web", "shared") in service._session_leases
    with stores[0].db() as db:
        assert db.execute("SELECT count(*) FROM context_cards").fetchone()[0] == 12
        assert db.execute("SELECT count(*) FROM card_bindings").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM card_leases").fetchone()[0] == 0


def test_default_only_hides_web_and_readonly_single_mutation_is_rejected(service):
    store = seed(service)
    seed(service, "message:nya_desk_pet")
    response = request(service, "/api/users/alice/sessions")
    assert response.status_code == 200 and response.json()["source"] == "web"
    assert store.session("web", "shared")["lifecycle"] == "deleted"
    assert store.session("message:nya_desk_pet", "shared")["lifecycle"] == "open"
    rejected = request(service, "/api/users/alice/sessions/shared?source=message:nya_desk_pet")
    assert rejected.status_code == 400


def test_all_sources_selects_beyond_the_first_listing_page(service):
    for index in range(103):
        reserve_session(service.root, "alice", "message:nya_desk_pet", f"s-{index}")
    page = service.sessions("alice", source="all", limit=50)
    assert len(page["sessions"]) == 50 and page["has_more"]
    assert service.delete_all_sessions("alice", source="all")["deleted_sessions"] == 103
    assert service.sessions("alice", source="all")["sessions"] == []


@pytest.mark.parametrize("source", ["app", "cli", "message:nya_desk_pet",
                                  "background:cron:cron_1234abcd"])
def test_active_run_blocks_entire_batch(service, source):
    store = seed(service)
    seed(service, source)
    service._active_runs["busy"] = ActiveRun("busy", "alice", "shared", source=source)
    assert request(service, "/api/users/alice/sessions?source=all").status_code == 409
    assert store.session("web", "shared")["lifecycle"] == "open"
    assert store.session(source, "shared")["lifecycle"] == "open"


@pytest.mark.parametrize("closed_response", [False, True])
def test_durable_integration_or_unconfirmed_tool_blocks_entire_batch(service, closed_response):
    store = seed(service)
    reserve_session(service.root, "alice", "cli", "other")
    integration = IntegrationStore(store, "cli", "other", "i_live")
    integration.start()
    if closed_response:
        integration.register_tool("call-live", {"name": "write", "arguments": {}})
        integration.finish("cancelled")
    assert request(service, "/api/users/alice/sessions?source=all").status_code == 409
    assert store.session("web", "shared")["lifecycle"] == "open"
    assert store.session("cli", "other")["lifecycle"] == "open"


@pytest.mark.parametrize("source", ["web", "app"])
def test_other_live_document_lease_blocks_all_with_source_scoping(service, source):
    store = seed(service)
    seed(service, "app")
    assert touch_session_lease(service.root, "alice", source, "shared", "other-document")
    assert request(service, "/api/users/alice/sessions?source=all&client_id=client-mine-123").status_code == 409
    assert store.session("web", "shared")["lifecycle"] == "open"
    assert store.session("app", "shared")["lifecycle"] == "open"


def test_own_and_expired_other_leases_allow_idempotent_delete(service):
    store = seed(service)
    before = store.raw_page("web", "shared")
    assert touch_session_lease(service.root, "alice", "web", "shared", "client-mine-123")
    assert touch_session_lease(service.root, "alice", "web", "shared", "expired", now=0)
    response = request(service, "/api/users/alice/sessions?source=all&client_id=client-mine-123")
    assert response.status_code == 200 and response.json()["deleted_sessions"] == 1
    assert request(service, "/api/users/alice/sessions?source=all").json()["deleted_sessions"] == 0
    assert store.raw_page("web", "shared") == before


def test_transaction_failure_rolls_back_lifecycle_bindings_leases_and_raw(service):
    store = seed(service)
    seed(service, "cli")
    reserve_session(service.root, "alice", "web", "shared", active_key="browser")
    assert touch_session_lease(service.root, "alice", "web", "shared", "client-mine-123")
    before = store.raw_page("web", "shared")
    with store.db(write=True) as db:
        db.execute("""CREATE TRIGGER isolated_fail_hide BEFORE UPDATE OF lifecycle ON card_sessions
            WHEN NEW.lifecycle='deleted' AND NEW.source='cli'
            BEGIN SELECT RAISE(ABORT,'isolated failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="isolated failure"):
        delete_all_sessions(service.root, "alice", client_id="client-mine-123")
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM card_sessions WHERE lifecycle='deleted'").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM card_bindings").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM card_leases").fetchone()[0] == 1
    assert store.raw_page("web", "shared") == before
    with store.db(write=True) as db:
        db.execute("DROP TRIGGER isolated_fail_hide")
    assert service.delete_all_sessions("alice", source="all", client_id="client-mine-123")["deleted_sessions"] == 2


def test_stale_writer_cannot_resurrect_a_deleted_identity(service):
    store = seed(service, "message:nya_desk_pet")
    before = store.raw_page("message:nya_desk_pet", "shared")
    reserve_session(service.root, "alice", "cli", "reserved-only")
    service.delete_all_sessions("alice", source="all")
    with pytest.raises(CardSessionClosedError):
        store.append("message:nya_desk_pet", "shared", "late", "agent_text", {"text": "late"})
    with pytest.raises(CardSessionClosedError):
        reserve_session(service.root, "alice", "cli", "reserved-only")
    assert store.raw_page("message:nya_desk_pet", "shared") == before
    assert find_record(service.root, "alice", "message:nya_desk_pet", "shared") is None
    assert store.session("cli", "reserved-only")["session_key"] == session_key("cli", "reserved-only")


def test_empty_user_creates_no_card_database_and_unknown_user_is_rejected(service):
    path = CardStore(service.root, "alice").path
    assert not path.exists()
    response = request(service, "/api/users/alice/sessions?source=all")
    assert response.status_code == 200 and response.json()["deleted_sessions"] == 0
    assert not path.exists()
    assert request(service, "/api/users/unknown/sessions?source=all").status_code == 404
