from __future__ import annotations

from typing import Any

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from events import RunEvent
from provider.protocol.enums import IncompleteReason, MessagePhase, MessageRole, ResponseStatus
from provider.protocol.models import (
    KemoResponse,
    Measurement,
    MessageItem,
    ModelCapabilities,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    UnifiedError,
    Usage,
    text_from_content,
)
from provider.schema import ProviderError
from run.agents import AgentQueueError, AgentScheduler, AgentTaskNotFoundError
from run.agents import (
    AgentCancelledError,
    AgentOutputError,
    AgentRunResult,
    AgentRunner,
    AgentTimeoutError,
)
from run.agents import AgentDisabledError, AgentManifestError, discover_agents
from run.agents.runner import AgentProviderError, _run_agent_with_retries
from run.extensions import clear_model_capability_cache


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
    def __init__(
        self,
        *,
        text: str | None = None,
        texts: list[str] | None = None,
        delay: float = 0.0,
        order=None,
        reasoning_supported: bool = True,
        reasoning_efforts: list[str] | None = None,
    ) -> None:
        self.text = text if text is not None else json.dumps(SUMMARY)
        self.texts = list(texts or [])
        self.delay = delay
        self.order = order
        self.reasoning_supported = reasoning_supported
        self.reasoning_efforts = list(
            reasoning_efforts
            if reasoning_efforts is not None
            else ["minimal", "low", "medium", "high", "max"]
        )
        self.requests = []
        self.capability_calls: list[str] = []

    def capabilities(self, model: str, *, capabilities_url: str | None = None):
        del capabilities_url
        self.capability_calls.append(model)
        return ModelCapabilities.model_validate(
            {
                "protocol_version": "2.0",
                "model": model,
                "provider_id": "mock",
                "provider_model": model,
                "task": "llm",
                "reasoning": {
                    "supported": self.reasoning_supported,
                    "efforts": self.reasoning_efforts if self.reasoning_supported else [],
                    "summary": self.reasoning_supported,
                    "returns": ["none", "summary", "auto"] if self.reasoning_supported else ["none", "auto"],
                },
            }
        )

    def create(self, request):
        response_text = (
            self.texts[min(len(self.requests), len(self.texts) - 1)]
            if self.texts
            else self.text
        )
        self.requests.append(request)
        payload = json.loads(text_from_content(request.input[0].content))
        if self.order is not None:
            self.order.append(("start", payload.get("value")))
        if self.delay:
            time.sleep(self.delay)
        if self.order is not None:
            self.order.append(("end", payload.get("value")))
        return KemoResponse(
            request_id=request.request_id,
            status=ResponseStatus.COMPLETED,
            model=request.model,
            output=[
                MessageItem.text(
                    MessageRole.ASSISTANT,
                    response_text,
                    phase=MessagePhase.FINAL_ANSWER,
                )
            ],
            usage=Usage(
                input_tokens=2,
                output_tokens=1,
                total_tokens=3,
                measurement=Measurement.model_validate({
                                'mode': "provider",
                                'exact': True,
                            }),
            ),
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
        clear_model_capability_cache()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        repository = Path(__file__).resolve().parents[2]
        shutil.copytree(repository / "agents", self.root / "agents",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for plugin in ("memory_manage", "skill_creater"):
            shutil.copytree(repository / "plugins" / plugin, self.root / "plugins" / plugin,
                            ignore=shutil.ignore_patterns("__pycache__"))
        for user in ("kesepain", "alice", "bob"):
            (self.root / "users" / user).mkdir(parents=True)
        # Generic AgentRunner schema/retry tests use their own fixture agent.
        # Context compression is covered through the CardStream summarizer.
        directory = self.root / "agents" / "schema_worker"
        directory.mkdir()
        (directory / "agent.json").write_text(json.dumps({
            "schema_version": 2, "name": "schema_worker", "version": "1.0.0",
            "description": "Isolated schema validation fixture", "enabled": True,
            "instruction": "AGENT.md", "trigger": "trigger.md", "executor": "builtin:llm",
            "model_profile": "cheap", "timeout": 600, "execution": "sync", "write_policy": "none",
            "input_schema": {"type": "object", "additionalProperties": True},
            "output_schema": {"type": "object", "required": list(SUMMARY),
                "properties": {key: ({"type": "string"} if key == "narrative" else
                    {"type": "array", "items": {"type": "string"}, "maxItems": 12})
                    for key in SUMMARY}, "additionalProperties": False},
        }), "utf-8")
        (directory / "AGENT.md").write_text("Return the fixture JSON schema.", "utf-8")
        (directory / "trigger.md").write_text("Schema validation fixture trigger.", "utf-8")
        (directory / "agent-config.json").write_text(json.dumps({
            "schema_version": 1, "exposure": {"mode": "internal", "allowed_callers": []},
            "tools": {"plugins": {"allow": []}, "max_iterations": 80},
            "prompt_sources": {"skills": {"shared": [], "user": []},
                               "expand": {"global": [], "shared": [], "user": []}},
            "knowledge": {"scopes": []},
            "context": {"inherit_main_history": False, "inherit_current_request": False},
        }), "utf-8")
        self.config = {
            "provider": {
                "type": "kemo",
                "base_url": "http://127.0.0.1:1",
                "api_key_env": "KEMO_API_KEY",
                "model": "main-model",
                "reasoning_effort": "high",
            },
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
        self.assertEqual(
            set(registry.agents),
            {
                "schema_worker",
                "history_summary",
                "general_worker",
                "memory_temporary_important",
                "self_improve",
                "task_plan",
                "time_plan",
            },
        )
        context_definition = registry.get("schema_worker")
        self.assertEqual(context_definition.version, "1.0.0")
        self.assertEqual(context_definition.instruction_file, "AGENT.md")
        self.assertEqual(context_definition.model_profile, "cheap")
        self.assertEqual(context_definition.timeout, 600.0)
        self.assertEqual(context_definition.input_schema["type"], "object")
        self.assertTrue(context_definition.input_schema["additionalProperties"])
        self.assertEqual(
            context_definition.output_schema["required"],
            list(SUMMARY),
        )
        self.assertFalse(context_definition.output_schema["additionalProperties"])
        task_definition = registry.get("task_plan")
        self.assertEqual(task_definition.capabilities.knowledge_scopes, ("global", "shared"))
        self.assertEqual(task_definition.capabilities.knowledge_body_access, "none")
        summary_definition = registry.get("history_summary")
        self.assertEqual(summary_definition.trigger_file, "trigger.md")
        self.assertTrue(summary_definition.trigger_registration)
        self.assertEqual(summary_definition.version, "1.1.0")
        self.assertEqual(summary_definition.output_schema["required"], ["title", "summary"])
        self.assertFalse(summary_definition.output_schema["additionalProperties"])
        important_definition = registry.get("memory_temporary_important")
        self.assertEqual(important_definition.version, "1.1.2")

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        directory = root / "agents" / "bad"
        directory.mkdir(parents=True)
        (directory / "职责.txt").write_text("x", "utf-8")
        (directory / "agent.json").write_text("{}", "utf-8")
        with self.assertRaises(AgentManifestError):
            discover_agents(root)

        definition = registry.agents["schema_worker"]
        object.__setattr__(definition, "enabled", False)
        try:
            with self.assertRaises(AgentDisabledError):
                registry.get("schema_worker")
        finally:
            object.__setattr__(definition, "enabled", True)

    def test_runner_uses_explicit_input_and_inherits_main_model(self) -> None:
        provider = MockProvider()
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "schema_worker",
                {"previous_summary": None, "rounds": [{"round": 1}], "trigger": "manual"},
            )
        self.assertEqual(result.data["narrative"], "summary")
        request = provider.requests[0]
        self.assertEqual(request.model, "main-model")
        self.assertTrue(request.reasoning.enabled)
        self.assertEqual(request.reasoning.effort, "high")
        self.assertEqual(request.reasoning.return_mode, "content")
        self.assertEqual(len(request.input), 1)
        self.assertIn("[agent_instruction]", request.system_prompt)
        self.assertNotIn("# 操作信息", request.system_prompt)
        self.assertNotIn(
            "main conversation",
            json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
        )
        self.assertEqual(json.loads(text_from_content(request.input[0].content))["trigger"], "manual")

    def test_runner_usage_preserves_cache_and_provider_request_diagnostics(self) -> None:
        first = AgentRunner._usage_dict(
            Usage(
                input_tokens=100,
                cached_input_tokens=70,
                output_tokens=4,
                total_tokens=104,
                reasoning_tokens=2,
                measurement=Measurement.model_validate({
                                'mode': "provider",
                                'exact': True,
                            }),
            )
        )
        second = AgentRunner._usage_dict(
            Usage(
                input_tokens=50,
                cached_input_tokens=20,
                output_tokens=3,
                total_tokens=53,
                measurement=Measurement.model_validate({
                                'mode': "provider",
                                'exact': True,
                            }),
            )
        )

        self.assertEqual(first["cached_tokens"], 70)
        self.assertEqual(first["provider_request_count"], 1)
        self.assertEqual(first["reasoning_tokens"], 2)
        total: dict[str, Any] = {}
        AgentRunner._merge_usage(total, first)
        AgentRunner._merge_usage(total, second)

        self.assertEqual(total["prompt_tokens"], 150)
        self.assertEqual(total["cached_prompt_tokens"], 90)
        self.assertEqual(total["cache_miss_tokens"], 60)
        self.assertEqual(total["provider_request_count"], 2)
        self.assertAlmostEqual(total["cache_hit_rate"], 0.6, places=6)

    def test_runner_usage_does_not_turn_unknown_cache_into_a_miss(self) -> None:
        payload = AgentRunner._usage_dict(
            Usage(
                input_tokens=100,
                cached_input_tokens=None,
                output_tokens=1,
                total_tokens=101,
                measurement=Measurement.model_validate({
                                'mode': "provider",
                                'exact': True,
                            }),
            )
        )
        self.assertNotIn("cached_tokens", payload)

        total: dict[str, Any] = {}
        AgentRunner._merge_usage(total, payload)
        self.assertNotIn("cached_prompt_tokens", total)
        self.assertNotIn("cache_miss_tokens", total)
        self.assertNotIn("cache_hit_rate", total)

    def test_runner_uses_configured_subagent_model_profile(self) -> None:
        provider = MockProvider()
        config = {
            **self.config,
            "agent_models": {"cheap": "summary-model"},
        }
        runner = AgentRunner(
            self.root,
            "kesepain",
            config=config,
            provider_factory=lambda _: provider,
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            runner.run(
                "schema_worker",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )
        self.assertEqual(provider.requests[0].model, "summary-model")

    def test_runner_omits_reasoning_when_kemo_model_declares_it_unsupported(self) -> None:
        provider = MockProvider(reasoning_supported=False)
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            self.runner(provider).run(
                "schema_worker",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )
        request = provider.requests[0]
        self.assertIsNone(request.reasoning)
        self.assertNotIn("reasoning_effort", request.provider_options)

    def test_runner_preserves_declared_max_and_falls_back_to_first_available_effort(self) -> None:
        for efforts, expected in ((["low", "max"], "max"), (["low", "high"], "low")):
            with self.subTest(efforts=efforts):
                clear_model_capability_cache()
                provider = MockProvider(reasoning_efforts=efforts)
                config = {
                    **self.config,
                    "provider": {**self.config["provider"], "reasoning_effort": "max"},
                }
                runner = AgentRunner(
                    self.root,
                    "kesepain",
                    config=config,
                    provider_factory=lambda _: provider,
                )
                with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
                    runner.run(
                        "schema_worker",
                        {"previous_summary": None, "rounds": [], "trigger": "manual"},
                    )
                request = provider.requests[0]
                self.assertEqual(request.reasoning.effort, expected)
                self.assertTrue(request.reasoning.enabled)

    def test_runner_submits_new_gateway_declared_effort_without_mapping(self) -> None:
        provider = MockProvider(reasoning_efforts=["low", "ultra"])
        config = {
            **self.config,
            "provider": {**self.config["provider"], "reasoning_effort": "ultra"},
        }
        runner = AgentRunner(
            self.root,
            "kesepain",
            config=config,
            provider_factory=lambda _: provider,
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            runner.run(
                "schema_worker",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )
        request = provider.requests[0]
        self.assertEqual(request.reasoning.effort, "ultra")
        self.assertTrue(request.reasoning.enabled)

    def test_runner_retries_transient_provider_failure_and_reports_attempts(self) -> None:
        class FlakyProvider(MockProvider):
            def create(_self, request):
                if not _self.requests:
                    _self.requests.append(request)
                    raise ProviderError(
                        "gateway disconnected",
                        category="connection_error",
                        retryable=True,
                        retry_after_ms=0,
                    )
                return super().create(request)

        provider = FlakyProvider()
        events = []
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.agents.runner._agent_retry_delay_seconds",
                return_value=0.0,
            ),
        ):
            result = self.runner(provider).run(
                "schema_worker",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
                event_callback=events.append,
            )

        self.assertEqual(result.data, SUMMARY)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(result.metadata["retry_attempts"], 2)
        events = [event for event in events if event.type != "subagent_progress"]
        self.assertEqual(
            [event.metadata["status"] for event in events],
            ["started", "retrying", "completed"],
        )
        self.assertEqual(events[1].metadata["failed_attempt"], 1)
        self.assertEqual(events[1].metadata["next_attempt"], 2)

    def test_runner_stops_after_initial_attempt_and_five_retries(self) -> None:
        class FailingProvider(MockProvider):
            def create(_self, request):
                _self.requests.append(request)
                raise ProviderError(
                    "gateway unavailable",
                    category="upstream_error",
                    retryable=True,
                    retry_after_ms=0,
                )

        provider = FailingProvider()
        events = []
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.agents.runner._agent_retry_delay_seconds",
                return_value=0.0,
            ),
        ):
            with self.assertRaises(ProviderError) as raised:
                self.runner(provider).run(
                    "schema_worker",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                    event_callback=events.append,
                )

        self.assertEqual(len(provider.requests), 6)
        self.assertTrue(raised.exception.retry_exhausted)
        self.assertTrue(raised.exception.retry_budget_exhausted)
        self.assertEqual(raised.exception.retry_attempts, 6)
        self.assertEqual(raised.exception.retry_max_attempts, 6)
        self.assertFalse(raised.exception.retryable)
        events = [event for event in events if event.type != "subagent_progress"]
        self.assertEqual(
            [event.metadata["status"] for event in events],
            ["started", "retrying", "retrying", "retrying", "retrying", "retrying", "failed"],
        )
        self.assertTrue(events[-1].metadata["retry_budget_exhausted"])

    def test_subagent_retry_budget_is_bounded_despite_progress(self) -> None:
        events: list[RunEvent] = []
        context = SimpleNamespace(
            definition=SimpleNamespace(name="episode_agent"),
            cancel_event=threading.Event(),
            event_callback=events.append,
            task_id="task_episode",
            source="web",
            session_id="episode-session",
        )
        calls = 0

        def run_once(current, _input):
            nonlocal calls
            calls += 1
            if calls <= 8:
                current.retry_state.progress = True
                raise ProviderError(
                    "temporary interruption after progress",
                    category="upstream_error",
                    retryable=True,
                    retry_after_ms=0,
                )
            return AgentRunResult(
                agent="episode_agent",
                data={"ok": True},
                raw_text="done",
                usage={},
                model="test-model",
            )

        with (patch("run.agents.runner._agent_retry_delay_seconds", return_value=0.0),
              self.assertRaises(ProviderError) as raised):
            _run_agent_with_retries(
                run_once,
                context,
                {},
                max_attempts=6,
            )

        retries = [
            event for event in events if event.metadata.get("status") == "retrying"
        ]
        self.assertEqual(calls, 6)
        self.assertTrue(raised.exception.retry_budget_exhausted)
        self.assertEqual([event.metadata["failed_attempt"] for event in retries], list(range(1, 6)))
        self.assertEqual([event.metadata["run_attempt"] for event in retries], list(range(1, 6)))
        self.assertTrue(all(event.metadata["consecutive_failures"] == 1 for event in retries))
        self.assertTrue(all(event.metadata["retry_progress"] for event in retries))
        self.assertTrue(all(event.metadata["retry_progress"] for event in retries))

    def test_runner_retries_gateway_response_when_retryable_is_omitted(self) -> None:
        class FailedResponseProvider(MockProvider):
            def create(_self, request):
                if not _self.requests:
                    _self.requests.append(request)
                    return KemoResponse(
                        request_id=request.request_id,
                        status=ResponseStatus.FAILED,
                        model=request.model,
                        error=UnifiedError.model_validate({
                                  'type': "connection_error",
                                  'code': "PROVIDER_UNAVAILABLE",
                                  'message': "gateway disconnected",
                                  'provider_status': 503,
                              }),
                    )
                return super().create(request)

        provider = FailedResponseProvider()
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.agents.runner._agent_retry_delay_seconds",
                return_value=0.0,
            ),
        ):
            result = self.runner(provider).run(
                "schema_worker",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )

        self.assertEqual(result.data, SUMMARY)
        self.assertEqual(len(provider.requests), 2)

    def test_runner_bounds_output_truncation_retries(self) -> None:
        class TruncatedProvider(MockProvider):
            def create(_self, request):
                _self.requests.append(request)
                return KemoResponse.model_validate({
                           'request_id': request.request_id,
                           'status': ResponseStatus.INCOMPLETE,
                           'model': request.model,
                           'incomplete_details': {
                        "reason": IncompleteReason.OUTPUT_TRUNCATED,
                        "details": {
                            "finish_reason": "length",
                            "category": "output_truncated",
                            "retryable": True,
                        },
                    },
                       })

        provider = TruncatedProvider()
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch("run.agents.runner._agent_retry_delay_seconds", return_value=0.0),
        ):
            with self.assertRaises(AgentProviderError) as raised:
                self.runner(provider).run(
                    "schema_worker",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                )

        self.assertEqual(len(provider.requests), 6)
        self.assertTrue(raised.exception.retryable_declared)
        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.retry_budget_exhausted)
        self.assertEqual(raised.exception.retry_attempts, 6)
        self.assertEqual(raised.exception.code, "output_truncated")

    def test_runner_retries_transient_incomplete_response(self) -> None:
        class EmptyThenCompleteProvider(MockProvider):
            def create(_self, request):
                if not _self.requests:
                    _self.requests.append(request)
                    return KemoResponse.model_validate({
                               'request_id': request.request_id,
                               'status': ResponseStatus.INCOMPLETE,
                               'model': request.model,
                               'incomplete_details': {
                            "reason": IncompleteReason.EMPTY_OUTPUT,
                            "details": {
                                "category": "upstream_error",
                                "status_code": 503,
                                "retry_after_ms": 0,
                            },
                        },
                           })
                return super().create(request)

        provider = EmptyThenCompleteProvider()
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.agents.runner._agent_retry_delay_seconds",
                return_value=0.0,
            ),
        ):
            result = self.runner(provider).run(
                "schema_worker",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )

        self.assertEqual(result.data, SUMMARY)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(result.metadata["retry_attempts"], 2)

    def test_runner_unknown_incomplete_http_400_retries_five_times(self) -> None:
        class InvalidRequestProvider(MockProvider):
            def create(_self, request):
                _self.requests.append(request)
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.FAILED,
                    model=request.model,
                    error=UnifiedError.model_validate({
                              'type': "provider_error",
                              'code': "PROVIDER_BAD_RESPONSE",
                              'message': "request rejected",
                              'provider_status': 400,
                          }),
                )

        provider = InvalidRequestProvider()
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            with self.assertRaises(AgentProviderError) as raised:
                self.runner(provider).run(
                    "schema_worker",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                )

        # R1 3.3: HTTP 400 cannot consume a transient retry budget.
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertFalse(raised.exception.retryable_declared)
        self.assertFalse(raised.exception.retryable)
        self.assertFalse(getattr(raised.exception, "retry_budget_exhausted", False))

    def test_runner_uses_declared_chat_reasoning_capabilities(self) -> None:
        provider = MockProvider()
        config = {
            **self.config,
            "provider": {
                **self.config["provider"],
                "type": "chat",
                "reasoning_effort": "high",
            },
        }
        runner = AgentRunner(
            self.root,
            "kesepain",
            config=config,
            provider_factory=lambda _: provider,
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            runner.run(
                "schema_worker",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )
        self.assertEqual(provider.capability_calls, ["main-model"])
        self.assertIsNotNone(provider.requests[0].reasoning)
        self.assertTrue(provider.requests[0].reasoning.enabled)
        self.assertEqual(provider.requests[0].reasoning.effort, "high")
        self.assertEqual(provider.requests[0].provider_options, {})

    def test_runner_defaults_chat_reasoning_to_medium(self) -> None:
        provider = MockProvider()
        config = {
            **self.config,
            "provider": {
                **self.config["provider"],
                "type": "chat",
                "reasoning_effort": None,
            },
        }
        runner = AgentRunner(
            self.root,
            "kesepain",
            config=config,
            provider_factory=lambda _: provider,
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            runner.run(
                "schema_worker",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )
        self.assertEqual(provider.requests[0].reasoning.effort, "medium")
        self.assertEqual(provider.requests[0].provider_options, {})

    def test_runner_rewrites_reused_response_and_tool_ids_across_iterations(self) -> None:
        class ReusedResponseIdProvider(MockProvider):
            def __init__(_self) -> None:
                super().__init__()
                _self.responses = []

            def create(_self, request):
                _self.requests.append(request)
                iteration = len(_self.requests)
                if iteration < 3:
                    output = [
                        ReasoningItem(id="rs_0", summary=f"reasoning {iteration}"),
                        ToolCallItem(
                            id="call_0_0",
                            call_id="callid_0_0",
                            name="memory_manage",
                            arguments={
                                "action": "search_by_content",
                                "tier": "seven_days",
                                "query": f"candidate {iteration}",
                            },
                        ),
                    ]
                    status = ResponseStatus.REQUIRES_ACTION
                else:
                    output = [
                        ReasoningItem(id="rs_0", summary="final reasoning"),
                        MessageItem.text(
                            MessageRole.ASSISTANT,
                            json.dumps({"candidates": [], "promotions": []}),
                            phase=MessagePhase.FINAL_ANSWER,
                        ),
                    ]
                    status = ResponseStatus.COMPLETED
                response = KemoResponse(
                    request_id=request.request_id,
                    status=status,
                    model=request.model,
                    output=output,
                    usage=Usage(
                        input_tokens=2,
                        output_tokens=1,
                        total_tokens=3,
                        measurement=Measurement.model_validate({
                                        'mode': "provider",
                                        'exact': True,
                                    }),
                    ),
                )
                _self.responses.append(response)
                return response

        provider = ReusedResponseIdProvider()
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch("run.agents.model_loop.execute_tool", return_value={"matches": []}),
        ):
            result = self.runner(provider).run(
                "self_improve",
                {"trigger": "context_compression", "rounds": []},
            )

        self.assertEqual(result.data["candidates"], [])
        self.assertEqual(len(provider.requests), 3)
        carried_reasoning = [
            item
            for item in provider.requests[2].input
            if isinstance(item, ReasoningItem)
        ]
        self.assertEqual(len(carried_reasoning), 2)
        self.assertEqual(len({item.id for item in carried_reasoning}), 2)
        self.assertTrue(all(item.id != "rs_0" for item in carried_reasoning))
        self.assertTrue(
            all(
                item.id == "rs_0"
                for response in provider.responses
                for item in response.output
                if isinstance(item, ReasoningItem)
            )
        )
        carried_calls = [
            item
            for item in provider.requests[2].input
            if isinstance(item, ToolCallItem)
        ]
        carried_results = [
            item
            for item in provider.requests[2].input
            if isinstance(item, ToolResultItem)
        ]
        self.assertEqual(len(carried_calls), 2)
        self.assertEqual(len({item.id for item in carried_calls}), 2)
        self.assertEqual(len({item.call_id for item in carried_calls}), 2)
        self.assertTrue(all(item.id != "call_0_0" for item in carried_calls))
        self.assertTrue(all(item.call_id != "callid_0_0" for item in carried_calls))
        self.assertEqual(
            {item.call_id for item in carried_results},
            {item.call_id for item in carried_calls},
        )
        self.assertTrue(
            all(
                item.id == "call_0_0" and item.call_id == "callid_0_0"
                for response in provider.responses[:2]
                for item in response.output
                if isinstance(item, ToolCallItem)
            )
        )

    def test_history_summary_uses_local_fallback_after_one_invalid_response(self) -> None:
        provider = MockProvider(text="标题已经整理好了，但这次没有按照 JSON 格式输出。")
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_summary_test",
                    "target_seq": 2,
                    "records": [
                        {"seq": 1, "role": "user", "text": "请整理摘要"},
                        {"seq": 2, "role": "assistant", "text": "已经完成整理"},
                    ],
                },
                max_tokens=512,
            )
        self.assertEqual(result.data["title"], "历史对话内容摘要")
        self.assertEqual(result.metadata["history_summary_recovery"], "deterministic_fallback")
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].generation.max_output_tokens, 512)

    def test_history_summary_recovers_labelled_text_without_second_request(self) -> None:
        provider = MockProvider(
            text=(
                "标题：历史摘要普通文本恢复\n"
                "摘要：模型即使没有返回 JSON，系统也能从明确标签中恢复标题和摘要内容。"
            )
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_labelled_summary",
                    "target_seq": 1,
                    "records": [{"seq": 1, "role": "user", "text": "整理历史"}],
                },
            )
        self.assertEqual(result.data["title"], "历史摘要普通文本恢复")
        self.assertIn("明确标签", result.data["summary"])
        self.assertEqual(
            result.metadata["history_summary_recovery"],
            "labelled_text",
        )
        self.assertEqual(len(provider.requests), 1)

    def test_schema_worker_rejects_truncated_json_without_reexecution(self) -> None:
        provider = MockProvider(
            texts=[
                '{"facts":["unfinished response"]',
                json.dumps(SUMMARY, ensure_ascii=False),
            ]
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            with self.assertRaises(AgentOutputError) as caught:
                self.runner(provider).run(
                    "schema_worker", {"rounds": [], "trigger": "manual"}, max_tokens=1024,
                )
        self.assertIn("疑似被截断", str(caught.exception))
        self.assertEqual(caught.exception.raw_text, '{"facts":["unfinished response"]')
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].generation.max_output_tokens, 1024)

    def test_schema_worker_rejects_schema_overflow_without_reexecution(self) -> None:
        overflow = {**SUMMARY, "facts": [f"fact-{index}" for index in range(13)]}
        provider = MockProvider(
            texts=[
                json.dumps(overflow, ensure_ascii=False),
                json.dumps(SUMMARY, ensure_ascii=False),
            ]
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            with self.assertRaises(AgentOutputError) as caught:
                self.runner(provider).run("schema_worker", {"rounds": []})
        self.assertIn("元素数量不能大于 12", str(caught.exception))
        self.assertEqual(json.loads(caught.exception.raw_text), overflow)
        self.assertEqual(len(provider.requests), 1)

    def test_schema_worker_preserves_invalid_output_without_outer_retries(self) -> None:
        provider = MockProvider(texts=["first invalid", "second invalid"])
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            with self.assertRaises(AgentOutputError) as caught:
                self.runner(provider).run(
                    "schema_worker",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                )
        self.assertEqual(caught.exception.raw_text, "first invalid")
        self.assertEqual(len(provider.requests), 1)
        self.assertFalse(getattr(caught.exception, "retry_budget_exhausted", False))

    def test_schema_worker_extracts_json_object_from_surrounding_text(self) -> None:
        provider = MockProvider(
            text=(
                "摘要如下：\n"
                + json.dumps(SUMMARY, ensure_ascii=False)
                + "\n以上为完整结果。"
            )
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "schema_worker",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )
        self.assertEqual(result.data, SUMMARY)
        self.assertEqual(len(provider.requests), 1)

    def test_history_summary_uses_local_fallback_without_repair_request(self) -> None:
        provider = MockProvider(text="first invalid")
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_summary_test",
                    "target_seq": 2,
                    "records": [
                        {"seq": 1, "role": "user", "text": "问题"},
                        {"seq": 2, "role": "assistant", "text": "回答"},
                    ],
                },
                max_tokens=512,
            )
        self.assertEqual(result.data["title"], "历史对话内容摘要")
        self.assertIn("已保存在历史记录中", result.data["summary"])
        self.assertEqual(
            result.metadata["history_summary_recovery"],
            "deterministic_fallback",
        )
        self.assertEqual(len(provider.requests), 1)

    def test_history_summary_accepts_strict_structured_output_tool(self) -> None:
        expected = {
            "title": "历史摘要结构化输出",
            "summary": "历史摘要通过严格工具参数提交，避免普通文本格式不稳定导致解析失败。",
        }

        class StructuredProvider(MockProvider):
            def create(_self, request):
                _self.requests.append(request)
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.COMPLETED,
                    model=request.model,
                    output=[
                        ToolCallItem(
                            id="call_structured_output",
                            call_id="callid_structured_output_1",
                            name="submit_structured_output",
                            arguments=expected,
                        )
                    ],
                    usage=Usage(
                        input_tokens=2,
                        output_tokens=1,
                        total_tokens=3,
                        measurement=Measurement.model_validate({
                                        'mode': "provider",
                                        'exact': True,
                                    }),
                    ),
                )

        provider = StructuredProvider()
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_structured_summary",
                    "target_seq": 2,
                    "records": [
                        {"seq": 1, "role": "user", "text": "生成摘要"},
                        {"seq": 2, "role": "assistant", "text": "完成"},
                    ],
                },
                max_tokens=1024,
                structured_output_tool=True,
            )
        self.assertEqual(result.data, expected)
        self.assertEqual(result.metadata["structured_output_transport"], "tool")
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(
            provider.requests[0].tools[-1].name,
            "submit_structured_output",
        )

    def test_history_summary_safely_truncates_slightly_overlong_fields(self) -> None:
        title = "历史摘要轻微超长内容会安全截断处理结果"
        summary = "这是一段用于验证历史摘要轻微超过长度限制时可以安全截断的内容，系统不会因此额外调用一次模型修复格式，同时仍会保留足够完整且可读的会话信息。" * 2
        provider = MockProvider(
            text=json.dumps({"title": title, "summary": summary}, ensure_ascii=False)
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_summary_trim",
                    "target_seq": 2,
                    "records": [
                        {"seq": 1, "role": "user", "text": "问题"},
                        {"seq": 2, "role": "assistant", "text": "回答"},
                    ],
                },
                max_tokens=512,
            )
        self.assertLessEqual(len(result.data["title"]), 24)
        self.assertLessEqual(len(result.data["summary"]), 120)
        self.assertEqual(len(provider.requests), 1)

    def test_runner_uses_loose_input_but_rejects_invalid_output_timeout_and_cancel(self) -> None:
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            loose = self.runner(MockProvider()).run("schema_worker", {"rounds": []})
            self.assertEqual(loose.data["narrative"], "summary")
            with self.assertRaises(AgentOutputError):
                self.runner(MockProvider(text="not-json")).run(
                    "schema_worker",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                )
            timeout_events = []
            with self.assertRaises(AgentTimeoutError) as raised:
                self.runner(MockProvider(delay=0.2)).run(
                    "schema_worker",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                    timeout=0.02,
                    event_callback=timeout_events.append,
                )
            self.assertTrue(raised.exception.process_terminated)
            self.assertEqual(timeout_events[-1].metadata["status"], "timed_out")
            self.assertTrue(timeout_events[-1].metadata["cancel_requested"])
            self.assertTrue(timeout_events[-1].metadata["process_terminated"])
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(AgentCancelledError):
                self.runner(MockProvider()).run(
                    "schema_worker",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                    cancel_event=cancelled,
                )

    def test_two_users_keep_independent_runner_context(self) -> None:
        first_provider = MockProvider()
        second_provider = MockProvider()
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
                "schema_worker",
                {"previous_summary": None, "rounds": [{"owner": "alice"}], "trigger": "manual"},
            )
            second.run(
                "schema_worker",
                {"previous_summary": None, "rounds": [{"owner": "bob"}], "trigger": "manual"},
            )
        first_input = json.loads(text_from_content(first_provider.requests[0].input[0].content))
        second_input = json.loads(text_from_content(second_provider.requests[0].input[0].content))
        self.assertEqual(first_input["rounds"][0]["owner"], "alice")
        self.assertEqual(second_input["rounds"][0]["owner"], "bob")
        self.assertNotIn("bob", text_from_content(first_provider.requests[0].input[0].content))

    def test_scheduler_result_handler_is_serialized_with_agent(self) -> None:
        registry = discover_agents(self.root)
        order = []
        runner = StubRunner(registry, order)
        runner.gate.set()
        scheduler = AgentScheduler(runner)
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        task = scheduler.submit(
            "self_improve",
            {"value": 7},
            result_handler=lambda result: order.append(("persist", result.data["value"])),
        )
        scheduler.wait(task, 1)
        self.assertEqual(order, [("start", 7), ("end", 7), ("persist", 7)])

    def test_background_serial_is_shared_across_independent_runners(self) -> None:
        first_started = threading.Event()
        release_first = threading.Event()
        order: list[tuple[str, int]] = []
        errors: list[BaseException] = []

        def executor(_context, input_data):
            marker = int(input_data["target_seq"])
            order.append(("start", marker))
            if marker == 1:
                first_started.set()
                release_first.wait(2)
            order.append(("end", marker))
            return AgentRunResult(
                "history_summary",
                {"title": f"title-{marker}", "summary": f"summary-{marker}"},
                "{}",
                {},
                "mock",
            )

        def invoke(marker: int) -> None:
            try:
                self.runner(MockProvider()).run(
                    "history_summary",
                    {
                        "trigger": "session_closed",
                        "session_id": "conv_serial_test",
                        "target_seq": marker,
                        "records": [{"seq": marker, "role": "user", "text": "u"}],
                    },
                )
            except BaseException as exc:
                errors.append(exc)

        with patch("run.agents.runner._load_executor", return_value=executor):
            first = threading.Thread(target=invoke, args=(1,))
            second = threading.Thread(target=invoke, args=(2,))
            first.start()
            self.assertTrue(first_started.wait(1))
            second.start()
            time.sleep(0.1)
            self.assertEqual(order, [("start", 1)])
            release_first.set()
            first.join(2)
            second.join(2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(
            order,
            [("start", 1), ("end", 1), ("start", 2), ("end", 2)],
        )

    def test_background_serial_allows_same_user_nested_runner_call(self) -> None:
        order: list[tuple[str, int]] = []

        def executor(context, input_data):
            marker = int(input_data["target_seq"])
            order.append(("start", marker))
            if marker == 1:
                context.runner.run(
                    "history_summary",
                    {
                        "trigger": "session_closed",
                        "session_id": "conv_nested_serial_test",
                        "target_seq": 2,
                        "records": [{"seq": 2, "role": "user", "text": "u"}],
                    },
                    timeout=2,
                )
            order.append(("end", marker))
            return AgentRunResult(
                "history_summary",
                {"title": f"title-{marker}", "summary": f"summary-{marker}"},
                "{}",
                {},
                "mock",
            )

        with patch("run.agents.runner._load_executor", return_value=executor):
            self.runner(MockProvider()).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_nested_serial_test",
                    "target_seq": 1,
                    "records": [{"seq": 1, "role": "user", "text": "u"}],
                },
                timeout=2,
            )

        self.assertEqual(
            order,
            [("start", 1), ("start", 2), ("end", 2), ("end", 1)],
        )

    def test_subagent_events_are_observable(self) -> None:
        events = []
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            self.runner(MockProvider()).run(
                "schema_worker",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
                event_callback=events.append,
                source="web",
                session_id="session-a",
            )
        self.assertEqual([event.metadata["status"] for event in events], ["started", "model_request", "validating", "completed"])
        self.assertEqual(events[1].metadata["iteration"], 1)
        self.assertTrue(all(event.metadata["phase"] == "subagent" for event in events))
        self.assertTrue(all(event.metadata["source"] == "web" for event in events))
        self.assertTrue(all(event.metadata["session_id"] == "session-a" for event in events))

    def test_scheduler_task_callbacks_do_not_cross_conversation_spaces(self) -> None:
        registry = discover_agents(self.root)

        class CallbackRunner(StubRunner):
            def run(self, name, input_data, **kwargs):
                callback = kwargs.get("event_callback")
                if callback is not None:
                    callback(
                        RunEvent(
                            type="reasoning_delta",
                            metadata={
                                "status": "runner",
                                "source": kwargs.get("source"),
                                "session_id": kwargs.get("session_id"),
                            },
                        )
                    )
                return AgentRunResult(name, {"value": input_data["value"]}, "{}", {}, "mock")

        runner = CallbackRunner(registry, [])
        scheduler = AgentScheduler(runner)
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        events_a: list[RunEvent] = []
        events_b: list[RunEvent] = []
        task_a = scheduler.submit(
            "self_improve",
            {"value": 1},
            source="web",
            session_id="session-a",
            event_callback=events_a.append,
        )
        task_b = scheduler.submit(
            "self_improve",
            {"value": 2},
            source="web",
            session_id="session-b",
            event_callback=events_b.append,
        )
        scheduler.wait(task_a, timeout=1, source="web", session_id="session-a")
        scheduler.wait(task_b, timeout=1, source="web", session_id="session-b")

        self.assertTrue(events_a)
        self.assertTrue(events_b)
        self.assertTrue(all(event.metadata["session_id"] == "session-a" for event in events_a))
        self.assertTrue(all(event.metadata["session_id"] == "session-b" for event in events_b))

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
            scheduler.submit("schema_worker", {"rounds": [], "trigger": "manual"})

    def test_scoped_scheduler_task_cannot_use_legacy_unscoped_access(self) -> None:
        registry = discover_agents(self.root)
        runner = StubRunner(registry, [])
        scheduler = AgentScheduler(runner)
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        task_id = scheduler.submit(
            "self_improve",
            {"value": 1},
            source="web",
            session_id="session-a",
        )
        with self.assertRaises(AgentTaskNotFoundError):
            scheduler.get(task_id)
        with self.assertRaises(AgentTaskNotFoundError):
            scheduler.wait(task_id, timeout=0)
        with self.assertRaises(AgentTaskNotFoundError):
            scheduler.cancel(task_id)
        runner.gate.set()

    def test_scheduler_rejects_partial_conversation_scope(self) -> None:
        registry = discover_agents(self.root)
        runner = StubRunner(registry, [])
        scheduler = AgentScheduler(runner)
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)

        with self.assertRaises(AgentQueueError):
            scheduler.submit("self_improve", {"value": 1}, source="web")
        with self.assertRaises(AgentQueueError):
            scheduler.submit_callable(
                "external",
                {},
                lambda _cancel_event: None,
                session_id="session-a",
            )

    def test_scheduler_preserves_subagent_timeout_status(self) -> None:
        registry = discover_agents(self.root)
        runner = StubRunner(registry, [])

        def timed_out(*args, **kwargs):
            del args, kwargs
            raise AgentTimeoutError(
                "timed out",
                process_terminated=True,
            )

        runner.run = timed_out
        scheduler = AgentScheduler(runner)
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        task = scheduler.submit("self_improve", {"value": 1})
        with self.assertRaises(AgentQueueError):
            scheduler.wait(task, 1)
        snapshot = scheduler.get(task)
        self.assertEqual(snapshot["status"], "timed_out")
        self.assertEqual(snapshot["error"]["exception_type"], "AgentTimeoutError")
        self.assertTrue(snapshot["error"]["cancel_requested"])
        self.assertTrue(snapshot["error"]["process_terminated"])

    def test_scheduler_preserves_retry_classification_in_error_snapshot(self) -> None:
        registry = discover_agents(self.root)
        runner = StubRunner(registry, [])

        def failed(*args, **kwargs):
            del args, kwargs
            raise ProviderError(
                "gateway unavailable",
                category="gateway_error",
                status_code=503,
                retryable=False,
                retry_after_ms=1500,
            )

        runner.run = failed
        scheduler = AgentScheduler(runner)
        self.addCleanup(scheduler.close, wait=True, cancel_pending=True)
        task = scheduler.submit("self_improve", {"value": 1})
        with self.assertRaises(AgentQueueError):
            scheduler.wait(task, 1)
        snapshot = scheduler.get(task)
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["error"]["category"], "gateway_error")
        self.assertEqual(snapshot["error"]["status_code"], 503)
        self.assertFalse(snapshot["error"]["retryable"])
        self.assertEqual(snapshot["error"]["retry_after_ms"], 1500)


if __name__ == "__main__":
    unittest.main()
