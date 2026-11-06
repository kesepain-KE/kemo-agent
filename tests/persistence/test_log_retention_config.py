"""R1 4.2: instance log retention is live and independent of history cleanup."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import yaml
import json
from pathlib import Path
import sqlite3

import pytest

from cron.scheduler import _expand_spec
from run import config
from run.infra.log_store import DEFAULT_RETENTION_DAYS, LogStore


def write_config(root, value):
    path = root / "config/global_config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture(autouse=True)
def no_retention_env(monkeypatch):
    monkeypatch.delenv("KEMO_LOG_RETENTION_DAYS", raising=False)


def test_repository_has_distinct_log_retention_and_collection_interval_settings():
    value = yaml.safe_load((Path(__file__).resolve().parents[2] / "config/global_config.yaml").read_text("utf-8"))
    assert value["cron"]["log_retention_days"] == 30
    assert value["cron"]["task_cron_system"]["expand_update"]["interval_seconds"] == 30
    assert config.cron_history_retention_days(value) == 7


def test_environment_overrides_instance_and_explicit_override_remains_compatible(tmp_path, monkeypatch):
    write_config(tmp_path, {"cron": {"log_retention_days": 30}})
    store = LogStore(tmp_path)
    assert store.retention_days == 30
    monkeypatch.setenv("KEMO_LOG_RETENTION_DAYS", "14")
    assert store.retention_days == 14
    assert LogStore(tmp_path, retention_days=0).retention_days == 0
    monkeypatch.setenv("KEMO_LOG_RETENTION_DAYS", "invalid")
    assert store.retention_days == 30
    monkeypatch.setenv("KEMO_LOG_RETENTION_DAYS", "0")
    assert store.retention_days == 0


@pytest.mark.parametrize("value", [True, -1, 2.5, "30", None, {}, []])
def test_invalid_instance_retention_falls_back_without_deleting_more(tmp_path, value):
    write_config(tmp_path, {"cron": {"log_retention_days": value}})
    assert LogStore(tmp_path).retention_days == DEFAULT_RETENTION_DAYS


@pytest.mark.parametrize("value", [{}, {"cron": []}, {"cron": None}, []])
def test_missing_or_malformed_document_uses_code_default(tmp_path, value):
    write_config(tmp_path, value)
    assert LogStore(tmp_path).retention_days == DEFAULT_RETENTION_DAYS


def test_same_instance_observes_shortened_policy_on_next_append_without_vacuum(tmp_path, monkeypatch):
    write_config(tmp_path, {"cron": {"log_retention_days": 90}})
    store = LogStore(tmp_path)
    now = datetime.now(timezone.utc)
    old = {
        "executed_at": (now - timedelta(days=45)).isoformat(),
        "user": "alice", "task_id": "old", "status": "success", "result": {},
    }
    store.append_cron(old)
    assert len(store.list_cron("alice")) == 1
    statements = []
    connect = sqlite3.connect

    def traced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced)
    write_config(tmp_path, {"cron": {"log_retention_days": 30}})
    store.append_cron({**old, "task_id": "new", "executed_at": now.isoformat()})
    assert store.retention_days == 30
    assert [row["task_id"] for row in store.list_cron("alice")] == ["new"]
    assert not any("VACUUM" in sql.upper() for sql in statements)
    with closing(connect(store.path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_disabled_policy_preserves_old_logs_and_user_config_cannot_change_it(tmp_path):
    write_config(tmp_path, {"cron": {"log_retention_days": 0}})
    user = tmp_path / "users/alice"
    user.mkdir(parents=True)
    (user / "user_config.yaml").write_text('{"cron":{"log_retention_days":1}}', encoding="utf-8")
    store = LogStore(tmp_path)
    store.append_cron({
        "executed_at": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
        "user": "alice", "task_id": "old", "status": "success",
    })
    assert store.retention_days == 0 and len(store.list_cron("alice")) == 1


def test_nested_interval_controls_collector_schedule():
    value = {
        "cron": {"task_cron_system": {"expand_update": {"interval_seconds": 17}}},
        "task_cron_system": {"expand_update_rate": 5},
    }
    assert config.configured_expand_update_interval(value) == 17
    assert _expand_spec(value)["interval_seconds"] == 17
    assert config.configured_expand_update_interval({}) == 30


@pytest.mark.parametrize("value", [0, -1, True, "5", 1.5, None])
def test_invalid_nested_interval_is_rejected(value):
    with pytest.raises(config.ConfigError, match="interval_seconds"):
        config.configured_expand_update_interval({
            "cron": {"task_cron_system": {"expand_update": {"interval_seconds": value}}},
        })
