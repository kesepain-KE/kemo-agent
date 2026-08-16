from __future__ import annotations

import json
from datetime import datetime, timedelta
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

import pytest

from cron.scheduler import BEIJING, CronScheduler
from run.cron_log_aggregator import CronLogAggregator
from run.cron_runtime_state import (
    SystemCronLease,
    pending_cron_runtime,
    update_cron_runtime,
)
from run.cron_store import CronStore, normalize_task
from run.log_store import LogStore


def _system_task(root: Path, task_id: str = "expand_update") -> tuple[CronStore, Path]:
    store = CronStore(root, "__system__", system=True)
    store.create(
        normalize_task(
            task_id=task_id,
            title="collector",
            prompt="",
            user="",
            type="recurring",
            interval_seconds=5,
            next_run_at=(datetime.now(BEIJING) - timedelta(seconds=1)).isoformat(),
            exec_mode="system",
            action=task_id,
        )
    )
    return store, root / "cron" / "task_cron_system" / f"{task_id}.json"


def test_one_hundred_successes_are_persisted_as_one_aggregate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        aggregator = CronLogAggregator(root, flush_seconds=3600)
        for index in range(100):
            aggregator.record_success(
                {
                    "executed_at": f"2026-08-16T12:{index // 60:02d}:{index % 60:02d}+08:00",
                    "user": "__system__",
                    "task_id": "expand_update",
                    "status": "success",
                    "duration_ms": index,
                    "result": {"status": "completed"},
                }
            )

        assert not (root / "runtime" / "logs.sqlite3").exists()
        assert aggregator.pending_windows() == 1

        aggregator.flush()

        rows = LogStore(root).list_cron("__system__")
        assert len(rows) == 1
        assert rows[0]["result"]["aggregated"] is True
        assert rows[0]["result"]["runs"] == 100
        assert rows[0]["result"]["successes"] == 100


def test_error_flushes_pending_successes_and_is_immediately_durable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        aggregator = CronLogAggregator(root, flush_seconds=3600)
        common = {
            "executed_at": "2026-08-16T12:00:00+08:00",
            "user": "alice",
            "task_id": "perception_update",
            "duration_ms": 10,
        }
        aggregator.record_success(
            {**common, "status": "success", "result": {"status": "completed"}}
        )
        aggregator.record_immediate(
            {
                **common,
                "executed_at": "2026-08-16T12:00:05+08:00",
                "status": "failed",
                "result": {},
                "error": {"type": "RuntimeError", "message": "boom"},
            }
        )

        rows = LogStore(root).list_cron("alice")
        assert len(rows) == 2
        assert {row["status"] for row in rows} == {"success", "failed"}


def test_due_success_window_flushes_without_another_record() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        aggregator = CronLogAggregator(root, flush_seconds=10)
        aggregator.record_success(
            {
                "executed_at": "2026-08-16T12:00:00+08:00",
                "user": "alice",
                "task_id": "expand_update",
                "status": "success",
                "duration_ms": 10,
                "result": {"status": "completed"},
            }
        )

        assert aggregator.flush_due(now=time.monotonic() + 11) == 1
        assert len(LogStore(root).list_cron("alice")) == 1


def test_failed_success_flush_does_not_suppress_immediate_error() -> None:
    with tempfile.TemporaryDirectory() as directory:
        aggregator = CronLogAggregator(Path(directory), flush_seconds=3600)
        aggregator.record_success(
            {
                "executed_at": "2026-08-16T12:00:00+08:00",
                "user": "alice",
                "task_id": "expand_update",
                "status": "success",
                "duration_ms": 10,
                "result": {"status": "completed"},
            }
        )
        written: list[dict[str, object]] = []

        def flaky_append(_store: LogStore, record: dict[str, object]) -> None:
            if record.get("status") == "success":
                raise RuntimeError("temporary database failure")
            written.append(record)

        with patch.object(LogStore, "append_cron", new=flaky_append):
            with pytest.raises(RuntimeError, match="temporary database failure"):
                aggregator.record_immediate(
                    {
                        "executed_at": "2026-08-16T12:00:05+08:00",
                        "user": "alice",
                        "task_id": "expand_update",
                        "status": "failed",
                        "duration_ms": 10,
                        "result": {},
                        "error": {"type": "RuntimeError", "message": "boom"},
                    }
                )

        assert [record["status"] for record in written] == ["failed"]
        assert aggregator.pending_windows() == 1


def test_high_frequency_noop_is_aggregated_but_partial_failure_is_immediate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        scheduler = CronScheduler(root, config={})
        scheduler._record_system_execution(
            action="expand_update",
            user="alice",
            task_id="expand_update",
            executed_at=datetime.now(BEIJING),
            duration_ms=1,
            result={"status": "skipped"},
        )
        assert LogStore(root).list_cron("alice") == []
        scheduler._record_system_execution(
            action="expand_update",
            user="alice",
            task_id="expand_update",
            executed_at=datetime.now(BEIJING),
            duration_ms=2,
            result={"status": "partial", "failed": ["demo"]},
        )
        rows = LogStore(root).list_cron("alice")
        assert len(rows) == 2
        assert rows[0]["status"] == "partial"
        assert rows[1]["status"] == "success"
        assert rows[1]["result"]["aggregated"] is True


def test_system_schedule_advances_in_memory_until_explicit_flush() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store, path = _system_task(root)
        before = path.read_bytes()
        scheduler = CronScheduler(
            root,
            config={
                "task_cron_system": {
                    "runtime_checkpoint_seconds": 3600,
                    "success_log_flush_seconds": 3600,
                }
            },
        )

        with patch(
            "cron.scheduler.execute_cron_task",
            return_value={"status": "completed", "category": "expand"},
        ):
            assert scheduler._system_lease.try_acquire() is True
            assert scheduler._scan_once(include_system=True) == 1

        assert path.read_bytes() == before
        live = store.read("expand_update")
        assert datetime.fromisoformat(live["next_run_at"]) > datetime.now(BEIJING)
        assert LogStore(root).list_cron("__system__") == []

        scheduler.flush_persistence()

        persisted = json.loads(path.read_text("utf-8"))
        assert persisted["latest_run_at"]
        assert datetime.fromisoformat(persisted["next_run_at"]) > datetime.now(BEIJING)
        rows = LogStore(root).list_cron("__system__")
        assert len(rows) == 1
        assert rows[0]["result"]["runs"] == 1
        assert pending_cron_runtime(root) == []
        scheduler._system_lease.release()


def test_checkpoint_does_not_clear_a_newer_runtime_snapshot() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _store, path = _system_task(root)
        scheduler = CronScheduler(
            root,
            config={"task_cron_system": {"runtime_checkpoint_seconds": 300}},
        )
        old_state = update_cron_runtime(
            root,
            "__system__",
            True,
            "expand_update",
            latest_run_at="2026-08-16T12:00:00+08:00",
            next_run_at="2026-08-16T12:00:05+08:00",
        )
        original_update = CronStore.update

        def publish_new_state_before_write(
            current_store: CronStore,
            task_id: str,
            mutator,
            *,
            clear_runtime: bool = True,
        ):
            update_cron_runtime(
                root,
                "__system__",
                True,
                task_id,
                latest_run_at="2026-08-16T12:00:05+08:00",
                next_run_at="2026-08-16T12:00:10+08:00",
            )
            return original_update(
                current_store,
                task_id,
                mutator,
                clear_runtime=clear_runtime,
            )

        with patch.object(CronStore, "update", new=publish_new_state_before_write):
            scheduler._persist_runtime_state(old_state)

        pending = pending_cron_runtime(root)
        assert len(pending) == 1
        assert pending[0]["latest_run_at"] == "2026-08-16T12:00:05+08:00"
        persisted = json.loads(path.read_text("utf-8"))
        assert persisted["latest_run_at"] == "2026-08-16T12:00:00+08:00"


def test_foreground_scan_checkpoints_before_releasing_system_lease() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _store, path = _system_task(root)
        before = path.read_bytes()
        scheduler = CronScheduler(root, config={})

        with patch(
            "cron.scheduler.execute_cron_task",
            return_value={"status": "completed", "category": "expand"},
        ):
            assert scheduler.scan_once() == 1

        assert path.read_bytes() != before
        assert scheduler._system_lease.owned is False


def test_only_one_system_scheduler_lease_can_own_a_root() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first = SystemCronLease(root)
        second = SystemCronLease(root)
        assert first.try_acquire() is True
        assert second.try_acquire() is False
        first.release()
        assert second.try_acquire() is True
        second.release()


def test_system_scheduler_lease_is_exclusive_across_processes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        lease = SystemCronLease(root)
        assert lease.try_acquire() is True
        command = (
            "from pathlib import Path; "
            "from run.cron_runtime_state import SystemCronLease; "
            f"lease=SystemCronLease(Path({str(root)!r})); "
            "print('owned' if lease.try_acquire() else 'blocked'); "
            "lease.release()"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])

        blocked = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert blocked.stdout.strip() == "blocked"

        lease.release()
        acquired = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert acquired.stdout.strip() == "owned"
