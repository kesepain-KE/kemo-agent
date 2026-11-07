"""Cron retention hides metadata atomically and keeps every RAW event."""
import json
from datetime import datetime, timedelta, timezone
import threading
from unittest.mock import patch

import pytest

from run.cards import CardSessionClosedError, CardStore
from run.config import ConfigError, cron_history_retention_days, merge_user_config
from run.conversation import session_lock
from run.history import (cleanup_cron_history, close_session, find_record,
                         mutate_registry_record)
from run.scheduler import MaintenanceScheduler
from tests.card_history_support import seed_history


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.yaml").write_text('{}')
    for user in ("alice", "bob"):
        (tmp_path / f"users/{user}").mkdir(parents=True)
    return tmp_path


NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
SOURCE = "background:cron:job"


def seed(root, sid="old", *, source=SOURCE, days=8, user="alice"):
    stamp = (NOW - timedelta(days=days)).isoformat()
    with patch("run.cards.store.utc_now", return_value=stamp):
        history = seed_history(root, user, source, sid, [{"role": "user", "content": "fixture"}])
    close_session(root, user, source, sid)
    mutate_registry_record(root, user, source, sid,
                          lambda r: {**r, "cron_finished_at": stamp}, updated_at=stamp)
    return history


def test_retention_configuration_validation_and_global_only():
    assert cron_history_retention_days({}) == 7
    assert cron_history_retention_days({"cron": {"history_retention_days": 0}}) == 0
    merged = merge_user_config({"cron": {"history_retention_days": 14}},
                              {"cron": {"history_retention_days": 1, "enabled": False}})
    assert merged["cron"]["history_retention_days"] == 14
    assert merged["cron"]["enabled"] is False
    for value in (-1, 3651, 1.5, True, None, "7"):
        with pytest.raises(ConfigError):
            cron_history_retention_days({"cron": {"history_retention_days": value}})


def test_only_expired_cron_sessions_are_hidden_and_raw_is_preserved(root):
    history = seed(root)
    original = history.cards
    seed(root, "recent", days=6)
    seed(root, user="bob")
    for n, source in enumerate(("web", "message:telegram", "background:plan:job", "background:cron:", "BACKGROUND:CRON:job")):
        seed(root, f"other{n}", source=source)
    sentinel = root / "users/alice/history/old-window.json"
    sentinel.parent.mkdir(exist_ok=True)
    sentinel.write_bytes(b"legacy must remain")
    result = cleanup_cron_history(root, "alice", now=NOW)
    assert result["deleted_sessions"] == 1 and result["raw_preserved"]
    assert find_record(root, "alice", SOURCE, "old") is None
    assert history.cards == original
    assert find_record(root, "bob", SOURCE, "old") is not None
    assert find_record(root, "alice", SOURCE, "recent") is not None
    assert sentinel.read_bytes() == b"legacy must remain"
    with pytest.raises(CardSessionClosedError):
        history.store.append(SOURCE, "old", "late", "user_text", {"text": "late"})


def test_boundary_metadata_rename_and_invalid_completion_time(root):
    seed(root, "boundary", days=7)
    seed(root, "renamed")
    mutate_registry_record(root, "alice", SOURCE, "renamed", lambda r: {**r, "title": "fresh"}, updated_at=NOW.isoformat())
    seed(root, "invalid")
    mutate_registry_record(root, "alice", SOURCE, "invalid", lambda r: {**r, "cron_finished_at": "invalid"})
    assert cleanup_cron_history(root, "alice", now=NOW)["deleted_sessions"] == 2
    assert find_record(root, "alice", SOURCE, "invalid")


@pytest.mark.parametrize("kind", ["memory", "summary"])
def test_active_maintenance_claim_is_protected(root, kind):
    seed(root)
    mutate_registry_record(root, "alice", SOURCE, "old", lambda r: {**r,
        f"{kind}_status": "processing", f"{kind}_claim_expires_at": (NOW + timedelta(seconds=60)).isoformat()})
    result = cleanup_cron_history(root, "alice", now=NOW)
    assert result["deleted_sessions"] == 0 and result["skipped_busy"] == 1


def test_active_session_lock_is_skipped_without_waiting(root):
    seed(root)
    ready, release = threading.Event(), threading.Event()
    def hold():
        with session_lock(root, "alice", SOURCE, "old"):
            ready.set()
            release.wait(5)
    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert ready.wait(2)
        result = cleanup_cron_history(root, "alice", now=NOW)
        assert result["skipped_busy"] == 1 and result["deleted_sessions"] == 0
    finally:
        release.set()
        thread.join(3)


def test_disabled_retention_and_batch_limit(root):
    for n in range(3):
        seed(root, str(n))
    assert cleanup_cron_history(root, "alice", retention_days=0, now=NOW)["deleted_sessions"] == 0
    assert cleanup_cron_history(root, "alice", limit=2, now=NOW)["deleted_sessions"] == 2
    assert cleanup_cron_history(root, "alice", now=NOW)["deleted_sessions"] == 1


def test_hide_transaction_rolls_back_on_error(root):
    history = seed(root)
    from run.history.retention import _hide
    def fail(db, key):
        _hide(db, key)
        raise OSError("fixture failure")
    with patch("run.history.retention._hide", side_effect=fail), pytest.raises(OSError):
        cleanup_cron_history(root, "alice", now=NOW)
    found_record = find_record(root, "alice", SOURCE, "old")
    assert found_record is not None
    assert found_record["lifecycle"] == "closed"
    assert len(history.cards) == 1


def test_maintenance_throttle_reads_hot_global_setting(root):
    seed(root)
    config = root / "config/global_config.yaml"
    config.write_text('{"cron":{"history_retention_days":0}}')
    scheduler = MaintenanceScheduler(root, history_summary_enabled=False)
    with patch.object(scheduler, "_recover_pending_memory", return_value={}):
        scheduler.scan_once(now=NOW)
        config.write_text('{"cron":{"history_retention_days":7}}')
        assert "cron_history_cleanup" not in scheduler.scan_once(now=NOW + timedelta(minutes=1))["alice"]
        result = scheduler.scan_once(now=NOW + timedelta(minutes=5))
    assert result["alice"]["cron_history_cleanup"]["deleted_sessions"] == 1
