"""R1 4.1: only proven unchanged success is quiet; hourly metrics retain runs."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cron import executor
from cron.scheduler import BEIJING, CronScheduler
from cron.system_execution import _append_system_execution, module_data_signature
from run.infra import LogStore


def record(scheduler, *, index=0, task="expand_update", result=None, duration=10):
    scheduler._record_system_execution(
        action=task, user="alice", task_id=task,
        executed_at=datetime(2026, 10, 30, tzinfo=BEIJING) + timedelta(seconds=index),
        duration_ms=duration,
        result=result if result is not None else {"status": "completed", "data_changed": False},
    )


def test_proven_unchanged_success_has_only_one_hourly_row_and_mixed_metrics(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("run.scheduler.log_aggregator.time", SimpleNamespace(monotonic=lambda: clock[0]))
    scheduler = CronScheduler(tmp_path, config={})
    record(scheduler, index=0, duration=10)
    assert not (tmp_path / "runtime/logs.sqlite3").exists()
    clock[0] = 3599
    record(scheduler, index=3599, duration=30, result={"status": "completed", "data_changed": True})
    record(scheduler, index=3599, duration=50, result={"status": "partial", "failed": ["demo"]})
    rows = LogStore(tmp_path).list_cron("alice")
    assert len(rows) == 2 and not any(row["result"].get("aggregated") for row in rows)
    clock[0] = 3600
    scheduler._flush_due_success_logs()
    rows = LogStore(tmp_path).list_cron("alice")
    aggregates = [row for row in rows if row["result"].get("aggregated")]
    assert len(aggregates) == 1
    summary = aggregates[0]["result"]
    assert summary["runs"] == 3 and summary["successes"] == 2 and summary["failures"] == 1
    assert summary["success_rate"] == pytest.approx(2 / 3)
    assert summary["average_duration_ms"] == 30 and summary["max_duration_ms"] == 50
    assert summary["status_counts"] == {"success": 2, "partial": 1}


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_every_repeated_failure_is_immediately_durable(tmp_path, status):
    scheduler = CronScheduler(tmp_path, config={})
    for index in range(20):
        record(scheduler, index=index, result={"status": status, "failed": ["same-module"]})
    assert len(LogStore(tmp_path).list_cron("alice")) == 20
    scheduler.flush_persistence()
    rows = LogStore(tmp_path).list_cron("alice")
    assert len(rows) == 21
    assert next(row["result"] for row in rows if row["result"].get("aggregated"))["failures"] == 20


def test_unknown_change_is_conservatively_written(tmp_path):
    scheduler = CronScheduler(tmp_path, config={})
    record(scheduler, result={"status": "completed", "updated": ["demo"]})
    rows = LogStore(tmp_path).list_cron("alice")
    assert len(rows) == 1 and not rows[0]["result"].get("aggregated")


def test_other_system_actions_follow_the_same_proven_unchanged_rule(tmp_path):
    scheduler = CronScheduler(tmp_path, config={})
    record(scheduler, task="session_lifecycle_sweep")
    assert LogStore(tmp_path).list_cron("alice") == []
    scheduler.flush_persistence()
    assert LogStore(tmp_path).list_cron("alice")[0]["result"]["runs"] == 1


def test_direct_writer_suppresses_only_proven_unchanged_success(tmp_path):
    for index, result in enumerate([
        {"status": "completed", "data_changed": False},
        {"status": "completed"},
        {"status": "partial", "data_changed": False},
    ]):
        _append_system_execution(
            tmp_path, user="alice", task_id="expand_update",
            executed_at=datetime(2026, 10, 30, tzinfo=BEIJING) + timedelta(seconds=index),
            duration_ms=1, result=result,
        )
    assert len(LogStore(tmp_path).list_cron("alice")) == 2


@pytest.mark.parametrize("change", ["same", "data", "health", "timestamp", "unknown", "failed"])
def test_module_change_evidence_is_from_declared_data_not_an_updated_label(tmp_path, monkeypatch, change):
    modules = tmp_path / "expand"
    module = modules / "demo"
    module.mkdir(parents=True)
    manifest = {
        "kind": "expand", "name": "demo", "explain": "fixture",
        "health": "异常" if change == "health" else "正常",
        "update_script": "update.py", "input_file": "input_data.md", "input_open": True,
        "control_open": False,
    }
    path = module / "module.yaml"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (module / "update.py").write_text("# offline fixture", encoding="utf-8")
    data = module / "input_data.md"
    data.write_text("x" * 1_048_577 if change == "unknown" else "stable data", encoding="utf-8")

    def update(*_args, **_kwargs):
        if change == "data":
            data.write_text("changed data", encoding="utf-8")
        elif change in {"health", "timestamp"}:
            value = json.loads(path.read_text(encoding="utf-8"))
            value["health" if change == "health" else "recent_update"] = (
                "正常" if change == "health" else "2026-10-30 10:05:00"
            )
            path.write_text(json.dumps(value), encoding="utf-8")
        return {"ok": False, "reason": "offline failure"} if change == "failed" else {"ok": True}

    monkeypatch.setattr(executor, "_run_module_updater", update)
    monkeypatch.setattr(executor, "record_expand_runtime", lambda *_args, **_kwargs: None)
    result = executor._update_modules(modules, "expand", config={}, cancel_event=None)
    assert result["data_changed"] is (change not in {"same", "timestamp"})
    assert result["status"] == ("failed" if change == "failed" else "completed")


@pytest.mark.parametrize(
    ("manifest_name", "data_key", "timestamp_line"),
    [
        ("module.yaml", "input_file", "_数据来源：Open-Meteo；采集时间：2026-10-30 10:00:00_"),
        ("module.yaml", "input_file", "> 最近采集：2026-10-30 10:00:00（北京时间）"),
    ],
)
def test_module_signature_uses_declared_data_file_and_ignores_collection_time(
    tmp_path, manifest_name, data_key, timestamp_line,
):
    module = tmp_path / "module"
    module.mkdir()
    manifest = module / manifest_name
    manifest.write_text(
        json.dumps({data_key: "data.md", "health": "正常", "recent_update": "10:00:00"}),
        encoding="utf-8",
    )
    data = module / "data.md"
    data.write_text(f"# 采集数据\n业务值：晴\n{timestamp_line}\n", encoding="utf-8")

    original = module_data_signature(module, manifest_name)
    assert original is not None

    manifest.write_text(
        json.dumps({data_key: "data.md", "health": "正常", "recent_update": "10:05:00"}),
        encoding="utf-8",
    )
    data.write_text(
        f"# 采集数据\n业务值：晴\n{timestamp_line.replace('10:00:00', '10:05:00')}\n",
        encoding="utf-8",
    )
    assert module_data_signature(module, manifest_name) == original

    data.write_text(
        f"# 采集数据\n业务值：雨\n{timestamp_line.replace('10:00:00', '10:05:00')}\n",
        encoding="utf-8",
    )
    assert module_data_signature(module, manifest_name) != original


def test_sense_collection_timestamp_noop_is_hourly_aggregated(tmp_path, monkeypatch):
    module = tmp_path / "sense" / "weather"
    module.mkdir(parents=True)
    manifest = module / "module.yaml"
    manifest.write_text(
        json.dumps({
            'name': 'weather',
            'input_file': 'sense.md',
            'health': '正常',
            'recent_update': '2026-10-30 10:00:00',
            'update_script': 'update.py',
            'kind': 'sense',
            'explain': 'fixture perception',
            'input_open': True,
            'control_open': False,
        }),
        encoding="utf-8",
    )
    data = module / "sense.md"
    data.write_text(
        "# 天气\n业务值：晴\n_数据来源：Open-Meteo；采集时间：2026-10-30 10:00:00_\n",
        encoding="utf-8",
    )
    (module / "update.py").write_text("# fixture", encoding="utf-8")

    def update(*_args, **_kwargs):
        data.write_text(
            "# 天气\n业务值：晴\n_数据来源：Open-Meteo；采集时间：2026-10-30 10:05:00_\n",
            encoding="utf-8",
        )
        return {"ok": True}

    monkeypatch.setattr(executor, "_run_module_updater", update)
    result = executor._update_modules(
        module.parent, "sense", config={}, cancel_event=None,
    )
    assert result["status"] == "completed"
    assert result["data_changed"] is False

    scheduler = CronScheduler(tmp_path, config={})
    record(scheduler, task="perception_update", result=result)
    assert LogStore(tmp_path).list_cron("alice") == []
    scheduler.flush_persistence()
    rows = LogStore(tmp_path).list_cron("alice")
    assert len(rows) == 1
    assert rows[0]["result"]["runs"] == 1
    assert rows[0]["result"]["successes"] == 1


@pytest.mark.parametrize("reparse", [False, True])
def test_module_evidence_without_new_pathlib_junction_api(tmp_path, monkeypatch, reparse):
    (tmp_path / "module.yaml").write_text('{"input_file":"data.md"}', encoding="utf-8")
    (tmp_path / "data.md").write_text("stable", encoding="utf-8")
    original = Path.lstat

    def attributes(current):
        value = original(current)
        if reparse and current == tmp_path:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
        return value

    monkeypatch.delattr(Path, "is_junction", raising=False)
    monkeypatch.setattr(Path, "lstat", attributes)
    assert (module_data_signature(tmp_path, "module.yaml") is None) is reparse


def test_simulated_day_of_three_stable_collectors_stays_below_200_rows(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("run.scheduler.log_aggregator.time", SimpleNamespace(monotonic=lambda: clock[0]))
    scheduler = CronScheduler(tmp_path, config={})
    for second in range(0, 86400, 5):
        clock[0] = float(second)
        for task in ("expand_update", "perception_update", "memory_promotion"):
            record(scheduler, index=second, task=task, duration=2)
        scheduler._flush_due_success_logs()
    scheduler.flush_persistence()
    rows = LogStore(tmp_path).list_cron("alice", limit=200)
    assert len(rows) == 72 and len(rows) < 200
    assert all(row["result"]["aggregated"] for row in rows)
    assert sum(row["result"]["runs"] for row in rows) == 3 * 86400 // 5
