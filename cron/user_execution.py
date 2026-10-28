"""Bounded independent cron executors; deadlines never falsify executor exit."""
from __future__ import annotations
import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from cron.system_execution import _system_result_summary
BEIJING = ZoneInfo("Asia/Shanghai")

def _report_execution_error(self, user, exc):
    from run.infra import record_runtime_event
    self._execution_failures += 1
    logging.getLogger(__name__).error("Cron execution failure (%s): %s", user, type(exc).__name__, exc_info=exc)
    try:
        record_runtime_event(self.root, user, category="backend", name="cron_scheduler",
                             status="failed", error_type=type(exc).__name__)
    except Exception:
        logging.getLogger(__name__).exception("Unable to persist cron failure marker")
    if self.on_error:
        try:
            self.on_error(user, exc)
        except Exception:
            logging.getLogger(__name__).exception("Cron error callback failed")

def _dispatch_user_task(self, user, task):
    key = (user, str(task["task_id"]))
    with self._lock:
        maximum = max(1, int((self._config.get("provider_runtime") or {}).get("max_concurrent_requests", 10)))
        if key in self._user_executions or len(self._user_executions) >= maximum:
            return False
        cancel = threading.Event()
        done = threading.Event()
        timeout = max(1.0, float((self._config.get("agent_runtime") or {}).get("default_timeout", 600)))
        def watchdog():
            deadline = time.monotonic() + timeout
            while not done.wait(.1):
                if self._stop_event.is_set() or time.monotonic() >= deadline:
                    setattr(cancel, "stop_reason", "stopped" if self._stop_event.is_set() else "timeout")
                    cancel.set()
                    if not self._stop_event.is_set():
                        self._report_execution_error(user, TimeoutError("Cron deadline exceeded; waiting for real executor exit"))
                    return
        def execute():
            monitor = threading.Thread(target=watchdog, daemon=True, name="cron-deadline")
            monitor.start()
            try:
                self._execute_user_task(user, task, cancel)
            finally:
                done.set()
                with self._lock:
                    self._user_executions.pop(key, None)
        worker = threading.Thread(target=execute, daemon=True, name=f"cron-{user}-{task['task_id']}")
        self._user_executions[key] = (worker, cancel)
        worker.start()
        return True

def _execute_user_task(self, user, task, cancel_event):
    # Keep the scheduler's injection/mocking surface stable.
    from cron import scheduler as runtime
    execute_cron_task = runtime.execute_cron_task
    LogStore = runtime.LogStore
    executed = 0
    task_id = str(task["task_id"])
    started_at = datetime.now(BEIJING)
    started = time.monotonic()
    try:
        result = execute_cron_task(
            root=self.root,
            user=user,
            task_id=task_id,
            provider_factory=self.provider_factory,
            tool_registry_factory=self.tool_registry_factory,
            cancel_event=cancel_event,
            transport_registry=self._transport_registry,
        )
        executed += 1
        try:
            result_status = str(result.get("status") or "completed")
            execution_error = result.get("_execution_error")
            LogStore(self.root).append_cron(
                {
                    "schema_version": 1,
                    "executed_at": started_at.isoformat(),
                    "user": user,
                    "task_id": task_id,
                    "status": (
                        "success"
                        if result_status in {"enabled", "completed"}
                        else result_status
                    ),
                    "duration_ms": round(
                        (time.monotonic() - started) * 1000
                    ),
                    "result": _system_result_summary(result),
                    "error": execution_error if isinstance(execution_error, dict) else None,
                }
            )
        except Exception as log_error:
            self._report_execution_error(user, log_error)
        if self.on_task_executed:
            self.on_task_executed(user, task["task_id"], result)
    except Exception as exc:
        try:
            LogStore(self.root).append_cron(
                {
                    "schema_version": 1,
                    "executed_at": started_at.isoformat(),
                    "user": user,
                    "task_id": task_id,
                    "status": "failed",
                    "duration_ms": round(
                        (time.monotonic() - started) * 1000
                    ),
                    "result": {},
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
            )
        except Exception as log_error:
            self._report_execution_error(user, log_error)
        self._report_execution_error(user, exc)
    return executed


def dispatch_system_scan(self, now):
    """System maintenance cannot block user dispatch or lose its OS lease."""
    with self._lock:
        if self._system_worker is not None and self._system_worker.is_alive():
            return

        def execute():
            try:
                self._scan_system_tasks(now)
            except Exception as exc:
                self._report_execution_error("__system__", exc)
            finally:
                # An uncooperative function retains leadership until it
                # actually returns, including after stop(timeout=...).
                if self._stop_event.is_set():
                    self.flush_persistence()
                    self._system_lease.release()

        self._system_worker = threading.Thread(target=execute, daemon=True, name="cron-system")
        self._system_worker.start()

