"""Detached collection execution, independent of conversation host lifetime."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any
import uuid

from run.infra import detached_subprocess_kwargs, process_identity_matches, process_snapshot
from run.scheduler import SystemCronLease


COLLECTION_ACTIONS = {"perception_update", "expand_update"}


def _path(root: Path, name: str) -> Path:
    return root / "runtime" / f"collector-{name}.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
    temporary.replace(path)


def collector_status(root: Path) -> dict[str, Any]:
    """Read only status and OS identity; never trust a recycled PID."""
    status = _read(_path(root, "status"))
    pid = status.get("pid")
    if type(pid) is not int or pid <= 0 or status.get("state") in {"stopped", "failed"}:
        return {}
    snapshot = process_snapshot(pid)
    if process_identity_matches(
        snapshot, process_started_at=str(status.get("process_started_at") or ""),
        process_name=str(status.get("process_name") or ""),
    ) is not True:
        return {}
    return status


class CollectorSupervisor:
    """Restart a lost worker while the host is alive; a worker survives a crash."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._next_start = 0.0

    def ensure_running(self) -> dict[str, Any]:
        with self._lock:
            if status := collector_status(self.root):
                return status
            if time.monotonic() < self._next_start:
                return {}
            lease = SystemCronLease(self.root, namespace="collector-launch")
            if not lease.try_acquire():
                return {}
            try:
                if status := collector_status(self.root):
                    return status
                if self._process is not None:
                    self._process.poll()
                token = uuid.uuid4().hex
                process = subprocess.Popen(
                    [sys.executable, "-m", "run.tools.background_worker",
                     "--collector", str(self.root), token],
                    cwd=Path(__file__).resolve().parents[1],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    **detached_subprocess_kwargs(),
                )
                self._process = process
                self._next_start = time.monotonic() + 5
                snapshot = process_snapshot(process.pid)
                status = {
                    "schema_version": 1, "pid": process.pid, "token": token, "state": "starting",
                    "process_started_at": snapshot.get("process_started_at", ""),
                    "process_name": snapshot.get("process_name", ""), "heartbeat_at": time.time(),
                }
                _write(_path(self.root, "status"), status)
                return status
            finally:
                lease.release()

    def _monitor(self) -> None:
        while not self._stop.is_set():
            try:
                self.ensure_running()
            except Exception as exc:
                logging.getLogger(__name__).error("采集进程监护失败：%s", type(exc).__name__)
            self._stop.wait(1)
        if self._process is not None:
            self._process.poll()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.ensure_running()
        self._thread = threading.Thread(target=self._monitor, name="collector-supervisor", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 10) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(2, timeout))
        status = collector_status(self.root)
        if not status:
            return
        _write(_path(self.root, "stop"), {"token": status["token"]})
        deadline = time.monotonic() + max(0, timeout)
        while time.monotonic() < deadline:
            if not collector_status(self.root):
                if self._process is not None:
                    self._process.poll()
                return
            threading.Event().wait(.05)
        raise TimeoutError("采集进程尚未完成退出，保留真实运行状态")


def run_collector(root: Path, token: str) -> int:
    """Own both collection schedules without starting conversations or WebSocket."""
    from cron.scheduler import CronScheduler, ensure_expand_task, ensure_perception_task
    from run.config import read_yaml_object

    root = root.resolve()
    lease = SystemCronLease(root, namespace="collector-process")
    if not lease.try_acquire():
        return 0
    schedulers = []
    snapshot = process_snapshot(os.getpid())
    status = {
        "schema_version": 1, "pid": os.getpid(), "token": token, "state": "running",
        "process_started_at": snapshot.get("process_started_at", ""),
        "process_name": snapshot.get("process_name", ""),
    }
    failed = False
    try:
        config = read_yaml_object(root / "config/global_config.yaml")
        ensure_perception_task(root, config)
        ensure_expand_task(root, config)
        for action in sorted(COLLECTION_ACTIONS):
            scheduler = CronScheduler(
                root, config=config, poll_interval=.2, system_actions={action},
                include_user_tasks=False, lease_namespace=f"collector-{action.replace('_', '-')}",
            )
            schedulers.append(scheduler)
            scheduler.start()
        next_config_check = time.monotonic() + 2
        while root.is_dir() and _read(_path(root, "stop")).get("token") != token:
            if time.monotonic() >= next_config_check:
                latest_config = read_yaml_object(root / "config/global_config.yaml")
                if latest_config != config:
                    config = latest_config
                    ensure_perception_task(root, config)
                    ensure_expand_task(root, config)
                    for scheduler in schedulers:
                        scheduler._config = config
                next_config_check = time.monotonic() + 2
            status["heartbeat_at"] = time.time()
            status["schedulers"] = {action: scheduler.running for action, scheduler in
                                    zip(sorted(COLLECTION_ACTIONS), schedulers)}
            for scheduler in schedulers:
                if not scheduler.running:
                    scheduler.start()
            _write(_path(root, "status"), status)
            threading.Event().wait(.5)
    except Exception as exc:
        failed = True
        status["error_type"] = type(exc).__name__
    finally:
        for scheduler in schedulers:
            scheduler.stop(timeout=10)
        while any(
            scheduler.running or (
                scheduler._system_worker is not None and scheduler._system_worker.is_alive()
            ) for scheduler in schedulers
        ):
            status["state"] = "stopping"
            if root.is_dir():
                _write(_path(root, "status"), status)
            threading.Event().wait(.1)
        status["state"] = "failed" if failed else "stopped"
        if root.is_dir() and _read(_path(root, "status")).get("token") == token:
            _write(_path(root, "status"), status)
        lease.release()
    return int(failed)
