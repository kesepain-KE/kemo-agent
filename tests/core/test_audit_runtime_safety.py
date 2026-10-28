"""Deterministic failure-injection checks for the audit's runtime boundaries."""
from __future__ import annotations

import asyncio
import builtins
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from run.agents.task_ledger import TaskLedger
from run.conversation import runtime
from run.conversation.loop_cleanup import cleanup_run_registration
from run.tools import _enforce_tool_result_limit
from web.auth import WebAuthConfig, WebAuthConfigError, validate_web_bind


def test_unknown_executor_does_not_become_terminal(monkeypatch):
    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("injected")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    record = {"status": "running", "owner": "other", "pid": 123, "process_started": 1.0}
    result = TaskLedger.reconcile(record)
    assert result["status"] == "running"
    assert result["reconcile_unverified"] is True


def test_access_denied_is_not_process_death(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "Process", Mock(side_effect=psutil.AccessDenied(123)))
    record = {"status": "running", "owner": "other", "pid": 123, "process_started": 1.0}
    assert TaskLedger.reconcile(record)["status"] == "running"
    monkeypatch.setattr(psutil, "Process", Mock(side_effect=psutil.NoSuchProcess(123)))
    assert TaskLedger.reconcile(record)["status"] == "interrupted"


def test_cleanup_failures_remain_visible_when_history_storage_fails(tmp_path, caplog):
    (tmp_path / "users/alice").mkdir(parents=True)
    request = {"run_id": "run_injected"}
    cancel = threading.Event()
    cancel.set()
    fail = Mock(side_effect=OSError("injected"))
    cleanup_run_registration(
        terminal_committer=SimpleNamespace(commit_cancelled_round=fail),
        round_state=SimpleNamespace(finalized=False, history_run_registered=True, retry_follows=False),
        cancel_event=cancel, request=request, update_run_state=fail, base=tmp_path,
        user="alice", source="web", session_id="scope", run_id="run_injected",
    )
    assert request["_cleanup_diagnostics"] == {
        "cancel_commit_failed": True, "run_state_update_failed": True,
    }
    files = list((tmp_path / "users/alice/history/run_failures").glob("*.json"))
    assert len(files) == 2
    assert all(json.loads(p.read_text())["run_id"] == "run_injected" for p in files)
    assert "cancel_commit_failed" in caplog.text


def test_large_completed_result_is_a_success_receipt_with_full_artifact(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    original = {"ok": True, "created": "external-resource", "data": "x" * 150_000}
    result = _enforce_tool_result_limit("expand_call", {}, original, {
        "root": str(tmp_path), "user": "alice",
    })
    assert result["ok"] is True and result["execution_completed"] is True
    assert len(json.dumps(result)) < 100_000
    assert json.loads((tmp_path / result["artifact"]["path"]).read_text()) == original


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "host.example"])
def test_public_binding_requires_instance_admin_auth(host):
    with pytest.raises(WebAuthConfigError):
        validate_web_bind(host, WebAuthConfig())
    validate_web_bind(host, WebAuthConfig(access_token="test-only"))


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_local_binding_keeps_no_auth_mode(host):
    validate_web_bind(host, WebAuthConfig())


def test_async_stream_drives_and_closes_generator_in_one_thread(monkeypatch):
    owners, closed = [], threading.Event()

    def events(*args, **kwargs):
        lock = threading.RLock()
        with lock:
            try:
                for _ in range(3):
                    owners.append(threading.get_ident())
                    yield object()
            finally:
                owners.append(threading.get_ident())
                closed.set()

    monkeypatch.setattr(runtime, "iter_request_events", events)

    async def consume():
        stream = runtime.stream_request({"user": "alice", "source": "web", "session_id": "scope"})
        await anext(stream)
        await anext(stream)
        await stream.aclose()

    asyncio.run(consume())
    assert closed.is_set()
    assert len(set(owners)) == 1
    assert owners[0] != threading.get_ident()


def test_independent_cron_dispatch_does_not_wait_for_hung_executor(tmp_path, monkeypatch):
    from cron.scheduler import CronScheduler
    scheduler = CronScheduler(tmp_path, config={"provider_runtime": {"max_concurrent_requests": 2}})
    entered, release, second = threading.Event(), threading.Event(), threading.Event()

    def execute(user, task, cancel):
        if task["task_id"] == "hung":
            entered.set()
            release.wait(5)
        else:
            second.set()

    monkeypatch.setattr(scheduler, "_execute_user_task", execute)
    try:
        assert scheduler._dispatch_user_task("alice", {"task_id": "hung"})
        assert entered.wait(2)
        assert not scheduler._dispatch_user_task("alice", {"task_id": "hung"})
        assert scheduler._dispatch_user_task("alice", {"task_id": "next"})
        assert second.wait(2)
        assert ("alice", "hung") in scheduler._user_executions
    finally:
        release.set()
        scheduler.stop(timeout=3)


def test_system_scan_deadline_stop_keeps_lease_until_actual_exit(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from cron.scheduler import CronScheduler
    from cron.user_execution import dispatch_system_scan

    scheduler = CronScheduler(tmp_path, config={})
    entered, release, second = threading.Event(), threading.Event(), threading.Event()
    lease = SimpleNamespace(release=Mock())
    monkeypatch.setattr(scheduler, "_system_lease", lease)
    monkeypatch.setattr(scheduler, "flush_persistence", Mock())
    monkeypatch.setattr(scheduler, "_scan_system_tasks", lambda now: (entered.set(), release.wait(5)))
    monkeypatch.setattr(scheduler, "_execute_user_task", lambda user, task, cancel: second.set())
    try:
        dispatch_system_scan(scheduler, datetime.now(timezone.utc))
        assert entered.wait(2)
        assert scheduler._dispatch_user_task("alice", {"task_id": "independent"})
        assert second.wait(2)
        scheduler.stop(timeout=0.01)
        lease.release.assert_not_called()
    finally:
        release.set()
        scheduler.stop(timeout=3)
    assert lease.release.called


def test_cron_deadline_cancels_but_keeps_live_execution_slot(tmp_path, monkeypatch):
    from cron.scheduler import CronScheduler
    scheduler = CronScheduler(tmp_path, config={"agent_runtime": {"default_timeout": 1}})
    entered, release = threading.Event(), threading.Event()
    seen = []

    def execute(user, task, cancel):
        seen.append(cancel)
        entered.set()
        release.wait(5)

    report = Mock()
    monkeypatch.setattr(scheduler, "_execute_user_task", execute)
    monkeypatch.setattr(scheduler, "_report_execution_error", report)
    try:
        assert scheduler._dispatch_user_task("alice", {"task_id": "live"})
        assert entered.wait(2)
        assert seen[0].wait(3)
        assert getattr(seen[0], "stop_reason") == "timeout"
        assert ("alice", "live") in scheduler._user_executions
        assert not scheduler._dispatch_user_task("alice", {"task_id": "live"})
    finally:
        release.set()
        scheduler.stop(timeout=3)
    report.assert_called_once()


def test_cancelled_cron_does_not_count_success_and_recovery_requires_confirmation(tmp_path, monkeypatch):
    from cron.executor import execute_cron_task
    from run.scheduler import CronStore, normalize_task
    from cron import executor

    (tmp_path / "users/alice").mkdir(parents=True)
    store = CronStore(tmp_path, "alice")
    task = normalize_task(title="test", prompt="no external effects", user="alice", type="recurring")
    store.create(task)
    cancel = threading.Event()
    monkeypatch.setattr(executor, "handle_request", lambda *a, **kw: cancel.set())
    result = execute_cron_task(root=tmp_path, user="alice", task_id=task["task_id"], config={}, cancel_event=cancel)
    assert result["status"] == "cancelled"
    assert result["successful_runs"] == 0
    assert result["last_stop_reason"] == "cancelled"
    store.update(task["task_id"], lambda item: {**item, "status": "running", "next_run_at": task["next_run_at"]})
    store.recover_interrupted()
    recovered = store.read(task["task_id"])
    assert recovered["status"] == "paused"
    assert recovered["next_run_at"] == ""
    assert recovered["last_stop_reason"] == "interrupted_pending_confirmation"


@pytest.mark.parametrize("terminal", ["interrupted", "completed", "failed", "cancelled"])
def test_terminal_background_job_cannot_be_revived(tmp_path, terminal):
    from run.tools.background_jobs import read_background_job, update_background_job
    directory = tmp_path / "users/alice/runtime/background_jobs"
    directory.mkdir(parents=True)
    job_id = "job_" + "a" * 32
    record = {"schema_version": 1, "job_id": job_id, "user": "alice", "status": terminal}
    (directory / f"{job_id}.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="终态不可变"):
        update_background_job(tmp_path, "alice", job_id, lambda item: {**item, "status": "running"})
    assert read_background_job(tmp_path, "alice", job_id)["status"] == terminal
