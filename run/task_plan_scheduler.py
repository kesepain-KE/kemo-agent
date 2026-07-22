"""Background dispatcher for approved task plans.

The scheduler owns lifecycle and recovery.  The existing task-plan executor
owns dependency ordering and durable step state, while each runnable step is
performed by a normal main-agent Run so the active plan injection is used.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Iterator

from events import RunEvent
from provider.factory import create_provider
from run.engine import iter_request_events
from run.task_plan_executor import execute_plan
from run.task_plan_store import PlanStore
from run.tools import ToolRegistry, discover_tools
from run.users import list_users


class TaskPlanScheduler:
    """Claim approved plans and execute their steps through the main agent."""

    def __init__(
        self,
        root: Path,
        *,
        poll_interval: float = 1.0,
        provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
        tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
        event_source: Callable[..., Iterator[RunEvent]] = iter_request_events,
        transport_registry: Any | None = None,
        on_error: Callable[[str, BaseException], None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.poll_interval = max(0.2, float(poll_interval))
        self.provider_factory = provider_factory
        self.tool_registry_factory = tool_registry_factory
        self.event_source = event_source
        self.transport_registry = transport_registry
        self.on_error = on_error
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._current: dict[str, str] | None = None
        self._last_result: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._recover_interrupted()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="task-plan-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float = 10.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def wake(self) -> None:
        self._wake_event.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "current": dict(self._current) if self._current else None,
                "last_result": dict(self._last_result) if self._last_result else None,
            }

    def scan_once(self) -> dict[str, Any] | None:
        for user in list_users(self.root):
            if self._stop_event.is_set():
                return None
            plans = sorted(
                PlanStore(self.root, user).list_plans(),
                key=lambda item: str(item.get("created_at") or ""),
            )
            for plan in plans:
                if plan.get("status") != "approved":
                    continue
                return self._execute(user, str(plan.get("plan_id") or ""))
        return None

    def _execute(self, user: str, plan_id: str) -> dict[str, Any]:
        with self._lock:
            self._current = {"user": user, "plan_id": plan_id}

        def agent_events(request: dict[str, Any]) -> Iterator[RunEvent]:
            if self.transport_registry is not None:
                request["_transport_registry"] = self.transport_registry
            return iter(
                self.event_source(
                    request,
                    root=self.root,
                    provider_factory=self.provider_factory,
                    tool_registry_factory=self.tool_registry_factory,
                    cancel_event=self._stop_event,
                )
            )

        terminal: dict[str, Any] | None = None
        errors: list[dict[str, Any]] = []
        try:
            for event in execute_plan(
                root=self.root,
                user=user,
                plan_id=plan_id,
                cancel_event=self._stop_event,
                agent_event_source=agent_events,
            ):
                if event.type == "done":
                    terminal = dict(event.metadata)
                elif event.type == "error":
                    errors.append(dict(event.error or {}))
            plan = PlanStore(self.root, user).read(plan_id)
            result = {
                "user": user,
                "plan_id": plan_id,
                "status": str(plan.get("status") or ""),
                "terminal": terminal,
                "errors": errors,
            }
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._report_error(f"task_plan:{user}:{plan_id}", exc)
            result = {
                "user": user,
                "plan_id": plan_id,
                "status": "error",
                "errors": [
                    {"message": str(exc), "exception_type": type(exc).__name__}
                ],
            }
        with self._lock:
            self._last_result = result
            self._current = None
        return result

    def _recover_interrupted(self) -> None:
        for user in list_users(self.root):
            try:
                PlanStore(self.root, user).recover_interrupted()
            except Exception as exc:
                self._report_error(f"task_plan_recovery:{user}", exc)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                executed = self.scan_once()
            except Exception as exc:
                self._report_error("task_plan_scheduler", exc)
                executed = None
            if executed is not None:
                continue
            self._wake_event.wait(self.poll_interval)
            self._wake_event.clear()

    def _report_error(self, component: str, exc: BaseException) -> None:
        if self.on_error is not None:
            try:
                self.on_error(component, exc)
            except Exception:
                pass
