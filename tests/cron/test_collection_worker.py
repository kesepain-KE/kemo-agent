"""Collection must have its own execution process and scheduling leadership."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from cron.scheduler import CronScheduler, ensure_expand_task, ensure_perception_task
from run.config import configured_expand_update_interval, system_update_rate
from run.scheduler import CronStore, SystemCronLease


def test_collection_defaults_and_canonical_interval():
    assert system_update_rate({}, "sense_update_rate") == 5
    assert system_update_rate({}, "expand_update_rate") == 30
    assert configured_expand_update_interval({}) == 30
    config = {"task_cron_system": {"expand_update_rate": 7},
              "cron": {"task_cron_system": {"expand_update": {"interval_seconds": 31}}}}
    assert system_update_rate(config, "expand_update_rate") == 31
    assert system_update_rate({"task_cron_system": {"expand_update_rate": 7}}, "expand_update_rate") == 7


def test_collector_has_an_independent_cross_process_lease(tmp_path):
    host = SystemCronLease(tmp_path)
    worker = SystemCronLease(tmp_path, namespace="collector-perception")
    contender = SystemCronLease(tmp_path, namespace="collector-perception")
    try:
        assert host.try_acquire() and worker.try_acquire()
        assert not contender.try_acquire()
    finally:
        host.release()
        worker.release()
        contender.release()


def test_cli_background_scheduler_also_separates_collection(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from run.infra import cli_interactive

    monkeypatch.setattr(cli_interactive, "run_interactive", SimpleNamespace(), raising=False)
    with patch("cron.scheduler.CronScheduler") as scheduler, patch("cron.collector.CollectorSupervisor") as collector:
        scheduler.return_value.running = False
        for command in ("/cron-start", "/cron-stop"):
            cli_interactive._interactive_command(
                command, root=tmp_path, user="a", source="cli", session_id="s", stdout=io.StringIO(),
            )
        assert scheduler.call_args.kwargs["excluded_system_actions"] == {"perception_update", "expand_update"}
        collector.return_value.start.assert_called_once()
        collector.return_value.stop.assert_called_once()


def test_host_excludes_collection_and_worker_never_dispatches_user_tasks(tmp_path):
    config = {}
    ensure_perception_task(tmp_path, config)
    ensure_expand_task(tmp_path, config)
    store = CronStore(tmp_path, "__system__", system=True)
    for action in ("perception_update", "expand_update"):
        store.update(action, lambda task: {**task, "next_run_at": "2000-01-01T00:00:00+08:00"})
    calls = []

    def execute(**kwargs):
        calls.append(kwargs["task_id"])
        return {"status": "completed", "data_changed": False}

    host = CronScheduler(tmp_path, excluded_system_actions={"perception_update", "expand_update"})
    with patch("cron.scheduler.execute_cron_task", side_effect=execute):
        assert host.scan_once() == 0
    worker = CronScheduler(
        tmp_path, system_actions={"perception_update"}, include_user_tasks=False,
        lease_namespace="collector-perception",
    )
    with patch("cron.scheduler.execute_cron_task", side_effect=execute), patch.object(
        worker, "_scan_user_tasks", side_effect=AssertionError("collector cannot dispatch conversations"),
    ):
        assert worker.scan_once() == 1
    assert calls == ["perception_update"]


def make_collection_root(root):
    (root / "config").mkdir()
    (root / "config/global_config.yaml").write_text(
        "task_cron_system:\n"
        "  sense_update_rate: 1\n"
        "cron:\n"
        "  task_cron_system:\n"
        "    expand_update:\n"
        "      interval_seconds: 2\n",
        "utf-8",
    )
    module = root / "sense/probe"
    module.mkdir(parents=True)
    (module / "sense.json").write_text(json.dumps({"start_update": "update.py"}), "utf-8")
    (module / "update.py").write_text(
        "from pathlib import Path\nimport time\n"
        "def update():\n"
        "    with Path(__file__).with_name('ticks.txt').open('a') as stream:\n"
        "        stream.write(str(time.time())+'\\n')\n", "utf-8",
    )
    return module / "ticks.txt"


def eventually(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError("collection process did not reach expected state")


def test_actual_collector_survives_parent_exit_and_restarts_after_worker_exit(tmp_path):
    from cron.collector import CollectorSupervisor, collector_status

    ticks = make_collection_root(tmp_path)
    source = Path(__file__).resolve().parents[2]
    launcher = subprocess.Popen([
        sys.executable, "-c",
        "import sys,time; from pathlib import Path; from cron.collector import CollectorSupervisor; "
        "worker=CollectorSupervisor(Path(sys.argv[1])); worker.start(); time.sleep(60)",
        str(tmp_path),
    ], cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **__import__("run.infra", fromlist=["hidden_subprocess_kwargs"]).hidden_subprocess_kwargs())
    supervisor = CollectorSupervisor(tmp_path)
    worker_pid = 0
    try:
        first = eventually(lambda: collector_status(tmp_path))
        worker_pid = first["pid"]
        assert worker_pid not in {os.getpid(), launcher.pid}
        eventually(lambda: ticks.exists() and len(ticks.read_text().splitlines()) >= 2)
        launcher.terminate()  # This test owns the launcher; its worker is detached.
        launcher.wait(timeout=5)
        previous = len(ticks.read_text().splitlines())
        eventually(lambda: len(ticks.read_text().splitlines()) > previous)
        assert collector_status(tmp_path)["pid"] == worker_pid
        supervisor.start()
        # Terminate only this test's independently identified worker process.
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(1, False, worker_pid)
            assert handle
            try:
                assert kernel.TerminateProcess(handle, 1)
            finally:
                kernel.CloseHandle(handle)
        else:
            import signal
            os.kill(worker_pid, signal.SIGKILL)
        second = eventually(lambda: (
            status if (status := collector_status(tmp_path)) and status["pid"] != worker_pid else None
        ))
        assert second["pid"] != worker_pid
        previous = len(ticks.read_text().splitlines())
        eventually(lambda: len(ticks.read_text().splitlines()) > previous)
    finally:
        if launcher.poll() is None:
            launcher.terminate()
            launcher.wait(timeout=5)
        supervisor.stop(timeout=10)


def test_expand_modules_refresh_in_parallel_without_changing_result_order(tmp_path):
    from cron.executor import _execute_expand_update

    barrier = threading.Barrier(3)
    for name in ("a", "b", "c"):
        directory = tmp_path / "expand" / "global" / name
        directory.mkdir(parents=True)
        (directory / "expand.json").write_text(json.dumps({"start_update": "update.py"}), "utf-8")
        (directory / "update.py").write_text("", "utf-8")

    def update(*args, **kwargs):
        barrier.wait(timeout=1)
        return {"ok": True}

    with patch("cron.executor._run_module_updater", side_effect=update):
        result = _execute_expand_update(tmp_path, "__system__", config={}, cancel_event=None)
    assert result["status"] == "completed"
    assert result["updated"] == ["global/a", "global/b", "global/c"]
