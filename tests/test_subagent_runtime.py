from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.protocol.enums import MessagePhase, MessageRole, ResponseStatus
from provider.protocol.models import (
    KemoResponse,
    Measurement,
    MessageItem,
    ToolCallItem,
    Usage,
    text_from_content,
)
from run.agent_queue import AgentQueueError, AgentScheduler
from run.agent_runner import (
    AgentCancelledError,
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
    def __init__(
        self,
        *,
        text: str | None = None,
        texts: list[str] | None = None,
        delay: float = 0.0,
        order=None,
    ) -> None:
        self.text = text if text is not None else json.dumps(SUMMARY)
        self.texts = list(texts or [])
        self.delay = delay
        self.order = order
        self.requests = []

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
                measurement=Measurement(mode="provider", exact=True),
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
        self.root = Path(__file__).resolve().parents[1]
        self.config = {
            "provider": {
                "type": "kemo",
                "base_url": "http://127.0.0.1:1",
                "api_key_env": "TEST_AGENT_KEY",
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
                "context_manage",
                "history_summary",
                "memory_temporary_important",
                "self_improve",
                "task_plan",
                "time_plan",
            },
        )
        context_definition = registry.get("context_manage")
        self.assertEqual(context_definition.version, "1.1.0")
        self.assertEqual(context_definition.instruction_file, "AGENT.md")
        self.assertEqual(context_definition.trigger_file, "trigger.md")
        self.assertIn("按完整对话轮", context_definition.trigger_registration)
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
        self.assertEqual(summary_definition.version, "1.1.0")
        self.assertEqual(summary_definition.output_schema["required"], ["title", "summary"])
        self.assertFalse(summary_definition.output_schema["additionalProperties"])

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

    def test_runner_uses_explicit_input_and_inherits_main_model(self) -> None:
        provider = MockProvider()
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "context_manage",
                {"previous_summary": None, "rounds": [{"round": 1}], "trigger": "manual"},
            )
        self.assertEqual(result.data["narrative"], "summary")
        request = provider.requests[0]
        self.assertEqual(request.model, "main-model")
        self.assertTrue(request.reasoning.enabled)
        self.assertEqual(request.reasoning.effort, "high")
        self.assertEqual(request.reasoning.return_mode, "content")
        self.assertEqual(request.provider_options["reasoning_effort"], "high")
        self.assertEqual(len(request.input), 1)
        self.assertIn("[trigger_registration]", request.system_prompt)
        self.assertNotIn("# 操作信息", request.system_prompt)
        self.assertNotIn(
            "main conversation",
            json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
        )
        self.assertEqual(json.loads(text_from_content(request.input[0].content))["trigger"], "manual")

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
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            runner.run(
                "context_manage",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )
        self.assertEqual(provider.requests[0].model, "summary-model")

    def test_history_summary_repairs_non_json_output_once(self) -> None:
        repaired = {
            "title": "历史摘要格式自动修复",
            "summary": "后台摘要在首次格式错误后自动修复，并生成稳定清晰的历史对话标题与内容说明。",
        }
        provider = MockProvider(
            texts=[
                "标题已经整理好了，但这次没有按照 JSON 格式输出。",
                json.dumps(repaired, ensure_ascii=False),
            ]
        )
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_summary_test",
                    "target_round": 1,
                    "previous_summary": None,
                    "rounds": [{"round": 1, "user": "请整理摘要", "assistant": "已经完成整理"}],
                },
                max_tokens=512,
            )
        self.assertEqual(result.data, repaired)
        self.assertEqual(len(provider.requests), 2)
        repair_payload = json.loads(text_from_content(provider.requests[1].input[0].content))
        self.assertTrue(repair_payload["_format_repair"]["required"])
        self.assertIn("没有按照 JSON", repair_payload["_format_repair"]["previous_output"])
        self.assertEqual(provider.requests[1].generation.max_output_tokens, 512)

    def test_history_summary_recovers_labelled_text_without_second_request(self) -> None:
        provider = MockProvider(
            text=(
                "标题：历史摘要普通文本恢复\n"
                "摘要：模型即使没有返回 JSON，系统也能从明确标签中恢复标题和摘要内容。"
            )
        )
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_labelled_summary",
                    "target_round": 1,
                    "previous_summary": None,
                    "rounds": [{"round": 1, "user": "整理历史", "assistant": "已完成"}],
                },
            )
        self.assertEqual(result.data["title"], "历史摘要普通文本恢复")
        self.assertIn("明确标签", result.data["summary"])
        self.assertEqual(
            result.metadata["history_summary_recovery"],
            "labelled_text",
        )
        self.assertEqual(len(provider.requests), 1)

    def test_context_manage_repairs_truncated_json_once(self) -> None:
        provider = MockProvider(
            texts=[
                '{"facts":["unfinished response"]',
                json.dumps(SUMMARY, ensure_ascii=False),
            ]
        )
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "context_manage",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
                max_tokens=1024,
            )
        self.assertEqual(result.data, SUMMARY)
        self.assertTrue(result.metadata["format_repaired"])
        self.assertIn("疑似被截断", result.metadata["format_error"])
        self.assertEqual(len(provider.requests), 2)
        repair_payload = json.loads(
            text_from_content(provider.requests[1].input[0].content)
        )
        self.assertTrue(repair_payload["_format_repair"]["required"])
        self.assertIn("确保 JSON 完整闭合", repair_payload["_format_repair"]["instruction"])
        self.assertEqual(provider.requests[1].generation.max_output_tokens, 1024)

    def test_context_manage_repairs_schema_overflow_once(self) -> None:
        overflow = {**SUMMARY, "facts": [f"fact-{index}" for index in range(13)]}
        provider = MockProvider(
            texts=[
                json.dumps(overflow, ensure_ascii=False),
                json.dumps(SUMMARY, ensure_ascii=False),
            ]
        )
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "context_manage",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )
        self.assertEqual(result.data, SUMMARY)
        self.assertIn("元素数量不能大于 12", result.metadata["format_error"])
        self.assertEqual(len(provider.requests), 2)

    def test_context_manage_failed_repair_preserves_last_raw_output(self) -> None:
        provider = MockProvider(texts=["first invalid", "second invalid"])
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            with self.assertRaises(AgentOutputError) as caught:
                self.runner(provider).run(
                    "context_manage",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                )
        self.assertEqual(caught.exception.raw_text, "second invalid")
        self.assertIn("JSON 修复失败", str(caught.exception))
        self.assertEqual(len(provider.requests), 2)

    def test_context_manage_extracts_json_object_from_surrounding_text(self) -> None:
        provider = MockProvider(
            text=(
                "摘要如下：\n"
                + json.dumps(SUMMARY, ensure_ascii=False)
                + "\n以上为完整结果。"
            )
        )
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "context_manage",
                {"previous_summary": None, "rounds": [], "trigger": "manual"},
            )
        self.assertEqual(result.data, SUMMARY)
        self.assertEqual(len(provider.requests), 1)

    def test_history_summary_falls_back_after_failed_repair(self) -> None:
        provider = MockProvider(texts=["first invalid", "second invalid"])
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_summary_test",
                    "target_round": 1,
                    "previous_summary": None,
                    "rounds": [{"round": 1, "user": "问题", "assistant": "回答"}],
                },
                max_tokens=512,
            )
        self.assertEqual(result.data["title"], "关于问题的历史对话")
        self.assertIn("完整内容已保存在历史记录中", result.data["summary"])
        self.assertEqual(
            result.metadata["history_summary_recovery"],
            "deterministic_fallback",
        )
        self.assertEqual(len(provider.requests), 2)

    def test_history_summary_accepts_strict_structured_output_tool(self) -> None:
        expected = {
            "title": "历史摘要结构化输出",
            "summary": "历史摘要通过严格工具参数提交，避免普通文本格式不稳定导致解析失败。",
        }

        class StructuredProvider(MockProvider):
            def create(inner_self, request):
                inner_self.requests.append(request)
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.COMPLETED,
                    model=request.model,
                    output=[
                        ToolCallItem(
                            id="call_structured_output",
                            call_id="structured_output_1",
                            name="submit_structured_output",
                            arguments=expected,
                        )
                    ],
                    usage=Usage(
                        input_tokens=2,
                        output_tokens=1,
                        total_tokens=3,
                        measurement=Measurement(mode="provider", exact=True),
                    ),
                )

        provider = StructuredProvider()
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_structured_summary",
                    "target_round": 1,
                    "previous_summary": None,
                    "rounds": [{"round": 1, "user": "生成摘要", "assistant": "完成"}],
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
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            result = self.runner(provider).run(
                "history_summary",
                {
                    "trigger": "session_closed",
                    "session_id": "conv_summary_trim",
                    "target_round": 1,
                    "previous_summary": None,
                    "rounds": [{"round": 1, "user": "问题", "assistant": "回答"}],
                },
                max_tokens=512,
            )
        self.assertLessEqual(len(result.data["title"]), 24)
        self.assertLessEqual(len(result.data["summary"]), 120)
        self.assertEqual(len(provider.requests), 1)

    def test_runner_uses_loose_input_but_rejects_invalid_output_timeout_and_cancel(self) -> None:
        with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
            loose = self.runner(MockProvider()).run("context_manage", {"rounds": []})
            self.assertEqual(loose.data["narrative"], "summary")
            with self.assertRaises(AgentOutputError):
                self.runner(MockProvider(text="not-json")).run(
                    "context_manage",
                    {"previous_summary": None, "rounds": [], "trigger": "manual"},
                )
            timeout_events = []
            with self.assertRaises(AgentTimeoutError) as raised:
                self.runner(MockProvider(delay=0.2)).run(
                    "context_manage",
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


if __name__ == "__main__":
    unittest.main()
