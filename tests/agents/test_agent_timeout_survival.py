from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from plugins.subagent_dispatch.tool import _wait_for_task, run as dispatch_subagent
from run.agents import AgentScheduler
from run.tools import execution_watchdog_snapshot
from run.agents import (
    AgentCancelledError,
    AgentQueueError,
    AgentRunResult,
    AgentRunner,
    AgentTaskNotFoundError,
    AgentTaskWaitTimeout,
    AgentTimeoutError,
)
from run.agents import AgentError, discover_agents


_INPUT = {"previous_summary": None, "rounds": [], "trigger": "manual"}
_CONFIG = {
    "provider": {
        "type": "kemo",
        "base_url": "http://127.0.0.1:1",
        "api_key": "test-key",
        "model": "test-model",
    },
    "agent_runtime": {"timeout_survival_seconds": 0},
}


def _result() -> AgentRunResult:
    return AgentRunResult(
        agent="context_manage",
        data={"ok": True},
        raw_text="{}",
        usage={},
        model="mock",
    )


class AgentTimeoutSurvivalTests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def runner(self) -> AgentRunner:
        return AgentRunner(
            self.root,
            "kesepain",
            config=_CONFIG,
            provider_factory=lambda _config: object(),
        )

    def test_timeout_survival_completes_in_window(self) -> None:
        events = []

        def execute(_context, _input):
            time.sleep(0.06)
            return _result()

        with patch("run.agents.runner._load_executor", return_value=execute):
            result = self.runner().run(
                "context_manage",
                _INPUT,
                timeout=0.015,
                timeout_survival_seconds=0.15,
                event_callback=events.append,
            )

        self.assertTrue(result.metadata["completed_after_timeout"])
        self.assertEqual(result.metadata["timeout_seconds"], 0.015)
        self.assertEqual(
            [event.metadata["status"] for event in events],
            ["started", "completed_after_timeout"],
        )

    def test_timeout_survival_expires_and_preserves_timeout_status(self) -> None:
        events = []

        def execute(_context, _input):
            time.sleep(0.15)
            return _result()

        with patch("run.agents.runner._load_executor", return_value=execute):
            with self.assertRaises(AgentTimeoutError) as raised:
                self.runner().run(
                    "context_manage",
                    _INPUT,
                    timeout=0.01,
                    timeout_survival_seconds=0.02,
                    event_callback=events.append,
                )

        self.assertTrue(raised.exception.process_terminated)
        self.assertEqual(events[-1].metadata["status"], "timed_out")
        self.assertIn("存活期", str(raised.exception))

    def test_timeout_survival_zero_keeps_legacy_timeout(self) -> None:
        events = []

        def execute(_context, _input):
            time.sleep(0.05)
            return _result()

        with patch("run.agents.runner._load_executor", return_value=execute):
            with self.assertRaises(AgentTimeoutError):
                self.runner().run(
                    "context_manage",
                    _INPUT,
                    timeout=0.01,
                    timeout_survival_seconds=0,
                    event_callback=events.append,
                )

        self.assertEqual(events[-1].metadata["status"], "timed_out")

    def test_non_cooperative_timeout_is_reported_as_still_running(self) -> None:
        events = []
        release = threading.Event()

        def execute(_context, _input):
            release.wait(1)
            return _result()

        try:
            with (
                patch("run.agents.runner._load_executor", return_value=execute),
                patch("run.agents.runner._AGENT_TIMEOUT_CLEANUP_GRACE", 0.01),
            ):
                with self.assertRaises(AgentTimeoutError) as raised:
                    self.runner().run(
                        "context_manage",
                        _INPUT,
                        timeout=0.01,
                        timeout_survival_seconds=0,
                        event_callback=events.append,
                    )
            self.assertFalse(raised.exception.process_terminated)
            self.assertEqual(events[-1].metadata["status"], "timed_out_running")
            self.assertFalse(events[-1].metadata["process_terminated"])
        finally:
            release.set()
            deadline = time.monotonic() + 1
            while execution_watchdog_snapshot()["abandoned"] and time.monotonic() < deadline:
                time.sleep(0.01)

    def test_timeout_survival_cancel_during_window(self) -> None:
        started = threading.Event()
        cancel_event = threading.Event()
        raised: list[BaseException] = []

        def execute(_context, _input):
            started.set()
            time.sleep(0.3)
            return _result()

        def invoke() -> None:
            try:
                self.runner().run(
                    "context_manage",
                    _INPUT,
                    cancel_event=cancel_event,
                    timeout=0.02,
                    timeout_survival_seconds=0.4,
                )
            except BaseException as exc:
                raised.append(exc)

        with patch("run.agents.runner._load_executor", return_value=execute):
            thread = threading.Thread(target=invoke)
            thread.start()
            self.assertTrue(started.wait(1))
            time.sleep(0.05)
            cancel_event.set()
            thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(raised), 1)
        self.assertIsInstance(raised[0], AgentCancelledError)
        deadline = time.monotonic() + 1
        while execution_watchdog_snapshot()["abandoned"] and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_scheduler_passes_survival_and_exposes_completion_marker(self) -> None:
        registry = discover_agents(self.root)

        class RecordingRunner:
            config = {"agent_runtime": {"timeout_survival_seconds": 7}}

            def __init__(self) -> None:
                self.calls = []

            def refresh_registry(self):
                return registry

            def run(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return AgentRunResult(
                    "self_improve",
                    {"ok": True},
                    "{}",
                    {},
                    "mock",
                    metadata={"completed_after_timeout": True},
                )

        runner = RecordingRunner()
        scheduler = AgentScheduler(runner)
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        task_id = scheduler.submit("self_improve", {"value": 1}, timeout=3)
        scheduler.wait(task_id, 1)
        snapshot = scheduler.get(task_id)

        self.assertEqual(runner.calls[0][1]["timeout"], 3)
        self.assertEqual(runner.calls[0][1]["timeout_survival_seconds"], 7.0)
        self.assertEqual(snapshot["survival_seconds"], 7.0)
        self.assertTrue(snapshot["completed_after_timeout"])

    def test_dispatch_validates_and_forwards_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = {"root": str(root), "user": "alice"}
            definition = SimpleNamespace(name="custom", timeout=10.0)
            result = AgentRunResult("custom", {"ok": True}, "{}", {}, "mock")
            submitted: list[tuple[tuple[object, ...], dict[str, object]]] = []

            class RecordingScheduler:
                def submit(self, *args, **kwargs):
                    submitted.append((args, kwargs))
                    return "agent-task-test"

                def wait(self, task_id, timeout=None):
                    self.last_wait = (task_id, timeout)
                    return result

            with (
                patch("plugins.subagent_dispatch.tool._public", return_value=[definition]),
                patch("plugins.subagent_dispatch.tool.load_config", return_value={}),
                patch(
                    "plugins.subagent_dispatch.tool.prepare_main_agent_invocation",
                    return_value=SimpleNamespace(payload={}, synchronous_only=False),
                ),
                patch(
                    "plugins.subagent_dispatch.tool.get_agent_scheduler",
                    return_value=RecordingScheduler(),
                ),
                patch(
                    "plugins.subagent_dispatch.tool.persist_main_agent_result",
                    return_value=None,
                ),
            ):
                dispatched = dispatch_subagent(
                    "call",
                    agent="custom",
                    input={},
                    timeout=3,
                    context=context,
                )

            self.assertEqual(submitted[0][0], ("custom", {}))
            self.assertEqual(submitted[0][1]["timeout"], 3.0)
            self.assertEqual(submitted[0][1]["allow_sync"], True)
            self.assertEqual(dispatched["status"], "completed")

            with (
                patch("plugins.subagent_dispatch.tool._public", return_value=[definition]),
                patch("plugins.subagent_dispatch.tool.load_config", return_value={}),
                self.assertRaisesRegex(AgentError, "timeout 必须是正数"),
            ):
                dispatch_subagent(
                    "call",
                    agent="custom",
                    input={},
                    timeout=0,
                    context=context,
                )

    def test_wait_returns_timed_out_running_snapshot_instead_of_losing_task_id(self) -> None:
        class TimedOutRunningScheduler:
            def get(self, task_id):
                return {"id": task_id, "status": "timed_out_running"}

            def wait(self, task_id, timeout=None):
                del task_id, timeout
                raise AgentQueueError("仍在运行")

        completed, snapshot = _wait_for_task(
            TimedOutRunningScheduler(),
            "agent-task-still-running",
            timeout=0,
        )

        self.assertFalse(completed)
        self.assertEqual(snapshot["status"], "timed_out_running")
        self.assertEqual(snapshot["id"], "agent-task-still-running")

    def test_dispatch_timeout_returns_task_that_status_and_cancel_can_follow(self) -> None:
        definition = SimpleNamespace(
            name="custom",
            timeout=10.0,
            execution="sync",
        )

        class RunningScheduler:
            def __init__(self) -> None:
                self.status = "running"
                self.cancelled = False

            def submit(self, *args, **kwargs):
                del args, kwargs
                return "agent-task-running"

            def wait(self, task_id, timeout=None):
                del task_id, timeout
                raise AgentTaskWaitTimeout("等待超时")

            def get(self, task_id):
                return {"id": task_id, "status": self.status}

            def cancel(self, task_id):
                self.cancelled = True
                self.status = "cancelled"
                return True

        scheduler = RunningScheduler()
        context = {"root": str(self.root), "user": "kesepain"}
        with (
            patch("plugins.subagent_dispatch.tool._public", return_value=[definition]),
            patch("plugins.subagent_dispatch.tool.load_config", return_value={}),
            patch(
                "plugins.subagent_dispatch.tool.prepare_main_agent_invocation",
                return_value=SimpleNamespace(payload={}, synchronous_only=False),
            ),
            patch(
                "plugins.subagent_dispatch.tool.get_agent_scheduler",
                return_value=scheduler,
            ),
        ):
            running = dispatch_subagent(
                "call",
                agent="custom",
                input={},
                timeout=0.01,
                context=context,
            )
            status = dispatch_subagent(
                "status",
                task_id="agent-task-running",
                context=context,
            )
            cancelled = dispatch_subagent(
                "cancel",
                task_id="agent-task-running",
                context=context,
            )

        self.assertEqual(running["status"], "running")
        self.assertEqual(running["task_id"], "agent-task-running")
        self.assertEqual(status["status"], "running")
        self.assertTrue(cancelled["cancelled"])
        self.assertTrue(scheduler.cancelled)

    def test_cancel_race_preserves_non_terminated_timeout_status(self) -> None:
        registry = discover_agents(self.root)
        started = threading.Event()

        class TimeoutAfterCancelRunner:
            config = {"agent_runtime": {"timeout_survival_seconds": 0}}

            def refresh_registry(self):
                return registry

            def run(self, *args, cancel_event, **kwargs):
                del args, kwargs
                started.set()
                cancel_event.wait(1)
                raise AgentTimeoutError("仍在运行", process_terminated=False)

        scheduler = AgentScheduler(TimeoutAfterCancelRunner())
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        task_id = scheduler.submit("self_improve", {"value": 1}, allow_sync=True)
        self.assertTrue(started.wait(1))
        self.assertTrue(scheduler.cancel(task_id))

        with self.assertRaises(AgentQueueError):
            scheduler.wait(task_id, 1)
        snapshot = scheduler.get(task_id)
        self.assertEqual(snapshot["status"], "timed_out_running")
        self.assertTrue(snapshot["error"]["cancel_requested"])
        self.assertFalse(snapshot["error"]["process_terminated"])
        self.assertTrue(scheduler.cancel(task_id))

    def test_scheduler_reconciles_detached_completion(self) -> None:
        registry = discover_agents(self.root)
        completion = Future()

        class DetachedRunner:
            config = {"agent_runtime": {"timeout_survival_seconds": 0}}

            def refresh_registry(self):
                return registry

            def run(self, *args, **kwargs):
                del args, kwargs
                raise AgentTimeoutError(
                    "仍在运行",
                    process_terminated=False,
                    completion_future=completion,
                )

        scheduler = AgentScheduler(DetachedRunner())
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        task_id = scheduler.submit("self_improve", {"value": 1}, allow_sync=True)

        deadline = time.monotonic() + 1
        while scheduler.get(task_id)["status"] != "timed_out_running":
            if time.monotonic() >= deadline:
                self.fail("子代理未进入 timed_out_running")
            time.sleep(0.01)

        completion.set_result(_result())

        deadline = time.monotonic() + 1
        snapshot = scheduler.get(task_id)
        while snapshot["status"] != "completed":
            if time.monotonic() >= deadline:
                self.fail("脱离调用方后完成的子代理未收敛到 completed")
            time.sleep(0.01)
            snapshot = scheduler.get(task_id)

        self.assertTrue(snapshot["completed_after_timeout"])
        self.assertTrue(snapshot["result"]["metadata"]["completed_after_detach"])
        self.assertIsNone(snapshot["error"])

    def test_scheduler_reconciles_real_runner_timeout_completion(self) -> None:
        release = threading.Event()

        def execute(_context, _input):
            release.wait(1)
            return _result()

        scheduler = AgentScheduler(self.runner())
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        try:
            with (
                patch("run.agents.runner._load_executor", return_value=execute),
                patch("run.agents.runner._AGENT_TIMEOUT_CLEANUP_GRACE", 0.01),
            ):
                task_id = scheduler.submit(
                    "context_manage",
                    _INPUT,
                    timeout=0.01,
                    timeout_survival_seconds=0,
                    allow_sync=True,
                )
                deadline = time.monotonic() + 1
                while scheduler.get(task_id)["status"] != "timed_out_running":
                    if time.monotonic() >= deadline:
                        self.fail("真实 AgentRunner 未进入 timed_out_running")
                    time.sleep(0.01)
                release.set()

                deadline = time.monotonic() + 1
                snapshot = scheduler.get(task_id)
                while snapshot["status"] != "completed":
                    if time.monotonic() >= deadline:
                        self.fail("真实 AgentRunner 脱离后未收敛到 completed")
                    time.sleep(0.01)
                    snapshot = scheduler.get(task_id)
        finally:
            release.set()

        self.assertTrue(snapshot["completed_after_timeout"])
        self.assertEqual(snapshot["result"]["agent"], "context_manage")

    def test_scheduler_uses_submission_config_for_new_runner(self) -> None:
        first_config = dict(_CONFIG)
        first_config["provider"] = {**_CONFIG["provider"], "model": "first-model"}
        second_config = dict(_CONFIG)
        second_config["provider"] = {**_CONFIG["provider"], "model": "second-model"}
        seen_models: list[str] = []

        def execute(context, _input):
            seen_models.append(str(context.runner.config["provider"]["model"]))
            return _result()

        scheduler = AgentScheduler(
            AgentRunner(
                self.root,
                "kesepain",
                config=first_config,
                provider_factory=lambda _config: object(),
            )
        )
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        with patch("run.agents.runner._load_executor", return_value=execute):
            task_id = scheduler.submit(
                "context_manage",
                _INPUT,
                allow_sync=True,
                config=second_config,
            )
            scheduler.wait(task_id, 1)

        self.assertEqual(seen_models, ["second-model"])

    def test_scheduler_worker_is_started_on_demand_and_restarts_after_idle(self) -> None:
        registry = discover_agents(self.root)

        class ImmediateRunner:
            config = {"agent_runtime": {"timeout_survival_seconds": 0}}

            def refresh_registry(self):
                return registry

            def run(self, name, input_data, **kwargs):
                del input_data, kwargs
                return AgentRunResult(name, {"ok": True}, "{}", {}, "mock")

        with patch("run.agents.queue._WORKER_IDLE_SECONDS", 0.01):
            scheduler = AgentScheduler(ImmediateRunner())
            self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
            self.assertIsNone(scheduler._worker)
            first = scheduler.submit("self_improve", {"value": 1}, allow_sync=True)
            scheduler.wait(first, 1)
            deadline = time.monotonic() + 1
            while scheduler._worker is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(scheduler._worker)
            second = scheduler.submit("self_improve", {"value": 2}, allow_sync=True)
            scheduler.wait(second, 1)

    def test_scheduler_bounds_completed_task_retention(self) -> None:
        registry = discover_agents(self.root)

        class ExternalRunner:
            config = {}

            def refresh_registry(self):
                return registry

        scheduler = AgentScheduler(
            ExternalRunner()
        )
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)

        task_ids = []
        for index in range(260):
            task_id = scheduler.submit_callable(
                "external:test",
                {"index": index},
                lambda _cancel: {"status": "completed", "data": {"ok": True}},
            )
            scheduler.wait(task_id, 2)
            task_ids.append(task_id)

        with self.assertRaises(AgentTaskNotFoundError):
            scheduler.get(task_ids[0])
        self.assertLessEqual(len(scheduler._tasks), 256)


if __name__ == "__main__":
    unittest.main()
