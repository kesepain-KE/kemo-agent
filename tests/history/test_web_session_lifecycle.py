"""Offline retirement uses CardStream lifecycle, actual execution and durable presence."""
import asyncio
import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from run.cards import CardConflictError, CardStore, IntegrationStore, session_key
from run.conversation import session_lock
from run.history import (cleanup_empty_web_sessions, close_session, delete_session, find_record,
                         inspect_stale_web_sessions, mutate_registry_record, reopen_session,
                         reserve_session, touch_web_session_lease)
from run.history import web_lifecycle
from run.history.lease_presence import update_presence
from run.scheduler import MaintenanceScheduler, sweep_idle_sessions
from tests.card_history_support import seed_history
from tests.type_assertions import require_present
from web.app import create_app
from web.errors import NotFoundError
from web.service import WebRunService


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.yaml").write_text(json.dumps({
        "memory": {"extraction_mode": "compression_only"}, "cron": {"session_idle_close_seconds": 3600}}))
    for user in ("alice", "bob"):
        (tmp_path / f"users/{user}").mkdir(parents=True)
        (tmp_path / f"users/{user}/user_config.yaml").write_text('{}')
    return tmp_path


def seed(root, sid="empty", *, data=False, source="web", user="alice", age=4000):
    if data:
        history = seed_history(root, user, source, sid, [
            {"role": "user", "content": "保留的事实"}, {"role": "assistant", "content": "完成"}])
    else:
        reserve_session(root, user, source, sid, active_key=f"{source}:{user}:{sid}")
        history = None
    stamp = datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat()
    mutate_registry_record(root, user, source, sid, lambda r: r, updated_at=stamp)
    return history


def test_offline_empty_retirement_is_user_source_scoped_and_fences_late_leases(root):
    seed(root)
    seed(root, "other", user="bob")
    seed(root, "app", source="app")
    seed(root, "cron", source="background:cron:job")
    reserve_session(root, "alice", "web", "newborn")
    assert cleanup_empty_web_sessions(root, "alice")["deleted_sessions"] == ["empty"]
    assert find_record(root, "alice", "web", "empty") is None
    assert not touch_web_session_lease(root, "alice", "empty", "late")
    assert find_record(root, "alice", "app", "app")
    assert find_record(root, "bob", "web", "other")
    assert find_record(root, "alice", "web", "newborn")


def test_multi_tab_presence_survives_service_restart_and_expires(root):
    seed(root)
    one, two = WebRunService(root), WebRunService(root)
    one.session_lease("alice", "empty", "client_one")
    two.session_lease("alice", "empty", "client_two")
    one.release_session_lease("alice", "empty", "client_one")
    assert cleanup_empty_web_sessions(root, "alice")["deleted_sessions"] == []
    assert WebRunService(root).cleanup_empty_web_sessions() == 0
    assert cleanup_empty_web_sessions(root, "alice", now=time.time() + 100)["deleted_sessions"] == ["empty"]
    with pytest.raises(NotFoundError):
        two.session_lease("alice", "empty", "client_two")


def test_closed_identity_requires_explicit_reopen_and_deleted_identity_stays_fenced(root):
    history = seed(root, "ended", data=True)
    assert history is not None
    before = history.cards
    close_session(root, "alice", "web", "ended")
    assert not touch_web_session_lease(root, "alice", "ended", "late")
    found_record = reopen_session(root, "alice", "web", "ended")
    assert found_record is not None
    assert found_record["lifecycle"] == "open"
    assert touch_web_session_lease(root, "alice", "ended", "page")
    assert history.cards == before
    delete_session(root, "alice", "web", "ended")
    assert reopen_session(root, "alice", "web", "ended") is None


def test_empty_grace_is_independent_of_data_idle_threshold(root):
    seed(root)
    seed(root, "data", data=True)
    result = inspect_stale_web_sessions(root, "alice", idle_seconds=7200)
    assert result["deleted_sessions"] == ["empty"] and result["queued_memory"] == []
    found_record = find_record(root, "alice", "web", "data")
    assert found_record is not None
    assert found_record["lifecycle"] == "open"


def test_cron_sweep_rechecks_lease_renewed_before_archive(root):
    sid, client = "stale-web", "web_racing_client"
    seed(root, sid, data=True, age=6000)
    now = time.time()
    update_presence(root, "alice", "web", sid, client, 1,
                    now=now - web_lifecycle.WEB_LEASE_SECONDS - 1)
    assert not web_lifecycle.active_session_lease_clients(root, "alice", "web", sid, now=now)
    original, renewed = CardStore.db, False

    @contextmanager
    def renew_before_write(store, *, write=False):
        nonlocal renewed
        # The cron candidate and its initial lease check are already read.
        # Commit the new heartbeat in another process before the first write.
        if write and not renewed:
            worker = get_context("spawn").Process(
                target=update_presence, args=(root, "alice", "web", sid, client, 2), kwargs={"now": now},
            )
            worker.start()
            worker.join(10)
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
            assert worker.exitcode == 0, "The lease writer did not exit successfully"
            assert web_lifecycle.active_session_lease_clients(root, "alice", "web", sid, now=now) == [client]
            renewed = True
        with original(store, write=write) as db:
            yield db

    with patch.object(CardStore, "db", renew_before_write):
        result = sweep_idle_sessions(root, "alice", idle_seconds=5400, now=now)

    assert renewed and not result["errors"], result
    assert require_present(find_record(root, "alice", "web", sid))["lifecycle"] == "open"
    assert result["closed"] == 0


@pytest.mark.parametrize("inspect", [cleanup_empty_web_sessions, inspect_stale_web_sessions])
def test_closed_empty_sessions_are_recycled_after_grace(root, inspect):
    seed(root, "closed-empty")
    close_session(root, "alice", "web", "closed-empty")
    seed(root, "closed-data", data=True)
    close_session(root, "alice", "web", "closed-data")
    now = time.time()

    assert inspect(root, "alice", now=now)["deleted_sessions"] == []
    result = inspect(root, "alice", now=now + 91)

    assert result["deleted_sessions"] == ["closed-empty"]
    assert find_record(root, "alice", "web", "closed-empty") is None
    record = find_record(root, "alice", "web", "closed-data")
    assert record is not None and record["last_seq"] == 2


def test_zero_card_session_with_execution_record_is_not_an_empty_draft(root):
    sid = "failed-before-first-card"
    seed(root, sid, age=0)
    seed(root, "blank-draft", age=0)
    store = CardStore(root, "alice")
    integration = IntegrationStore(store, "web", sid, "provider-startup")
    integration.start()
    integration.finish("failed")
    before = require_present(find_record(root, "alice", "web", sid))
    assert before["last_seq"] == 0 and before["integration_count"] == 1
    assert before["run_state"] == "idle"
    now = time.time()

    result = inspect_stale_web_sessions(
        root, "alice", now=now + web_lifecycle.WEB_EMPTY_GRACE_SECONDS + 1, idle_seconds=5400,
    )
    assert "blank-draft" in result["deleted_sessions"]
    assert require_present(store.session("web", sid))["lifecycle"] == "open", result

    inspect_stale_web_sessions(root, "alice", now=now + 5401, idle_seconds=5400)
    archived = require_present(find_record(root, "alice", "web", sid))
    assert archived["lifecycle"] == "closed"
    assert archived["session_generation"] == before["session_generation"]
    assert archived["integration_count"] == 1


@pytest.mark.parametrize("inspect", [inspect_stale_web_sessions, sweep_idle_sessions])
def test_recovered_orphan_can_archive_and_queue_missing_title_without_erasing_unknown_tool(root, inspect):
    from run.history import claim_pending_memory, finish_memory_claim

    history = seed(root, "orphan", data=True)
    assert history is not None
    store = history.store
    integration = IntegrationStore(store, "web", "orphan", "lost-owner")
    integration.start()
    execution, _ = integration.register_tool("unfinished-call", {"name": "shell", "arguments": {}})
    integration.finish("cancelled")
    with store.db(write=True) as db:
        db.execute("UPDATE card_integrations SET owner_pid=987654321,owner_token='lost' "
                   "WHERE integration_id='lost-owner'")
    with patch("run.infra.process_identity_matches", return_value=False):
        assert integration.recover_interrupted() == ["lost-owner"]

    claim = claim_pending_memory(root, "alice", source="web", session_id="orphan")
    assert claim is not None
    finish_memory_claim(root, "alice", "web", "orphan", claim_id=claim["memory_claim_id"])
    stamp = datetime.fromtimestamp(time.time() - 4000, timezone.utc).isoformat()
    mutate_registry_record(root, "alice", "web", "orphan", lambda r: r, updated_at=stamp)
    before = store.raw_page("web", "orphan")

    result = inspect(root, "alice", idle_seconds=3600)

    assert not result["errors"]
    record = require_present(find_record(root, "alice", "web", "orphan"))
    assert record["lifecycle"] == "closed"
    assert record["memory_processed_seq"] == record["last_seq"]
    assert record["memory_status"] == "completed"
    assert record["summary_status"] == "queued"
    assert record["summary_target_seq"] == record["last_seq"]
    assert store.raw_page("web", "orphan") == before
    reopened = require_present(reopen_session(root, "alice", "web", "orphan"))
    assert reopened["lifecycle"] == "open"
    store.close("web", "orphan")
    assert require_present(find_record(root, "alice", "web", "orphan"))["lifecycle"] == "closed"
    with pytest.raises(CardConflictError):
        store.close("web", "orphan", deleted=True)
    with pytest.raises(CardConflictError):
        delete_session(root, "alice", "web", "orphan")
    assert store.raw_page("web", "orphan") == before
    with store.db() as db:
        tool = db.execute("SELECT status,execution_live FROM card_tool_executions WHERE execution_id=?",
                          (execution["execution_id"],)).fetchone()
    assert tool["status"] == "interrupted_unknown" and tool["execution_live"] == 1


def test_active_integration_and_maintenance_claim_are_protected(root):
    history = seed(root, "live", data=True)
    assert history is not None
    integration = IntegrationStore(history.store, "web", "live", "live_id")
    integration.start()
    execution, _ = integration.register_tool("live_call", {"name": "work", "arguments": {}})
    integration.finish("cancelled")
    seed(root, "memory")
    mutate_registry_record(root, "alice", "web", "memory", lambda r: {**r,
        "memory_status": "processing", "memory_claim_expires_at": datetime.fromtimestamp(time.time() + 1000, timezone.utc).isoformat()})
    result = inspect_stale_web_sessions(root, "alice")
    assert not result["closed_sessions"] and not result["deleted_sessions"]
    assert result["skipped_busy"] >= 1
    integration.tool_result(execution["execution_id"], {"ok": True}, status="completed", duration_ms=1)


def _leave_provider_request_inflight(root, sid, integration_id):
    integration = IntegrationStore(CardStore(root, "alice"), "web", sid, integration_id)
    integration.start()
    integration.request_started()


def test_web_startup_inspection_reconciles_lost_provider_owner_without_replay(root):
    sid, integration_id = "lost-provider", "orphan-inflight"
    history = require_present(seed(root, sid, data=True))
    store = history.store
    old_host = get_context("spawn").Process(target=_leave_provider_request_inflight, args=(root, sid, integration_id))
    old_host.start()
    old_host.join(10)
    if old_host.is_alive():
        old_host.terminate()
        old_host.join(5)
    assert old_host.exitcode == 0, "The old host did not leave a durable request"
    with store.db() as db:
        before = db.execute("SELECT * FROM card_integrations WHERE integration_id=?", (integration_id,)).fetchone()
    assert before["owner_pid"] == old_host.pid
    assert (before["response_closed"], before["model_request_inflight"]) == (0, 1)
    cards = history.cards
    assert update_presence(root, "alice", "web", sid, "web_returning_page", 1)["active"]
    event_source = Mock()
    service = WebRunService(root, event_source=event_source)
    assert service.current_run("alice", sid)["run"] is None

    result = service.inspect_conversation_spaces_on_startup()

    event_source.assert_not_called()
    assert result["errors"] == 0, result
    with store.db() as db:
        after = db.execute("SELECT * FROM card_integrations WHERE integration_id=?", (integration_id,)).fetchone()
    assert (after["response_closed"], after["model_request_inflight"], after["status"]) == (1, 0, "failed")
    recovery = json.loads(after["record_json"])["recovery"]
    assert recovery["reason"] == "owner_lost" and not recovery["replay_permitted"]
    record = require_present(find_record(root, "alice", "web", sid))
    assert record["lifecycle"] == "open" and record["session_generation"] == before["generation"]
    assert history.cards == cards


def test_web_sweep_preserves_terminal_shell_job_with_uncommitted_receipt(root):
    from run.agents import TaskCoordinator
    from run.tools.background_jobs import prepare_background_job, publish_background_result, read_background_job
    from run.tools.background_worker import _finish

    sid = "pending-receipt"
    history = require_present(seed(root, sid, data=True))
    store = history.store
    integration = IntegrationStore(store, "web", sid, "shell-integration")
    integration.start()
    coordinator = TaskCoordinator(root, "alice", "web", sid, "run-shell", {
        "_cardstream_integration_id": integration.id,
        "_cardstream_generation": integration.generation,
        "_cardstream_meter_key": store.meter.key,
        "_cancel_subagents_event": threading.Event(),
    })
    coordinator.ensure_started()
    call = {"name": "shell", "arguments": {}}
    execution, _ = integration.register_tool("shell-call", call)
    job, _ = prepare_background_job(
        root, "alice", source="web", session_id=sid, working_dir=str(root),
        shell_type="auto", command_digest="test-only", completion_mode="required",
        ownership={"root_task_id": coordinator.id, "cardstream_integration_id": integration.id,
                   "cardstream_generation": integration.generation, "cardstream_meter_key": store.meter.key,
                   "original_call": call, "tool_call_id": "shell-call"},
    )
    job_id = job["job_id"]
    integration.tool_result(execution["execution_id"], {"ok": True, "job_id": job_id},
                            status="completed", duration_ms=1)
    assert not integration.live_executions()
    receipt = require_present(coordinator.finish("cancelled", defer_terminal=True))
    assert receipt["status"] == "partial"
    # The host can stop after committing the response, before sealing the root ledger.
    integration.finish("partial", metadata=receipt)

    with patch("run.tools.background_notifications.persist_shell_result", side_effect=OSError("test-only receipt write failure")):
        _finish(root, "alice", job_id, exit_code=0, output_tail=["late result"])
        terminal = read_background_job(root, "alice", job_id)
        assert terminal["status"] == "completed" and not terminal.get("card_result_seq")
        result = inspect_stale_web_sessions(root, "alice", idle_seconds=5400, now=time.time() + 6000)

    lifecycle = require_present(find_record(root, "alice", "web", sid))["lifecycle"]
    published = publish_background_result(root, "alice", terminal)
    assert (lifecycle, bool(published.get("card_result_seq"))) == ("open", True), (
        result, published.get("card_result_error_type"),
    )
    cards = [card for card in store.raw_page("web", sid) if card.event_key == f"shell-result:{job_id}"]
    assert len(cards) == 1 and cards[0].seq == published["card_result_seq"]


def test_stale_processing_claim_can_return_to_offline_recovery(root):
    seed(root, "expired", data=True)
    mutate_registry_record(root, "alice", "web", "expired", lambda r: {**r,
        "memory_status": "processing", "memory_claim_id": "abandoned",
        "memory_claimed_at": "2000-01-01T00:00:00Z"})
    assert "expired" in inspect_stale_web_sessions(root, "alice")["closed_sessions"]


def test_session_lock_is_skipped_and_raw_data_never_treated_as_empty(root):
    seed(root)
    history = seed(root, "data", data=True)
    ready, release = threading.Event(), threading.Event()
    def hold():
        with session_lock(root, "alice", "web", "empty"):
            ready.set()
            release.wait(5)
    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert ready.wait(2)
        result = cleanup_empty_web_sessions(root, "alice")
        assert result["skipped_busy"] == 1 and result["deleted_sessions"] == []
    finally:
        release.set()
        thread.join(3)
    assert history is not None
    assert len(history.cards) == 2


def test_transaction_rechecks_racing_presence_and_rolls_back_hide(root):
    seed(root)
    original, renewed = CardStore.db, False
    @contextmanager
    def race(store, *, write=False):
        nonlocal renewed
        if write and not renewed:
            renewed = True
            with original(store, write=True) as db:
                db.execute("INSERT INTO card_leases VALUES(?,?,?,?,?)",
                           (session_key("web", "empty"), "racer", time.time() + 90, 1, 0))
        with original(store, write=write) as db:
            yield db
    with patch.object(CardStore, "db", race):
        assert cleanup_empty_web_sessions(root, "alice")["deleted_sessions"] == []
    update_presence(root, "alice", "web", "empty", "racer", 2, release=True)
    original_hide = web_lifecycle._hide
    def fail(db, key):
        original_hide(db, key)
        raise OSError("rollback")
    with patch.object(web_lifecycle, "_hide", side_effect=fail), pytest.raises(OSError):
        cleanup_empty_web_sessions(root, "alice")
    found_record = find_record(root, "alice", "web", "empty")
    assert found_record is not None
    assert found_record["lifecycle"] == "open"


def test_bounded_pages_scan_all_candidates_and_repeated_inspection_is_idempotent(root):
    for n in range(3):
        seed(root, f"data{n}", data=True)
    seed(root)
    result = inspect_stale_web_sessions(root, "alice", batch_size=1)
    assert result["queued_memory_count"] == 3 and result["deleted_count"] == 1
    assert result["closed_count"] == 3 and result["inspected"] == 4
    assert inspect_stale_web_sessions(root, "alice", batch_size=1)["inspected"] == 0
    for n in range(3):
        record = find_record(root, "alice", "web", f"data{n}")
        assert record is not None
        assert record["memory_target_seq"] == 2 and record["summary_target_seq"] == 2


def test_queue_failure_remains_recoverable_and_error_is_sanitized(root):
    seed(root, "data", data=True)
    first = inspect_stale_web_sessions(root, "alice", queue_memory=Mock(side_effect=RuntimeError("private detail")))
    assert first["errors"] == [{"session_id": "data", "exception_type": "RuntimeError"}]
    assert "private detail" not in json.dumps(first)
    found_record = find_record(root, "alice", "web", "data")
    assert found_record is not None
    assert found_record["offline_inspection_pending"]
    second = inspect_stale_web_sessions(root, "alice")
    assert second["queued_memory_count"] == 1 and second["error_count"] == 0


def test_disabled_memory_and_bounded_result_samples(root):
    (root / "config/global_config.yaml").write_text('{"memory":{"extraction_mode":"disabled"}}')
    seed(root, "data", data=True)
    for n in range(25):
        seed(root, f"empty{n}")
    result = inspect_stale_web_sessions(root, "alice", batch_size=3)
    assert result["deleted_count"] == 25 and len(result["deleted_sessions"]) == 20
    assert result["queued_memory_count"] == 0
    assert result["queued_summary_count"] == 1
    assert {"session_id": "data", "reason": "memory_extraction_disabled"} in result["preserved_sessions"]


def test_readonly_inspection_does_not_create_or_migrate_old_history(root):
    legacy = root / "users/alice/history/history.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"never open this old database")
    assert inspect_stale_web_sessions(root, "alice")["inspected"] == 0
    assert not (root / "users/alice/history/cards.sqlite3").exists()
    assert legacy.read_bytes() == b"never open this old database"


def test_startup_service_checks_users_and_offline_app_once(root):
    seed(root, "alice-data", data=True)
    seed(root, "bob-data", data=True, user="bob")
    seed(root, "app-data", data=True, source="app", age=8000)
    wake, summary_wake = Mock(), Mock()
    service = WebRunService(root, memory_waker=wake, summary_waker=summary_wake)
    live = service.active_session("alice", "app_live_client", source="app")["session"]["session_id"]
    result = service.inspect_conversation_spaces_on_startup()
    assert result["state"] == "completed" and result["queued_memory"] == 3 and result["queued_summary"] == 3
    found_record = find_record(root, "alice", "app", live)
    assert found_record is not None
    assert found_record["lifecycle"] == "open"
    assert service.inspect_conversation_spaces_on_startup()["queued_memory"] == 0
    wake.assert_called_once()
    summary_wake.assert_called_once()


class LifecycleWorkerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'config').mkdir()
        (self.root / 'config/global_config.yaml').write_text(
            json.dumps({
                'memory': {'extraction_mode': 'compression_only'},
                'cron': {'session_idle_close_seconds': 3600},
            }), encoding='utf-8'
        )
        for user in ('alice', 'bob'):
            (self.root / 'users' / user).mkdir(parents=True)
            (self.root / 'users' / user / 'user_config.yaml').write_text('{}', encoding='utf-8')
        self.now = time.time()

    def test_cleanup_worker_owned_by_app_lifespan(self):
        app = create_app(root=self.root)
        previous = set(threading.enumerate())
        async def check():
            async with app.router.lifespan_context(app):
                workers = [t for t in threading.enumerate() if t not in previous and t.name == 'web-empty-session-cleanup']
                self.assertEqual(len(workers), 1)
            self.assertFalse(workers[0].is_alive())
        asyncio.run(check())

    def test_app_lifespan_runs_one_delayed_startup_pass(self):
        service = WebRunService(self.root)
        service.startup_inspection_delay_seconds = 0
        inspected = threading.Event()
        inspect_mock = Mock(
            side_effect=lambda _stop: inspected.set() or {'state': 'completed'}
        )
        service.inspect_conversation_spaces_on_startup = inspect_mock
        app = create_app(root=self.root, service=service)
        async def check():
            async with app.router.lifespan_context(app):
                self.assertTrue(await asyncio.to_thread(inspected.wait, 2))
                await asyncio.sleep(0.05)
                inspect_mock.assert_called_once()
        asyncio.run(check())

    def test_memory_queue_wake_interrupts_maintenance_sleep_without_running_agent(self):
        scheduler = MaintenanceScheduler(self.root, poll_interval=30)
        first, second = threading.Event(), threading.Event()
        calls: list[int] = []
        def scan_once():
            calls.append(len(calls) + 1)
            (first if len(calls) == 1 else second).set()
            return {}
        scheduler.scan_once = Mock(side_effect=scan_once)
        scheduler.start()
        try:
            self.assertTrue(first.wait(2))
            scheduler.wake()
            self.assertTrue(second.wait(2))
            self.assertEqual(calls, [1, 2])
        finally:
            scheduler.stop(timeout=2)
