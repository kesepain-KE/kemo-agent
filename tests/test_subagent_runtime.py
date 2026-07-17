from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.schema import ChatResponse, Usage
from run.agent_queue import AgentQueueError, AgentScheduler
from run.agent_runner import (
    AgentCancelledError,
    AgentInputError,
    AgentOutputError,
    AgentRunResult,
    AgentRunner,
    AgentTimeoutError,
)
from run.agents import AgentDisabledError, AgentManifestError, discover_agents


SUMMARY = {
    "facts": ["fact"],
    "requirements": [],
    "decisions": [],
    "unfinished": [],
    "tool_results": [],
    "entities": [],
    "narrative": "summary",
}


class MockProvider:
    def __init__(self, *, text: str | None = None, delay: float = 0.0, order=None) -> None:
        self.text = text if text is not None else json.dumps(SUMMARY)
        self.delay = delay
        self.order = order
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        if self.order is not None:
            payload = json.loads(request.messages[1]["content"])
            self.order.append(("start", payload.get("value")))
        if self.delay:
            time.sleep(self.delay)
        if self.order is not None:
            payload = json.loads(request.messages[1]["content"])
            self.order.append(("end", payload.get("value")))
        return ChatResponse(
            text=self.text,
            model=request.model,
            usage=Usage(2, 1, 3, source="mock"),
        )


class StubRunner:
    def __init__(self, registry, order) -> None:
        self.registry = registry
        self.order = order
        self.config = {"agent_runtime": {"queue_maxsize": 0}}
        self.gate = threading.Event()

    def run(self, name, input_data, **kwargs):
        value = input_data["value"]
        self.order.append(("start", value))
        if value == 1:
            self.gate.wait(1)
        self.order.append(("end", value))
        return AgentRunResult(name, {"value": value}, "{}", {}, "mock")


class SubAgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.config = {
            "provider": {
                "type": "kemo",
                "base_url": "http://127.0.0.1:1/v1",
                "api_key_env": "TEST_AGENT_KEY",
                "model": "main-model",
            },
            "agent_models": {"cheap": {"model": "cheap-model"}},
        }

    def runner(self, provider: MockProvider) -> AgentRunner:
        return AgentRunner(
            self.root,
            "kesepain",
            config=self.config,
            provider_factory=lambda _: provider,
        )

    def test_discovery_order_lookup_disabled_and_manifest_validation(self) -> None:
        registry = discover_agents(self.root)
        self.assertEqual(list(registry.agents), sorted(registry.agents, key=str.casefold))
        self.assertEqual(registry.get("context_manage").instruction_file, "上下文管理.txt")

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        directory = root / "agents" / "bad"
        directory.mkdir(parents=True)
        (directory / "职责.txt").write_text("x", "utf-8")
        (directory / "agent.json").write_text("{}", "utf-8")
        with self.assertRaises(AgentManifestError):
            discover_agents(root)

        definition = registry.agents["context_manage"]
        object.__setattr__(definition, "enabled", False)
        try:
            with self.assertRaises(AgentDisabledError):
                registry.get("context_manage")
        finally:
            object.__setattr__(definition, "enabled", True)

    def test_runner_uses_explicit_input_and_model_profile(self) -> None:
        provider = MockProvider()
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "context_manage",
                {"previous_summary": None, "rounds": [{"round": 1}], "trigger": "manual"},
            )
        self.assertEqual(result.data["narrative"], "summary")
        request = provider.requests[0]
        self.assertEqual(request.model, "cheap-model")
        self.assertEqual(len(request.messages), 2)
        self.assertNotIn("main conversation", json.dumps(request.messages, ensure_ascii=False))
        self.assertEqual(json.loads(request.messages[1]["content"])["trigger"], "manual")

    def test_runner_rejects_invalid_input_output_timeout_and_cancel(self) -> None:
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            with self.assertRaises(AgentInputError):
                self.runner(MockProvider()).run("context_manage", {"rounds": []})
            with self.assertRaises(AgentOutputError):
                self.runner(MockProvider(text="not-json")).run(
                    "context_manage",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                )
            with self.assertRaises(AgentTimeoutError):
                self.runner(MockProvider(delay=0.2)).run(
                    "context_manage",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                    timeout=0.02,
                )
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(AgentCancelledError):
                self.runner(MockProvider()).run(
                    "context_manage",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                    cancel_event=cancelled,
                )

    def test_two_users_keep_independent_runner_context(self) -> None:
        first_provider = MockProvider()
        second_provider = MockProvider()
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            first = AgentRunner(
                self.root,
                "alice",
                config=self.config,
                provider_factory=lambda _: first_provider,
            )
            second = AgentRunner(
                self.root,
                "bob",
                config=self.config,
                provider_factory=lambda _: second_provider,
            )
            first.run(
                "context_manage",
                {"previous_summary": None, "rounds": [{"owner": "alice"}], "trigger": "manual"},
            )
            second.run(
                "context_manage",
                {"previous_summary": None, "rounds": [{"owner": "bob"}], "trigger": "manual"},
            )
        first_input = json.loads(first_provider.requests[0].messages[1]["content"])
        second_input = json.loads(second_provider.requests[0].messages[1]["content"])
        self.assertEqual(first_input["rounds"][0]["owner"], "alice")
        self.assertEqual(second_input["rounds"][0]["owner"], "bob")
        self.assertNotIn("bob", first_provider.requests[0].messages[1]["content"])

    def test_subagent_events_are_observable(self) -> None:
        events = []
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            self.runner(MockProvider()).run(
                "context_manage",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
                event_callback=events.append,
            )
        self.assertEqual([event.metadata["status"] for event in events], ["started", "completed"])
        self.assertTrue(all(event.metadata["phase"] == "subagent" for event in events))

    def test_scheduler_is_serial_and_supports_cancel(self) -> None:
        registry = discover_agents(self.root)
        order = []
        runner = StubRunner(registry, order)
        scheduler = AgentScheduler(runner)
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        first = scheduler.submit("self_improve", {"value": 1})
        second = scheduler.submit("self_improve", {"value": 2})
        deadline = time.monotonic() + 1
        while scheduler.get(first)["status"] != "running" and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(scheduler.get(second)["status"], "queued")
        runner.gate.set()
        self.assertEqual(scheduler.wait(first, 1).data["value"], 1)
        self.assertEqual(scheduler.wait(second, 1).data["value"], 2)
        self.assertEqual(order, [("start", 1), ("end", 1), ("start", 2), ("end", 2)])

        runner.gate.clear()
        third = scheduler.submit("self_improve", {"value": 1})
        fourth = scheduler.submit("self_improve", {"value": 4})
        deadline = time.monotonic() + 1
        while scheduler.get(third)["status"] != "running" and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(scheduler.cancel(fourth))
        with self.assertRaises(AgentCancelledError):
            scheduler.wait(fourth, 1)
        runner.gate.set()
        scheduler.wait(third, 1)
        with self.assertRaises(AgentQueueError):
            scheduler.submit("context_manage", {"rounds": [], "trigger": "manual"})


if __name__ == "__main__":
    unittest.main()
