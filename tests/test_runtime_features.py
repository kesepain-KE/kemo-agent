from __future__ import annotations

import io
import json
import os
import queue
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import cli
from events import RunEvent
from provider.adapters.compat import (
    chat_response_to_kemo,
    chat_stream_to_protocol,
    kemo_request_to_chat,
)
from provider.schema import ChatResponse, ProviderError, ToolCall, Usage
from run.agent_runner import AgentRunResult
from run.engine import (
    _extract_round_memory,
    compress_context,
    context_status,
    handle_request,
    iter_request_events,
)
from run.history import find_window, load_runtime_window, load_window
from run.history_index import find_record as find_history_record
from run.tools import (
    ConsecutiveIdenticalToolCallTracker,
    ToolCancelledError,
    ToolDefinition,
    apply_runtime_tool_policy,
    discover_tools,
    execute_tool,
)


class ScriptedProvider:
    def __init__(self, responses: list[ChatResponse | BaseException] | None = None, streams: list[list[RunEvent]] | None = None) -> None:
        self.responses = list(responses or [])
        self.streams = list(streams or [])
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def chat_stream(self, request):
        self.requests.append(request)
        yield from self.streams.pop(0)

    def create(self, request):
        return chat_response_to_kemo(self.chat(kemo_request_to_chat(request)), request)

    def stream(self, request):
        return chat_stream_to_protocol(
            self.chat_stream(kemo_request_to_chat(request)),
            request,
        )


class RuntimeFeatureTests(unittest.TestCase):
    def test_uploaded_file_context_reaches_provider_without_polluting_saved_user_text(self) -> None:
        _, root = self.make_root()
        provider = ScriptedProvider(responses=[ChatResponse(text="attachment received")])
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            handle_request(
                {
                    "user": "alice",
                    "source": "web",
                    "session_id": "uploaded-file",
                    "prompt": "请读取附件",
                    "uploaded_files": [{
                        "name": "note.md",
                        "path": "users/alice/file_upload/note.md",
                        "size": 128,
                    }],
                },
                root=root,
                provider_factory=lambda _: provider,
            )
        current_user = next(
            message for message in reversed(provider.requests[0].messages)
            if message.get("role") == "user"
        )
        self.assertIn("users/alice/file_upload/note.md", current_user["content"])
        self.assertIn("可按需使用 file 工具读取", current_user["content"])
        window = load_window(find_window(root, "alice", "web", "uploaded-file"))
        self.assertEqual(window["text"]["messages"][0]["content"], "请读取附件")

    def make_root(self, *, stream: bool = False) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        for path in (
            root / "plugins",
            root / "shared_skills",
            root / "users" / "alice" / "history",
            root / "users" / "alice" / "user_skills" / "agent_create",
            root / "users" / "alice" / "user_skills" / "user_create",
        ):
            path.mkdir(parents=True, exist_ok=True)
        provider = {
            "type": "kemo",
            "base_url": "http://127.0.0.1:1/v1",
            "api_key_env": "TEST_KEMO_KEY",
            "model": "mock",
            "stream": stream,
        }
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {"tools": {"enabled": True, "timeout": 2, "max_iterations": 4}}
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1, "provider": provider}),
            "utf-8",
        )
        project_agents = Path(__file__).resolve().parents[1] / "agents"
        shutil.copytree(project_agents, root / "agents")
        return temporary, root

    def copy_self_improve_plugins(self, root: Path) -> None:
        project_plugins = Path(__file__).resolve().parents[1] / "plugins"
        for name in ("memory_manage", "skill_creater"):
            shutil.copytree(project_plugins / name, root / "plugins" / name)

    def write_tool(self, base: Path, name: str, source_value: str, *, enabled: bool = True, async_tool: bool = False) -> None:
        directory = base / name
        directory.mkdir(parents=True)
        manifest = {
            "name": name,
            "description": source_value,
            "input_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "version": "1.0.0",
            "enabled": enabled,
            "entrypoint": "tool.py:run",
        }
        (directory / "SKILL.md").write_text(
            f"# {name}\n{source_value}\n\n## Tool\n\n```json\n"
            f"{json.dumps(manifest, ensure_ascii=False)}\n```\n",
            "utf-8",
        )
        prefix = "async " if async_tool else ""
        await_line = "    await __import__('asyncio').sleep(0)\n" if async_tool else ""
        (directory / "tool.py").write_text(
            f"{prefix}def run(value, *, context):\n{await_line}    return {{'value': value, 'source': '{source_value}', 'user': context['user'], 'knowledge_scopes': context.get('knowledge_scopes')}}\n",
            "utf-8",
        )

    def test_only_plugins_supply_executable_tools(self) -> None:
        _, root = self.make_root()
        locations = [
            root / "plugins",
            root / "shared_skills",
            root / "users" / "alice" / "user_skills" / "agent_create",
            root / "users" / "alice" / "user_skills" / "user_create",
        ]
        for index, location in enumerate(locations):
            self.write_tool(location, "same", f"level-{index}", enabled=index != 3)
        registry = discover_tools(root, "alice")
        chosen = registry.tools["same"]
        self.assertEqual(chosen.source, "plugins")
        self.assertEqual(chosen.overrides, [])
        self.assertTrue(chosen.enabled)
        self.assertIs(registry.get("same"), chosen)

    def test_sync_and_async_tool_execution(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "sync_tool", "sync")
        self.write_tool(root / "plugins", "async_tool", "async", async_tool=True)
        registry = discover_tools(root, "alice")
        context = {"root": str(root), "user": "alice"}
        sync = execute_tool(registry.get("sync_tool"), {"value": "a"}, context=context, timeout=2)
        async_result = execute_tool(registry.get("async_tool"), {"value": "b"}, context=context, timeout=2)
        self.assertEqual(sync["source"], "sync")
        self.assertEqual(async_result["source"], "async")

    def test_running_tool_observes_emergency_cancel_without_waiting_for_timeout(self) -> None:
        cancel = threading.Event()
        tool = ToolDefinition(
            name="slow_tool",
            description="slow",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            version="1.0.0",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=Path.cwd(),
            _callable=lambda: time.sleep(1),
        )
        timer = threading.Timer(0.05, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(ToolCancelledError):
                execute_tool(
                    tool,
                    {},
                    context={"root": str(Path.cwd()), "user": "alice"},
                    timeout=5,
                    cancel_event=cancel,
                )
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 0.5)

    def test_history_search_registration_obeys_memory_switch(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "history_search", "history")
        self.write_tool(root / "plugins", "other_tool", "other")
        registry = apply_runtime_tool_policy(
            discover_tools(root, "alice"),
            {"memory": {"history_read_enabled": False}},
        )
        self.assertEqual(set(registry.tools), {"other_tool"})
        self.assertEqual(
            {manifest.tool["name"] for manifest in registry.plugin_manifests},
            {"other_tool"},
        )

    def test_plugin_whitelist_filters_registry_independently_of_graph_mode(self) -> None:
        _, root = self.make_root()
        for name in ("clock", "weather"):
            self.write_tool(root / "plugins", name, name)

        unrestricted = apply_runtime_tool_policy(
            discover_tools(root, "alice"),
            {"plugins": {"whitelist": []}},
        )
        self.assertEqual(
            set(unrestricted.tools),
            {"clock", "weather"},
        )

        filtered = apply_runtime_tool_policy(
            discover_tools(root, "alice"),
            {"plugins": {"whitelist": ["clock"]}},
        )
        self.assertEqual(set(filtered.tools), {"clock"})
        self.assertEqual(
            {manifest.tool["name"] for manifest in filtered.plugin_manifests},
            {"clock"},
        )

        graph_replaced = apply_runtime_tool_policy(
            discover_tools(root, "alice"),
            {
                "plugins": {"whitelist": []},
                "kemo_graph": {"kemo_graph_temporary_memory": True},
            },
        )
        self.assertEqual(set(graph_replaced.tools), {"clock", "weather"})

    def test_tavily_tool_remains_exposed_without_api_key(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "web_search", "search")
        self.write_tool(root / "plugins", "clock", "clock")

        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}, clear=False):
            unconfigured = apply_runtime_tool_policy(
                discover_tools(root, "alice"),
                {},
            )
        self.assertEqual(set(unconfigured.tools), {"web_search", "clock"})

        with patch.dict(
            os.environ,
            {"TAVILY_API_KEY": "configured-for-test"},
            clear=False,
        ):
            available = apply_runtime_tool_policy(
                discover_tools(root, "alice"),
                {},
            )
        self.assertEqual(set(available.tools), {"web_search", "clock"})

    def test_main_tool_context_receives_effective_knowledge_policy(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "policy_probe", "probe")
        user_config = root / "users" / "alice" / "user_config.json"
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("enabled", "policy_probe", {"value": "a"})],
                    finish_reason="tool_calls",
                    usage=Usage(),
                ),
                ChatResponse(text="enabled done", usage=Usage()),
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("disabled", "policy_probe", {"value": "b"})],
                    finish_reason="tool_calls",
                    usage=Usage(),
                ),
                ChatResponse(text="disabled done", usage=Usage()),
            ]
        )

        user_config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": json.loads(user_config.read_text("utf-8"))["provider"],
                    "knowledge": {"use_shared": False, "use_global": True}
                }
            ),
            "utf-8",
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            enabled_events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "knowledge-enabled",
                        "prompt": "probe",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

            user_config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "provider": json.loads(user_config.read_text("utf-8"))[
                            "provider"
                        ],
                        "knowledge": {"use_shared": True, "use_global": False},
                    }
                ),
                "utf-8",
            )
            disabled_events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "knowledge-disabled",
                        "prompt": "probe",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        enabled_result = next(
            event.result["result"]
            for event in enabled_events
            if event.type == "tool_call_result"
        )
        disabled_result = next(
            event.result["result"]
            for event in disabled_events
            if event.type == "tool_call_result"
        )
        self.assertNotIn("knowledge_enabled", enabled_result)
        self.assertEqual(enabled_result["knowledge_scopes"], ["user", "global"])
        self.assertNotIn("knowledge_enabled", disabled_result)
        self.assertEqual(disabled_result["knowledge_scopes"], ["user", "shared"])

    def test_tool_loop_and_transaction_commit(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="lookup", arguments={"value": "x"})],
                    finish_reason="tool_calls",
                    usage=Usage(
                        1,
                        1,
                        2,
                        source="mock",
                        extra={"prompt_tokens_details": {"cached_tokens": 1}},
                    ),
                ),
                ChatResponse(
                    text="final",
                    usage=Usage(
                        2,
                        2,
                        4,
                        source="mock",
                        extra={"cached_prompt_tokens": 1},
                    ),
                    finish_reason="stop",
                ),
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {"user": "alice", "source": "cli", "session_id": "tool", "prompt": "go"},
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )
        self.assertEqual(
            [event.type for event in events],
            ["tool_call_start", "usage", "tool_call_result", "text_delta", "usage", "done"],
        )
        self.assertEqual(provider.requests[1].messages[-1]["role"], "tool")
        path = find_window(root, "alice", "cli", "tool")
        window = load_window(path)
        self.assertEqual(window["text"]["messages"][-1]["content"], "final")
        self.assertEqual(window["tool"]["rounds"][0]["calls"][0]["status"], "completed")
        self.assertGreaterEqual(window["tool"]["rounds"][0]["calls"][0]["elapsed_ms"], 0)
        self.assertEqual(window["data"]["token_usage"]["total_tokens"], 6)
        self.assertEqual(window["data"]["token_usage"]["provider_request_count"], 2)
        _, runtime_window = load_runtime_window(path, window)
        snapshot = runtime_window["data"]["context_snapshot"]
        self.assertTrue(snapshot["available"])
        self.assertEqual(
            snapshot["total_tokens"],
            snapshot["system_prompt_tokens"]
            + snapshot["tool_schema_tokens"]
            + snapshot["conversation_tokens"]
            + snapshot["summary_tokens"]
            + snapshot["other_tokens"],
        )
        self.assertEqual(window["data"]["token_usage"]["cached_prompt_tokens"], 2)
        self.assertEqual(window["data"]["round_metrics"][0]["usage"]["cache_miss_tokens"], 1)
        done = events[-1]
        self.assertEqual(done.usage["cached_prompt_tokens"], 2)
        self.assertEqual(done.usage["provider_request_count"], 2)
        self.assertAlmostEqual(done.usage["cache_hit_rate"], 2 / 3, places=5)
        self.assertGreaterEqual(done.metadata["elapsed_ms"], 0)

    def test_tool_loop_guard_uses_exact_provider_input_plus_local_increment(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall(id="c1", name="lookup", arguments={"value": "x"})
                    ],
                    finish_reason="tool_calls",
                    usage=Usage(100, 1, 101, estimated=False),
                ),
                ChatResponse(
                    text="finished",
                    finish_reason="stop",
                    usage=Usage(150, 1, 151, estimated=False),
                ),
            ]
        )

        def inflated_messages(messages: list[dict[str, Any]]) -> int:
            has_tool_result = any(message.get("role") == "tool" for message in messages)
            return 200_100 if has_tool_result else 200_000

        with (
            patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False),
            patch("run.engine.estimate_messages_tokens", side_effect=inflated_messages),
            patch("run.engine.estimate_tools_tokens", return_value=0),
        ):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "provider-context-baseline",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(events[-1].type, "done")
        self.assertFalse(any(event.type == "error" for event in events))

    def test_tool_loop_guard_reports_exact_projection_and_file_result_size(self) -> None:
        _, root = self.make_root()
        project_file_tool = Path(__file__).resolve().parents[1] / "plugins" / "file"
        shutil.copytree(project_file_tool, root / "plugins" / "file")
        (root / "large.txt").write_text("payload", "utf-8")
        global_config_path = root / "config" / "global_config.json"
        global_config = json.loads(global_config_path.read_text("utf-8"))
        global_config["agents"] = {
            "token_limit": 100_000,
            "token_compression_ratio": 0.9,
        }
        global_config_path.write_text(json.dumps(global_config), "utf-8")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="f1",
                            name="file",
                            arguments={"action": "read", "path": "large.txt"},
                        )
                    ],
                    finish_reason="tool_calls",
                    usage=Usage(1_000, 1, 1_001, estimated=False),
                )
            ]
        )

        def growing_messages(messages: list[dict[str, Any]]) -> int:
            has_tool_result = any(message.get("role") == "tool" for message in messages)
            return 310_000 if has_tool_result else 200_000

        with (
            patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False),
            patch("run.engine.estimate_messages_tokens", side_effect=growing_messages),
            patch("run.engine.estimate_tools_tokens", return_value=0),
        ):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "provider-context-overflow",
                        "prompt": "read",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        error = events[-1]
        self.assertEqual(error.type, "error")
        guard = error.metadata["context_guard"]
        self.assertEqual(guard["measurement"], "provider_plus_increment")
        self.assertEqual(guard["provider_input_tokens"], 1_000)
        self.assertEqual(guard["incremental_tokens"], 110_000)
        self.assertEqual(guard["projected_tokens"], 111_000)
        self.assertEqual(guard["token_limit"], 100_000)
        self.assertEqual(len(provider.requests), 1)
        file_diagnostic = guard["latest_tools"][0]
        self.assertEqual(file_diagnostic["name"], "file")
        self.assertEqual(file_diagnostic["action"], "read")
        self.assertEqual(file_diagnostic["path"], "large.txt")
        self.assertGreater(file_diagnostic["result_chars"], 0)
        self.assertNotIn("result", file_diagnostic)

    def test_completed_round_extracts_memory_after_history_commit(self) -> None:
        _, root = self.make_root()
        config_path = root / "config" / "global_config.json"
        config = json.loads(config_path.read_text("utf-8"))
        config["memory"] = {"auto_extract_on_commit": True}
        config_path.write_text(json.dumps(config), "utf-8")
        provider = ScriptedProvider(
            responses=[ChatResponse(text="记住这台设备。", usage=Usage())]
        )
        observed: dict[str, Any] = {}

        def extract_after_commit(**kwargs):
            observed.update(kwargs)
            self.assertIsNotNone(find_window(root, "alice", "cli", "memory-round"))
            return {
                "status": "completed",
                "candidate_count": 1,
                "agent": "self_improve",
                "usage": {},
                "persisted": {"created": ["device.md"]},
                "error": None,
            }

        with (
            patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False),
            patch("run.engine._extract_round_memory", side_effect=extract_after_commit),
        ):
            result = handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "memory-round",
                    "prompt": "我的设备是 J1900。",
                },
                root=root,
                provider_factory=lambda _: provider,
            )

        self.assertEqual(observed["round_number"], 1)
        self.assertEqual(observed["prompt"], "我的设备是 J1900。")
        self.assertEqual(result["memory"]["extraction_mode"], "on_commit")
        self.assertEqual(result["memory"]["round_extraction"]["candidate_count"], 1)
        archive = load_window(find_window(root, "alice", "cli", "memory-round"))
        self.assertEqual(archive["data"]["memory_processed_round"], 1)
        self.assertEqual(archive["data"]["memory_status"], "completed")
        indexed = find_history_record(root, "alice", "cli", "memory-round")
        self.assertEqual(indexed["memory_processed_round"], 1)
        self.assertEqual(indexed["memory_status"], "completed")

    def test_extract_round_memory_persists_candidates_and_contains_failures(self) -> None:
        _, root = self.make_root()
        config = {"memory": {}}

        class Runner:
            def __init__(self, *, failure: Exception | None = None) -> None:
                self.failure = failure
                self.input_data: dict[str, Any] | None = None

            def run(self, name, input_data, **kwargs):
                del kwargs
                if self.failure is not None:
                    raise self.failure
                self.input_data = input_data
                return AgentRunResult(
                    agent=name,
                    data={
                        "candidates": [
                            {
                                "action": "upsert",
                                "filename": "device",
                                "content": "用户设备为 J1900。",
                                "explicit": False,
                            }
                        ]
                    },
                    raw_text="",
                    usage={"total_tokens": 4},
                    model="mock",
                )

        runner = Runner()
        result = _extract_round_memory(
            root=root,
            user="alice",
            config=config,
            round_number=3,
            prompt="我的设备是 J1900。",
            text="已经了解。",
            reasoning="",
            tool_records=[],
            agent_runner=runner,  # type: ignore[arg-type]
            cancel_event=None,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(runner.input_data["source"], {"source": "round_commit", "round": 3})
        self.assertTrue(
            (root / "users" / "alice" / "improve" / "seven_days" / "device.md").is_file()
        )

        failed = _extract_round_memory(
            root=root,
            user="alice",
            config=config,
            round_number=4,
            prompt="test",
            text="test",
            reasoning="",
            tool_records=[],
            agent_runner=Runner(failure=RuntimeError("extract failed")),  # type: ignore[arg-type]
            cancel_event=None,
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["exception_type"], "RuntimeError")

    def test_runtime_guidance_is_injected_at_tool_boundary_and_persisted(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        guidance: queue.Queue[str] = queue.Queue()
        guidance.put("focus on the revised target")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("g1", "lookup", {"value": "x"})],
                    usage=Usage(),
                ),
                ChatResponse(text="guided", usage=Usage()),
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "guided",
                        "prompt": "start",
                        "run_id": "run_guided_test",
                        "_guidance_queue": guidance,
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )
        result = events[-1].metadata
        guidance_messages = [
            item["content"]
            for item in provider.requests[1].messages
            if item.get("role") == "user" and "运行中引导" in item.get("content", "")
        ]
        self.assertEqual(len(guidance_messages), 1)
        self.assertIn("focus on the revised target", guidance_messages[0])
        self.assertEqual(result["guidance_count"], 1)
        self.assertEqual(result["run_id"], "run_guided_test")
        applied = [event for event in events if event.type == "guidance_applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].metadata["guidance"], ["focus on the revised target"])
        window = load_window(find_window(root, "alice", "cli", "guided"))
        self.assertEqual(
            window["data"]["round_metrics"][0]["guidance"],
            ["focus on the revised target"],
        )

    def test_stream_deltas_are_forwarded_before_provider_exhausts(self) -> None:
        _, root = self.make_root(stream=True)

        class IncrementalProvider(ScriptedProvider):
            def __init__(self) -> None:
                super().__init__()
                self.resumed_after_first = False

            def chat_stream(self, request):
                self.requests.append(request)
                yield RunEvent(type="text_delta", content="A")
                self.resumed_after_first = True
                yield RunEvent(type="text_delta", content="B")
                yield RunEvent(
                    type="usage",
                    usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                )

        provider = IncrementalProvider()
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            iterator = iter_request_events(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "incremental-stream",
                    "prompt": "go",
                    "stream": True,
                },
                root=root,
                provider_factory=lambda _: provider,
            )
            first = next(iterator)
            self.assertEqual((first.type, first.content), ("text_delta", "A"))
            self.assertFalse(provider.resumed_after_first)
            remaining = list(iterator)

        self.assertTrue(provider.resumed_after_first)
        self.assertEqual(
            [(event.type, event.content) for event in remaining],
            [("text_delta", "B"), ("usage", ""), ("done", "")],
        )
        window = load_window(find_window(root, "alice", "cli", "incremental-stream"))
        self.assertEqual(window["text"]["messages"][-1]["content"], "AB")

    def test_stream_tool_call_is_forwarded_before_provider_exhausts(self) -> None:
        _, root = self.make_root(stream=True)
        self.write_tool(root / "plugins", "lookup", "plugin")

        class IncrementalToolProvider(ScriptedProvider):
            def __init__(self) -> None:
                super().__init__()
                self.resumed_after_call = False

            def chat_stream(self, request):
                self.requests.append(request)
                yield RunEvent(
                    type="tool_call_start",
                    tool_call_id="call-live",
                    tool_name="lookup",
                    arguments={"value": "x"},
                )
                self.resumed_after_call = True
                yield RunEvent(type="usage", usage={})

        provider = IncrementalToolProvider()
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            iterator = iter_request_events(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "incremental-tool",
                    "prompt": "go",
                    "stream": True,
                },
                root=root,
                provider_factory=lambda _: provider,
            )
            first = next(iterator)
            self.assertEqual(first.type, "tool_call_start")
            self.assertEqual(first.tool_call_id, "call-live")
            self.assertFalse(provider.resumed_after_call)
            iterator.close()
        self.assertIsNone(find_window(root, "alice", "cli", "incremental-tool"))

    def test_stream_without_done_yields_error_and_does_not_commit(self) -> None:
        _, root = self.make_root(stream=True)

        class EmptyStreamProvider:
            def stream(self, request):
                del request
                return iter(())

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "missing-done",
                        "prompt": "go",
                        "stream": True,
                    },
                    root=root,
                    provider_factory=lambda _: EmptyStreamProvider(),
                )
            )
        self.assertEqual([event.type for event in events], ["error"])
        self.assertIn("缺少 done 终态", events[0].error["message"])
        self.assertIsNone(find_window(root, "alice", "cli", "missing-done"))

    def test_error_does_not_commit_and_cancel_commits_terminal_round(self) -> None:
        _, root = self.make_root(stream=True)
        error_provider = ScriptedProvider(streams=[[RunEvent(type="text_delta", content="partial"), RunEvent(type="error", error={"message": "boom"})]])
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {"user": "alice", "source": "cli", "session_id": "err", "prompt": "go"},
                    root=root,
                    provider_factory=lambda _: error_provider,
                )
            )
        self.assertEqual(events[-1].type, "error")
        self.assertIsNone(find_window(root, "alice", "cli", "err"))

        cancel = threading.Event()
        cancel_provider = ScriptedProvider(
            streams=[[RunEvent(type="text_delta", content="partial"), RunEvent(type="usage", usage={}), RunEvent(type="done", usage={})]]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            iterator = iter_request_events(
                {"user": "alice", "source": "cli", "session_id": "cancel", "prompt": "go"},
                root=root,
                provider_factory=lambda _: cancel_provider,
                cancel_event=cancel,
            )
            self.assertEqual(next(iterator).type, "text_delta")
            cancel.set()
            cancelled_events = list(iterator)
        self.assertEqual([event.type for event in cancelled_events], ["done"])
        self.assertTrue(cancelled_events[0].metadata["committed"])
        self.assertEqual(cancelled_events[0].metadata["status"], "cancelled")
        cancel_window = load_window(find_window(root, "alice", "cli", "cancel"))
        self.assertEqual(cancel_window["data"]["rounds"], 1)
        self.assertEqual(cancel_window["data"]["round_metrics"][0]["status"], "cancelled")
        self.assertEqual(cancel_window["text"]["messages"][0]["content"], "go")
        self.assertIn("partial", cancel_window["text"]["messages"][1]["content"])
        self.assertIn("紧急停止", cancel_window["text"]["messages"][1]["content"])

        next_provider = ScriptedProvider(
            streams=[[
                RunEvent(type="text_delta", content="next"),
                RunEvent(type="usage", usage={}),
                RunEvent(type="done", usage={}),
            ]]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            next_events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "cancel",
                        "prompt": "continue",
                    },
                    root=root,
                    provider_factory=lambda _: next_provider,
                )
            )
        self.assertEqual(next_events[-1].type, "done")
        resumed_window = load_window(find_window(root, "alice", "cli", "cancel"))
        self.assertEqual(resumed_window["data"]["rounds"], 2)
        self.assertEqual(resumed_window["text"]["messages"][-2]["content"], "continue")

    def test_duplicate_call_reuses_result(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(text="", tool_calls=[ToolCall("1", "lookup", {"value": "x"}), ToolCall("2", "lookup", {"value": "x"})], usage=Usage()),
                ChatResponse(text="done", usage=Usage()),
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            handle_request(
                {"user": "alice", "source": "cli", "session_id": "dup", "prompt": "go"},
                root=root,
                provider_factory=lambda _: provider,
            )
        window = load_window(find_window(root, "alice", "cli", "dup"))
        calls = window["tool"]["rounds"][0]["calls"]
        self.assertFalse(calls[0]["duplicate"])
        self.assertTrue(calls[1]["duplicate"])
        self.assertEqual(calls[1]["status"], "duplicate_reused")

    def test_cancelled_round_pairs_pending_tool_call_with_cancel_result(self) -> None:
        _, root = self.make_root(stream=True)
        cancel = threading.Event()
        provider = ScriptedProvider(
            streams=[[
                RunEvent(
                    type="tool_call_start",
                    tool_call_id="pending-1",
                    tool_name="lookup",
                    arguments={"value": "x"},
                ),
                RunEvent(type="usage", usage={}),
                RunEvent(type="done", usage={}),
            ]]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            iterator = iter_request_events(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "cancel-tool",
                    "prompt": "go",
                    "stream": True,
                },
                root=root,
                provider_factory=lambda _: provider,
                cancel_event=cancel,
            )
            started = next(iterator)
            self.assertEqual(started.type, "tool_call_start")
            cancel.set()
            terminal = list(iterator)

        self.assertEqual([event.type for event in terminal], ["done"])
        window = load_window(find_window(root, "alice", "cli", "cancel-tool"))
        calls = window["tool"]["rounds"][0]["calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], "pending-1")
        self.assertEqual(calls[0]["status"], "cancelled")
        self.assertTrue(calls[0]["result"]["error"]["cancelled"])
        durable_items = window["items"]["items"]
        call_item = next(item for item in durable_items if item["type"] == "tool_call")
        result_item = next(item for item in durable_items if item["type"] == "tool_result")
        self.assertEqual(call_item["call_id"], "pending-1")
        self.assertEqual(result_item["call_id"], "pending-1")
        self.assertTrue(result_item["is_error"])

    def test_identical_call_tracker_uses_name_and_canonical_arguments(self) -> None:
        tracker = ConsecutiveIdenticalToolCallTracker(2)
        self.assertEqual(tracker.record("lookup", {"a": 1, "b": 2}), 1)
        self.assertEqual(tracker.record("lookup", {"b": 2, "a": 1}), 2)
        count = tracker.record("lookup", {"a": 1, "b": 2})
        self.assertEqual(count, 3)
        self.assertTrue(tracker.is_blocked(count))
        self.assertEqual(tracker.record("lookup", {"a": 2, "b": 2}), 1)
        self.assertEqual(tracker.record("other", {"a": 2, "b": 2}), 1)

    def test_ninth_consecutive_identical_call_is_blocked_but_changed_arguments_continue(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        global_path = root / "config" / "global_config.json"
        config = json.loads(global_path.read_text("utf-8"))
        config["tools"].update(
            {"max_iterations": 12, "consecutive_identical_call_limit": 8}
        )
        global_path.write_text(json.dumps(config), "utf-8")
        responses = [
            ChatResponse(
                text="",
                tool_calls=[ToolCall(f"same-{index}", "lookup", {"value": "x"})],
            )
            for index in range(1, 10)
        ]
        responses.extend(
            [
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("changed", "lookup", {"value": "y"})],
                ),
                ChatResponse(text="done"),
            ]
        )
        provider = ScriptedProvider(responses=responses)
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "identical-limit",
                    "prompt": "go",
                },
                root=root,
                provider_factory=lambda _: provider,
            )
        window = load_window(find_window(root, "alice", "cli", "identical-limit"))
        calls = window["tool"]["rounds"][0]["calls"]
        self.assertEqual(calls[0]["status"], "completed")
        self.assertTrue(all(call["status"] == "duplicate_reused" for call in calls[1:8]))
        self.assertEqual(calls[8]["status"], "identical_call_blocked")
        self.assertEqual(
            calls[8]["result"]["error"]["exception_type"],
            "ConsecutiveIdenticalToolCallLimitExceeded",
        )
        self.assertEqual(calls[8]["consecutive_identical_calls"], 9)
        self.assertEqual(calls[9]["status"], "completed")
        self.assertEqual(calls[9]["consecutive_identical_calls"], 1)

    def test_consecutive_failures_temporarily_remove_tool_schema(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "unstable", "plugin")
        (root / "plugins" / "unstable" / "tool.py").write_text(
            "def run(value, *, context):\n    raise RuntimeError('boom')\n",
            "utf-8",
        )
        global_path = root / "config" / "global_config.json"
        config = json.loads(global_path.read_text("utf-8"))
        config["history"] = {"consecutive_tool_fail_limit": 2}
        global_path.write_text(json.dumps(config), "utf-8")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(text="", tool_calls=[ToolCall("f1", "unstable", {"value": "1"})]),
                ChatResponse(text="", tool_calls=[ToolCall("f2", "unstable", {"value": "2"})]),
                ChatResponse(text="", tool_calls=[ToolCall("f3", "unstable", {"value": "3"})]),
                ChatResponse(text="changed approach"),
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "failure-limit",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )
        results = [event for event in events if event.type == "tool_call_result"]
        self.assertEqual(
            [event.metadata["status"] for event in results],
            ["failed", "failed", "temporarily_unavailable"],
        )
        self.assertTrue(results[1].result["error"]["temporarily_unavailable"])
        self.assertIsNone(provider.requests[2].tools)
        self.assertEqual(events[-1].type, "done")

    def test_context_status_and_manual_compress_do_not_add_round(self) -> None:
        _, root = self.make_root()
        self.copy_self_improve_plugins(root)
        global_config_path = root / "config" / "global_config.json"
        global_config = json.loads(global_config_path.read_text("utf-8"))
        global_config["agents"] = {
            "conserved_rounds": 3,
            "max_rounds": 30,
            "rounds_after_compression": 10,
            "token_limit": 120000,
            "token_compression_ratio": 0.6,
        }
        global_config_path.write_text(json.dumps(global_config), "utf-8")
        provider = ScriptedProvider(
            responses=[ChatResponse(text=f"reply-{index}", usage=Usage()) for index in range(12)]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            for index in range(12):
                handle_request(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "long",
                        "prompt": f"round-{index}",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            status = context_status(
                {"user": "alice", "source": "cli", "session_id": "long"},
                root=root,
            )
            self.assertEqual(status["rounds"], 12)
            summary_provider = ScriptedProvider(
                responses=[
                    *[
                        ChatResponse(
                            text=json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "action": "upsert",
                                            "filename": "压缩记忆",
                                            "content": "old rounds fact",
                                            "explicit": False,
                                            "durable": True,
                                            "evidence": f"round-{index}",
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                            usage=Usage(1, 1, 2, source="mock"),
                        )
                        for index in range(3)
                    ],
                    ChatResponse(
                        text=json.dumps(
                            {
                                "facts": ["old rounds"],
                                "requirements": [],
                                "decisions": [],
                                "unfinished": [],
                                "tool_results": [],
                                "entities": [],
                                "narrative": "summary",
                            }
                        ),
                        usage=Usage(3, 1, 4, source="mock"),
                    )
                ]
            )
            result = compress_context(
                {"user": "alice", "source": "cli", "session_id": "long"},
                root=root,
                provider_factory=lambda _: summary_provider,
            )
        self.assertTrue(result["compressed"])
        self.assertFalse(result["committed"])
        self.assertEqual(result["context"]["rounds_kept"], 10)
        self.assertEqual(result["context"]["rounds_removed"], 2)
        # Twelve pending rounds are analyzed in three five-round batches,
        # followed by one context summary request.
        self.assertEqual(len(summary_provider.requests), 4)
        self.assertTrue(result["context"]["summary"]["generated"])
        window = load_window(find_window(root, "alice", "cli", "long"))
        self.assertEqual(window["data"]["rounds"], 12)
        self.assertEqual(window["data"]["memory_processed_round"], 12)
        self.assertEqual(window["data"]["memory_status"], "completed")
        self.assertEqual(result["memory"]["status"], "completed")
        self.assertEqual(len(window["text"]["messages"]), 24)
        self.assertTrue(
            (root / "users" / "alice" / "improve" / "seven_days" / "压缩记忆.md").is_file()
        )

    def test_queued_manual_compress_only_waits_for_summary(self) -> None:
        _, root = self.make_root()
        global_config_path = root / "config" / "global_config.json"
        global_config = json.loads(global_config_path.read_text("utf-8"))
        global_config.update(
            {
                "agents": {
                    "conserved_rounds": 3,
                    "max_rounds": 30,
                    "rounds_after_compression": 10,
                    "token_limit": 120000,
                    "token_compression_ratio": 0.6,
                },
                "memory": {"extraction_mode": "compression_only"},
            }
        )
        global_config_path.write_text(json.dumps(global_config), "utf-8")
        provider = ScriptedProvider(
            responses=[ChatResponse(text=f"reply-{index}", usage=Usage()) for index in range(12)]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            for index in range(12):
                handle_request(
                    {
                        "user": "alice",
                        "source": "web",
                        "session_id": "queued-compress",
                        "prompt": f"round-{index}",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            summary_provider = ScriptedProvider(
                responses=[
                    ChatResponse(
                        text=json.dumps(
                            {
                                "facts": ["old rounds"],
                                "requirements": [],
                                "decisions": [],
                                "unfinished": [],
                                "tool_results": [],
                                "entities": [],
                                "narrative": "summary",
                            }
                        ),
                        usage=Usage(3, 1, 4, source="mock"),
                    )
                ]
            )
            result = compress_context(
                {
                    "user": "alice",
                    "source": "web",
                    "session_id": "queued-compress",
                    "memory_extraction_policy": "queue",
                },
                root=root,
                provider_factory=lambda _: summary_provider,
            )

        self.assertTrue(result["compressed"])
        self.assertFalse(result["committed"])
        self.assertEqual(len(summary_provider.requests), 1)
        self.assertEqual(result["memory"]["status"], "queued")
        self.assertEqual(result["memory"]["processed_round"], 0)
        self.assertEqual(result["memory"]["target_round"], 12)
        self.assertEqual(result["memory"]["pending_rounds"], 12)
        window = load_window(find_window(root, "alice", "web", "queued-compress"))
        self.assertEqual(window["data"]["memory_status"], "queued")
        self.assertEqual(window["data"]["memory_target_round"], 12)
        self.assertEqual(window["data"]["memory_processed_round"], 0)

    def test_provider_context_length_error_compresses_and_retries(self) -> None:
        _, root = self.make_root()
        self.copy_self_improve_plugins(root)
        global_config_path = root / "config" / "global_config.json"
        global_config = json.loads(global_config_path.read_text("utf-8"))
        global_config.update(
            {
                "agents": {
                    "conserved_rounds": 3,
                    "max_rounds": 80,
                    "rounds_after_compression": 1,
                    "token_limit": 120000,
                    "token_compression_ratio": 0.6,
                },
                "history": {"recent_full_rounds": 1},
            }
        )
        global_config_path.write_text(json.dumps(global_config), "utf-8")
        context_error = ProviderError(
            "gateway rejected oversized context",
            status_code=502,
            body={
                "code": "PROVIDER_BAD_RESPONSE",
                "provider_status": 400,
            },
        )
        summary = {
            "facts": ["old rounds"],
            "requirements": [],
            "decisions": [],
            "unfinished": [],
            "tool_results": [],
            "entities": [],
            "narrative": "compressed history",
        }
        provider = ScriptedProvider(
            responses=[
                ChatResponse(text="reply-1", usage=Usage()),
                ChatResponse(text="reply-2", usage=Usage()),
                ChatResponse(text="reply-3", usage=Usage()),
                context_error,
                ChatResponse(text=json.dumps(summary), usage=Usage(2, 1, 3)),
                ChatResponse(text="recovered", usage=Usage(3, 1, 4)),
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            for index in range(3):
                handle_request(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "retry",
                        "prompt": f"round-{index}",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            result = handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "retry",
                    "prompt": "recover this request",
                },
                root=root,
                provider_factory=lambda _: provider,
            )
        self.assertEqual(result["text"], "recovered")
        self.assertEqual(result["context"]["api_context_retries"], 1)
        self.assertEqual(len(provider.requests), 6)
        archive_path = find_window(root, "alice", "cli", "retry")
        self.assertIsNotNone(archive_path)
        archive = load_window(archive_path)
        _, runtime = load_runtime_window(archive_path, archive)
        self.assertEqual(archive["data"]["rounds"], 4)
        self.assertEqual(archive["data"]["memory_processed_round"], 0)
        self.assertEqual(archive["data"]["memory_status"], "queued")
        self.assertEqual(archive["data"]["memory_target_round"], 3)
        self.assertEqual(runtime["data"]["rounds"], 1)
        self.assertEqual(runtime["data"]["context"]["round_offset"], 3)

    def test_automatic_round_compression_trims_runtime_and_queues_memory(self) -> None:
        _, root = self.make_root()
        global_config_path = root / "config" / "global_config.json"
        global_config = json.loads(global_config_path.read_text("utf-8"))
        global_config.update(
            {
                "agents": {
                    "conserved_rounds": 3,
                    "max_rounds": 4,
                    "rounds_after_compression": 2,
                    "token_limit": 120000,
                    "token_compression_ratio": 0.6,
                },
                "history": {"recent_full_rounds": 1},
                "memory": {"extraction_mode": "compression_only"},
            }
        )
        global_config_path.write_text(json.dumps(global_config), "utf-8")
        summary = {
            "facts": ["rounds 1-2"],
            "requirements": [],
            "decisions": [],
            "unfinished": [],
            "tool_results": [],
            "entities": [],
            "narrative": "compressed rounds 1-2",
        }
        provider = ScriptedProvider(
            responses=[
                ChatResponse(text="reply-1", usage=Usage()),
                ChatResponse(text="reply-2", usage=Usage()),
                ChatResponse(text="reply-3", usage=Usage()),
                ChatResponse(text=json.dumps(summary), usage=Usage(2, 1, 3)),
                ChatResponse(text="reply-4", usage=Usage()),
                ChatResponse(text="reply-5", usage=Usage()),
            ]
        )

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            for index in range(1, 4):
                handle_request(
                    {
                        "user": "alice",
                        "source": "web",
                        "session_id": "automatic-compress",
                        "prompt": f"round-{index}",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            fourth = handle_request(
                {
                    "user": "alice",
                    "source": "web",
                    "session_id": "automatic-compress",
                    "prompt": "round-4",
                },
                root=root,
                provider_factory=lambda _: provider,
            )

            archive_path = find_window(root, "alice", "web", "automatic-compress")
            archive = load_window(archive_path)
            _, runtime = load_runtime_window(archive_path, archive)
            self.assertEqual(fourth["text"], "reply-4")
            self.assertEqual(archive["data"]["rounds"], 4)
            self.assertEqual(runtime["data"]["rounds"], 2)
            self.assertEqual(runtime["data"]["context"]["round_offset"], 2)
            self.assertEqual(archive["data"]["memory_status"], "queued")
            self.assertEqual(archive["data"]["memory_target_round"], 2)
            self.assertEqual(len(provider.requests), 5)

            fifth = handle_request(
                {
                    "user": "alice",
                    "source": "web",
                    "session_id": "automatic-compress",
                    "prompt": "round-5",
                },
                root=root,
                provider_factory=lambda _: provider,
            )

        self.assertEqual(fifth["text"], "reply-5")
        self.assertTrue(
            any(
                message.get("role") == "system"
                and "已移出完整上下文的历史摘要" in str(message.get("content") or "")
                and "compressed rounds 1-2" in str(message.get("content") or "")
                for message in provider.requests[-1].messages
            )
        )

    def test_cli_stream_reasoning_json_and_interrupt(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        cli.emit_event_stream(
            iter(
                [
                    RunEvent(type="reasoning_delta", content="think"),
                    RunEvent(type="text_delta", content="A"),
                    RunEvent(type="text_delta", content="B"),
                    RunEvent(type="done", metadata={"committed": True}),
                ]
            ),
            output="text",
            stdout=stdout,
            stderr=stderr,
            show_reasoning=False,
        )
        self.assertEqual(stdout.getvalue(), "AB\n")
        self.assertEqual(stderr.getvalue(), "")

        json_out = io.StringIO()
        cli.emit_event_stream(
            iter([RunEvent(type="usage", usage={"total_tokens": 2}), RunEvent(type="done")]),
            output="json",
            stdout=json_out,
            stderr=io.StringIO(),
            show_reasoning=True,
        )
        self.assertEqual([json.loads(line)["type"] for line in json_out.getvalue().splitlines()], ["usage", "done"])

        class Interrupting:
            closed = False
            def __iter__(self):
                return self
            def __next__(self):
                raise KeyboardInterrupt
            def close(self):
                self.closed = True

        source = Interrupting()
        with self.assertRaises(KeyboardInterrupt):
            cli.emit_event_stream(source, output="text", stdout=io.StringIO(), stderr=io.StringIO(), show_reasoning=False)
        self.assertTrue(source.closed)


if __name__ == "__main__":
    unittest.main()
