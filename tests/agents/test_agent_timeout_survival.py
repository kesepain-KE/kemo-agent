from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from plugins.subagent_dispatch.tool import run as dispatch_subagent
from run.agent_queue import AgentScheduler
from run.agent_runner import (
    AgentCancelledError,
    AgentRunResult,
    AgentRunner,
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

        with patch("run.agent_runner._load_executor", return_value=execute):
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

        with patch("run.agent_runner._load_executor", return_value=execute):
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

        with patch("run.agent_runner._load_executor", return_value=execute):
            with self.assertRaises(AgentTimeoutError):
                self.runner().run(
                    "context_manage",
                    _INPUT,
                    timeout=0.01,
                    timeout_survival_seconds=0,
                    event_callback=events.append,
                )

        self.assertEqual(events[-1].metadata["status"], "timed_out")

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

        with patch("run.agent_runner._load_executor", return_value=execute):
            thread = threading.Thread(target=invoke)
            thread.start()
            self.assertTrue(started.wait(1))
            time.sleep(0.05)
            cancel_event.set()
            thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(raised), 1)
        self.assertIsInstance(raised[0], AgentCancelledError)

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
            definition = SimpleNamespace(name="custom")
            result = AgentRunResult("custom", {"ok": True}, "{}", {}, "mock")
            with (
                patch("plugins.subagent_dispatch.tool._public", return_value=[definition]),
                patch("plugins.subagent_dispatch.tool.load_config", return_value={}),
                patch(
                    "plugins.subagent_dispatch.tool.prepare_main_agent_invocation",
                    return_value=SimpleNamespace(payload={}, synchronous_only=False),
                ),
                patch("plugins.subagent_dispatch.tool.AgentRunner") as runner,
                patch(
                    "plugins.subagent_dispatch.tool.persist_main_agent_result",
                    return_value=None,
                ),
            ):
                runner.return_value.run.return_value = result
                dispatched = dispatch_subagent(
                    "call",
                    agent="custom",
                    input={},
                    timeout=3,
                    context=context,
                )

            runner.return_value.run.assert_called_once_with("custom", {}, timeout=3.0)
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


if __name__ == "__main__":
    unittest.main()
