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
from provider.factory import ProviderCongestionError
from provider.adapters.compat import (
    chat_response_to_kemo,
    chat_stream_to_protocol,
    kemo_request_to_chat,
)
from provider.protocol.enums import MessageRole, ResponseStatus
from provider.protocol.models import (
    KemoResponse,
    MessageItem,
    ProviderState,
    ReasoningItem,
    ToolCallItem,
)
from provider.schema import ChatResponse, ProviderError, ToolCall, Usage
from run.agent_runner import AgentRunResult
from run.attachments import describe_uploaded_asset
from run.guidance import GuidanceInput
from run.engine import (
    compress_context,
    context_status,
    handle_request,
    iter_request_events,
)
from run.history import find_window, load_runtime_window, load_window
from run.history_index import find_record as find_history_record
from run.memory import MemoryStore
from run.memory_analysis import extract_memory_backlog, extract_round_memory
from run.tools import (
    ConsecutiveIdenticalToolCallTracker,
    MAX_TOOL_RESULT_CHARS,
    ToolCancelledError,
    ToolDefinition,
    ToolResultTooLargeError,
    ToolTimeoutError,
    apply_runtime_tool_policy,
    discover_tools,
    execute_tool,
    resolve_tool_timeout,
)


class ScriptedProvider:
    def __init__(
        self,
        responses: list[ChatResponse | BaseException] | None = None,
        streams: list[list[RunEvent]] | None = None,
    ) -> None:
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
    def test_uploaded_file_context_reaches_provider_without_polluting_saved_user_text(
        self,
    ) -> None:
        _, root = self.make_root()
        provider = ScriptedProvider(
            responses=[ChatResponse(text="attachment received")]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            handle_request(
                {
                    "user": "alice",
                    "source": "web",
                    "session_id": "uploaded-file",
                    "prompt": "请读取附件",
                    "uploaded_files": [
                        {
                            "name": "note.md",
                            "path": "users/alice/file_upload/note.md",
                            "size": 128,
                        }
                    ],
                },
                root=root,
                provider_factory=lambda _: provider,
            )
        current_user = next(
            message
            for message in reversed(provider.requests[0].messages)
            if message.get("role") == "user"
        )
        self.assertIn("users/alice/file_upload/note.md", current_user["content"])
        self.assertIn("可按需使用 file 工具读取", current_user["content"])
        window = load_window(find_window(root, "alice", "web", "uploaded-file"))
        self.assertEqual(window["text"]["messages"][0]["content"], "请读取附件")

    def make_root(
        self, *, stream: bool = False
    ) -> tuple[tempfile.TemporaryDirectory[str], Path]:
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
            json.dumps({"tools": {"enabled": True, "timeout": 2, "max_iterations": 4}}),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1, "provider": provider}),
            "utf-8",
        )
        project_agents = Path(__file__).resolve().parents[2] / "agents"
        shutil.copytree(project_agents, root / "agents")
        return temporary, root

    def copy_self_improve_plugins(self, root: Path) -> None:
        project_plugins = Path(__file__).resolve().parents[2] / "plugins"
        for name in ("memory_manage", "skill_creater"):
            shutil.copytree(project_plugins / name, root / "plugins" / name)

    def write_tool(
        self,
        base: Path,
        name: str,
        source_value: str,
        *,
        enabled: bool = True,
        async_tool: bool = False,
    ) -> None:
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
        sync = execute_tool(
            registry.get("sync_tool"), {"value": "a"}, context=context, timeout=2
        )
        async_result = execute_tool(
            registry.get("async_tool"), {"value": "b"}, context=context, timeout=2
        )
        self.assertEqual(sync["source"], "sync")
        self.assertEqual(async_result["source"], "async")

    def test_tool_execution_rejects_oversized_inline_result_with_range_hint(
        self,
    ) -> None:
        self.assertEqual(MAX_TOOL_RESULT_CHARS, 100_000)
        allowed_value = "X" * (MAX_TOOL_RESULT_CHARS - 2)
        allowed_tool = ToolDefinition(
            name="file",
            description="file",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["action", "path"],
                "additionalProperties": False,
            },
            version="1.0.0",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=Path.cwd(),
            _callable=lambda action, path: allowed_value,
        )
        self.assertEqual(
            execute_tool(
                allowed_tool,
                {"action": "read", "path": "large.log"},
                context={"root": str(Path.cwd()), "user": "alice"},
                timeout=2,
            ),
            allowed_value,
        )

        tool = ToolDefinition(
            name="file",
            description="file",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["action", "path"],
                "additionalProperties": False,
            },
            version="1.0.0",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=Path.cwd(),
            _callable=lambda action, path: {"content": "X" * MAX_TOOL_RESULT_CHARS},
        )

        with self.assertRaises(ToolResultTooLargeError) as raised:
            execute_tool(
                tool,
                {"action": "read", "path": "large.log"},
                context={"root": str(Path.cwd()), "user": "alice"},
                timeout=2,
            )

        error = raised.exception.error_payload()
        self.assertEqual(error["category"], "result_too_large")
        self.assertGreater(error["result_chars"], MAX_TOOL_RESULT_CHARS)
        self.assertEqual(error["limit_chars"], 100_000)
        self.assertTrue(error["content_omitted"])
        self.assertFalse(error["retryable"])
        self.assertIn("file.read_range", error["instruction"])
        self.assertNotIn("X" * 100, json.dumps(error, ensure_ascii=False))

    def test_running_tool_observes_emergency_cancel_without_waiting_for_timeout(
        self,
    ) -> None:
        cancel = threading.Event()
        tool = ToolDefinition(
            name="slow_tool",
            description="slow",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
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

    def test_explicit_tool_timeout_overrides_global_default(self) -> None:
        observed: dict[str, Any] = {}

        def run_with_timeout(
            timeout: float, *, context: dict[str, Any]
        ) -> dict[str, Any]:
            time.sleep(0.08)
            observed.update(context)
            return {"timeout": timeout}

        tool = ToolDefinition(
            name="timeout_tool",
            description="timeout",
            input_schema={
                "type": "object",
                "properties": {
                    "timeout": {"type": "number", "minimum": 0.01, "maximum": 3600}
                },
                "additionalProperties": False,
            },
            version="1.0.0",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=Path.cwd(),
            _callable=run_with_timeout,
        )
        result = execute_tool(
            tool,
            {"timeout": 0.2},
            context={"root": str(Path.cwd()), "user": "alice"},
            timeout=0.05,
        )
        self.assertEqual(result, {"timeout": 0.2})
        self.assertEqual(observed["tool_timeout"], 0.2)
        self.assertIsInstance(observed["cancel_event"], threading.Event)
        self.assertFalse(observed["cancel_event"].is_set())

    def test_omitted_tool_timeout_uses_global_default_and_signals_cleanup(self) -> None:
        observed_cancel = threading.Event()

        def wait_for_cancel(*, context: dict[str, Any]) -> None:
            if context["cancel_event"].wait(1):
                observed_cancel.set()

        tool = ToolDefinition(
            name="timeout_tool",
            description="timeout",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            version="1.0.0",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=Path.cwd(),
            _callable=wait_for_cancel,
        )
        with self.assertRaisesRegex(ToolTimeoutError, r"0\.03s"):
            execute_tool(
                tool,
                {},
                context={"root": str(Path.cwd()), "user": "alice"},
                timeout=0.03,
            )
        self.assertTrue(observed_cancel.wait(0.2))

    def test_subagent_tool_uses_agent_runtime_watchdog(self) -> None:
        tool = ToolDefinition(
            name="subagent_dispatch",
            description="subagent",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            version="1.0.0",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=Path.cwd(),
            timeout_policy="agent_runtime",
            _callable=lambda: None,
        )
        timeout = resolve_tool_timeout(
            tool,
            {},
            default_timeout=0.05,
            context={"agent_timeout": 0.2},
        )
        self.assertEqual(timeout, 5.2)
        tool._callable = lambda: (time.sleep(0.08), "completed")[1]
        self.assertEqual(
            execute_tool(
                tool,
                {},
                context={"agent_timeout": 0.2},
                timeout=0.02,
            ),
            "completed",
        )

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

    def test_plugin_whitelist_filters_registry(self) -> None:
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
                    "knowledge": {"use_shared": False, "use_global": True},
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
                    reasoning="完整工具续轮推理状态",
                    tool_calls=[
                        ToolCall(id="c1", name="lookup", arguments={"value": "x"})
                    ],
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
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "tool",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )
        self.assertEqual(
            [event.type for event in events],
            [
                "reasoning_delta",
                "tool_call_start",
                "usage",
                "tool_call_result",
                "text_delta",
                "usage",
                "done",
            ],
        )
        self.assertEqual(provider.requests[1].messages[-1]["role"], "tool")
        assistant_tool_message = next(
            message
            for message in provider.requests[1].messages
            if message.get("tool_calls")
        )
        self.assertEqual(
            assistant_tool_message["reasoning_content"],
            "完整工具续轮推理状态",
        )
        path = find_window(root, "alice", "cli", "tool")
        window = load_window(path)
        self.assertEqual(window["text"]["messages"][-1]["content"], "final")
        self.assertEqual(window["tool"]["rounds"][0]["calls"][0]["status"], "completed")
        self.assertGreaterEqual(
            window["tool"]["rounds"][0]["calls"][0]["elapsed_ms"], 0
        )
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
        self.assertEqual(
            window["data"]["round_metrics"][0]["usage"]["cache_miss_tokens"], 1
        )
        done = events[-1]
        self.assertEqual(done.usage["cached_prompt_tokens"], 2)
        self.assertEqual(done.usage["provider_request_count"], 2)
        self.assertAlmostEqual(done.usage["cache_hit_rate"], 2 / 3, places=5)
        self.assertGreaterEqual(done.metadata["elapsed_ms"], 0)

    def test_provider_request_refresh_gates_expand_and_perception_by_user_switches(
        self,
    ) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")

        global_expand = root / "global_expand"
        global_expand.mkdir()
        (global_expand / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    registry.add_expand_root('global', Path(__file__).resolve().parent)\n",
            "utf-8",
        )
        expand = global_expand / "live"
        expand.mkdir()
        expand_data = expand / "input_data.md"
        expand_data.write_text("EXPAND_REQUEST_ONE", "utf-8")
        (expand / "expand_control.md").write_text(
            "## 注入层\n\n\n## 操作层\n\nmanual", "utf-8"
        )
        (expand / "data_update.py").write_text("def update():\n    return True\n", "utf-8")
        (expand / "start_expand.py").write_text("def execute(command, params):\n    return {}\n", "utf-8")
        (expand / "expand.json").write_text(
            json.dumps(
                {
                    "name": "live",
                    "explain": "live data",
                    "open_input": True,
                    "input_data": "input_data.md",
                    "input_health": "正常",
                    "start_update": "data_update.py",
                    "open_control": False,
                    "start_expand": "start_expand.py",
                    "start_control": "expand_control.md",
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )

        global_sense = root / "global_sense"
        global_sense.mkdir()
        (global_sense / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    registry.add_perception(Path(__file__).resolve().parent)\n",
            "utf-8",
        )
        sense = global_sense / "live"
        sense.mkdir()
        sense_data = sense / "sense.md"
        sense_data.write_text("SENSE_REQUEST_ONE", "utf-8")
        (sense / "data_update.py").write_text("def update():\n    return True\n", "utf-8")
        (sense / "sense.json").write_text(
            json.dumps(
                {
                    "name": "live",
                    "data_md": "sense.md",
                    "recent_update": "2026-08-07 20:00:00",
                    "health": "正常",
                    "start_update": "data_update.py",
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )

        class UpdatingProvider(ScriptedProvider):
            def chat(self, request):
                response = super().chat(request)
                if len(self.requests) == 1:
                    expand_data.write_text("EXPAND_REQUEST_TWO", "utf-8")
                    sense_data.write_text("SENSE_REQUEST_TWO", "utf-8")
                return response

        provider = UpdatingProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("refresh", "lookup", {"value": "x"})],
                    finish_reason="tool_calls",
                    usage=Usage(),
                ),
                ChatResponse(text="done", finish_reason="stop", usage=Usage()),
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "dynamic-sources",
                    "prompt": "go",
                },
                root=root,
                provider_factory=lambda _: provider,
            )

        first_system = provider.requests[0].messages[0]["content"]
        second_system = provider.requests[1].messages[0]["content"]
        self.assertIn("EXPAND_REQUEST_ONE", first_system)
        self.assertIn("SENSE_REQUEST_ONE", first_system)
        self.assertNotIn("EXPAND_REQUEST_TWO", first_system)
        self.assertIn("EXPAND_REQUEST_ONE", second_system)
        self.assertIn("SENSE_REQUEST_ONE", second_system)
        self.assertNotIn("EXPAND_REQUEST_TWO", second_system)
        self.assertNotIn("SENSE_REQUEST_TWO", second_system)

        user_config_path = root / "users" / "alice" / "user_config.json"
        user_config = json.loads(user_config_path.read_text("utf-8"))
        user_config["expand"] = {
            "global_whitelist": [],
            "shared_whitelist": [],
            "realtime_injection": True,
        }
        user_config["perception"] = {
            "global_whitelist": [],
            "realtime_injection": True,
        }
        user_config_path.write_text(json.dumps(user_config), "utf-8")
        expand_data.write_text("EXPAND_REQUEST_ONE", "utf-8")
        sense_data.write_text("SENSE_REQUEST_ONE", "utf-8")
        realtime_provider = UpdatingProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("refresh-live", "lookup", {"value": "x"})],
                    finish_reason="tool_calls",
                    usage=Usage(),
                ),
                ChatResponse(text="done", finish_reason="stop", usage=Usage()),
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "dynamic-sources-realtime",
                    "prompt": "go",
                },
                root=root,
                provider_factory=lambda _: realtime_provider,
            )

        realtime_first = realtime_provider.requests[0].messages[0]["content"]
        realtime_second = realtime_provider.requests[1].messages[0]["content"]
        self.assertIn("SENSE_REQUEST_ONE", realtime_first)
        self.assertIn("EXPAND_REQUEST_TWO", realtime_second)
        self.assertIn("SENSE_REQUEST_TWO", realtime_second)
        self.assertNotIn("SENSE_REQUEST_ONE", realtime_second)

    def test_oversized_tool_result_is_omitted_before_provider_and_history(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "large_result", "large")
        (root / "plugins" / "large_result" / "tool.py").write_text(
            "def run(value, *, context):\n"
            f"    return {{'content': 'X' * {MAX_TOOL_RESULT_CHARS + 1}, 'value': value}}\n",
            "utf-8",
        )
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("large-1", "large_result", {"value": "x"})],
                    finish_reason="tool_calls",
                    usage=Usage(1_000, 10, 1_010, estimated=False),
                ),
                ChatResponse(
                    text="已改用较小范围。",
                    usage=Usage(2_000, 20, 2_020, estimated=False),
                ),
            ]
        )

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "oversized-tool-result",
                        "prompt": "读取",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        result_event = next(
            event for event in events if event.type == "tool_call_result"
        )
        self.assertEqual(result_event.metadata["status"], "result_too_large")
        self.assertFalse(result_event.result["ok"])
        error = result_event.result["error"]
        self.assertEqual(error["exception_type"], "ToolResultTooLargeError")
        self.assertTrue(error["content_omitted"])
        self.assertGreater(error["result_chars"], MAX_TOOL_RESULT_CHARS)
        self.assertNotIn("X" * 100, json.dumps(result_event.result, ensure_ascii=False))
        self.assertEqual(len(provider.requests), 2)
        continuation = provider.requests[1].messages[-1]
        self.assertEqual(continuation["role"], "tool")
        self.assertIn("ToolResultTooLargeError", continuation["content"])
        self.assertLess(len(continuation["content"]), 2_000)
        self.assertIsNotNone(provider.requests[1].tools)
        self.assertEqual(events[-1].type, "done")

        window_path = find_window(
            root,
            "alice",
            "cli",
            "oversized-tool-result",
        )
        window = load_window(window_path)
        stored_call = window["tool"]["rounds"][0]["calls"][0]
        self.assertEqual(stored_call["status"], "result_too_large")
        self.assertTrue(stored_call["result"]["error"]["content_omitted"])
        self.assertNotIn("X" * 100, json.dumps(window, ensure_ascii=False))
        self.assertLess(len(json.dumps(window["tool"], ensure_ascii=False)), 10_000)
        self.assertLess(len(json.dumps(window["items"], ensure_ascii=False)), 20_000)

    def test_native_provider_state_is_preserved_across_tool_continuation(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")

        class NativeStateProvider:
            def __init__(self) -> None:
                self.requests = []

            def create(self, request):
                self.requests.append(request)
                if len(self.requests) <= 2:
                    index = len(self.requests)
                    return KemoResponse(
                        request_id=request.request_id,
                        status=ResponseStatus.REQUIRES_ACTION,
                        model=request.model,
                        output=[
                            ReasoningItem(
                                # 故意模拟每次响应重复使用同一个 item id。
                                id="reasoning-1",
                                content=f"原生推理内容 {index}",
                                provider_state=ProviderState(
                                    kind="opaque",
                                    data=f"opaque-state-{index}",
                                    provider="stateful-provider",
                                    model=request.model,
                                ),
                            ),
                            ToolCallItem(
                                id=f"tool-call-{index}",
                                call_id=f"call-native-{index}",
                                name="lookup",
                                arguments={"value": str(index)},
                            ),
                        ],
                    )
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.COMPLETED,
                    model=request.model,
                    output=[
                        MessageItem.text(
                            MessageRole.ASSISTANT,
                            "native done",
                        )
                    ],
                )

        provider = NativeStateProvider()
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "native-provider-state",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(events[-1].type, "done")
        second_reasoning = next(
            item
            for item in provider.requests[1].input
            if isinstance(item, ReasoningItem)
        )
        self.assertEqual(second_reasoning.content, "原生推理内容 1")
        self.assertIsNotNone(second_reasoning.provider_state)
        self.assertEqual(second_reasoning.provider_state.data, "opaque-state-1")
        third_reasoning = [
            item
            for item in provider.requests[2].input
            if isinstance(item, ReasoningItem)
        ]
        self.assertEqual(
            [item.provider_state.data for item in third_reasoning],
            ["opaque-state-1", "opaque-state-2"],
        )
        self.assertEqual(len({item.id for item in third_reasoning}), 2)

        # 新一轮对话会从已提交历史中回放前一轮的两个 reasoning item。
        # Provider 允许在不同响应中重复使用 item id，桥接层必须在构造
        # 新 KemoRequest 时重新保证请求内唯一，而不是拒绝第二轮对话。
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            continued = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "native-provider-state",
                        "prompt": "continue",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )
        self.assertEqual(continued[-1].type, "done")
        historical_reasoning = [
            item
            for item in provider.requests[3].input
            if isinstance(item, ReasoningItem)
        ]
        self.assertEqual(
            [item.provider_state.data for item in historical_reasoning],
            ["opaque-state-1", "opaque-state-2"],
        )
        self.assertEqual(len({item.id for item in historical_reasoning}), 2)

    def test_stream_tool_continuation_preserves_reasoning_content(self) -> None:
        _, root = self.make_root(stream=True)
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            streams=[
                [
                    RunEvent(type="reasoning_delta", content="流式工具续轮状态"),
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="stream-call",
                        tool_name="lookup",
                        arguments={"value": "x"},
                    ),
                    RunEvent(type="usage", usage={}),
                ],
                [
                    RunEvent(type="text_delta", content="stream done"),
                    RunEvent(type="usage", usage={}),
                ],
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "stream-tool-continuation",
                        "prompt": "go",
                        "stream": True,
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(events[-1].type, "done")
        assistant_tool_message = next(
            message
            for message in provider.requests[1].messages
            if message.get("tool_calls")
        )
        self.assertEqual(
            assistant_tool_message["reasoning_content"],
            "流式工具续轮状态",
        )

    def test_tool_call_limit_commits_terminal_round_and_can_continue(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        global_path = root / "config" / "global_config.json"
        config = json.loads(global_path.read_text("utf-8"))
        config["tools"]["max_iterations"] = 1
        global_path.write_text(json.dumps(config), "utf-8")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="正在查询",
                    tool_calls=[
                        ToolCall(
                            id="allowed-call", name="lookup", arguments={"value": "x"}
                        ),
                        ToolCall(
                            id="pending-limit", name="lookup", arguments={"value": "y"}
                        ),
                    ],
                    finish_reason="tool_calls",
                    usage=Usage(10, 2, 12),
                )
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "tool-limit",
                        "prompt": "开始",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        terminal = events[-1]
        self.assertEqual(terminal.type, "done")
        self.assertTrue(terminal.metadata["committed"])
        self.assertEqual(terminal.metadata["status"], "limited")
        self.assertEqual(terminal.metadata["stop_reason"], "max_tool_iterations")
        self.assertEqual(terminal.metadata["tool_calls"], 2)
        window_path = find_window(root, "alice", "cli", "tool-limit")
        window = load_window(window_path)
        self.assertEqual(window["data"]["rounds"], 1)
        self.assertEqual(window["data"]["round_metrics"][0]["status"], "limited")
        self.assertEqual(
            window["data"]["round_metrics"][0]["stop_reason"],
            "max_tool_iterations",
        )
        self.assertEqual(window["text"]["messages"][0]["content"], "开始")
        self.assertIn("最大次数 1", window["text"]["messages"][1]["content"])
        calls = window["tool"]["rounds"][0]["calls"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["status"], "completed")
        self.assertEqual(calls[1]["status"], "not_executed")
        self.assertEqual(
            calls[1]["result"]["error"]["exception_type"],
            "ToolCallLimitExceeded",
        )
        durable_items = window["items"]["items"]
        call_item = next(item for item in durable_items if item["type"] == "tool_call")
        result_item = next(
            item for item in durable_items if item["type"] == "tool_result"
        )
        self.assertIn(call_item["call_id"], {"allowed-call", "pending-limit"})
        self.assertIn(result_item["call_id"], {"allowed-call", "pending-limit"})
        pending_result = next(
            item
            for item in durable_items
            if item["type"] == "tool_result" and item["call_id"] == "pending-limit"
        )
        self.assertTrue(pending_result["is_error"])

        continue_provider = ScriptedProvider(
            responses=[ChatResponse(text="继续完成", usage=Usage(5, 2, 7))]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            continued = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "tool-limit",
                        "prompt": "继续",
                    },
                    root=root,
                    provider_factory=lambda _: continue_provider,
                )
            )
        self.assertEqual(continued[-1].type, "done")
        resumed = load_window(window_path)
        self.assertEqual(resumed["data"]["rounds"], 2)
        self.assertEqual(resumed["text"]["messages"][-2]["content"], "继续")
        self.assertEqual(resumed["text"]["messages"][-1]["content"], "继续完成")

    def test_tool_loop_guard_uses_exact_provider_input_plus_local_increment(
        self,
    ) -> None:
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
            patch(
                "run.conversation_runtime.estimate_messages_tokens",
                side_effect=inflated_messages,
            ),
            patch("run.conversation_runtime.estimate_tools_tokens", return_value=0),
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

    def test_tool_loop_guard_reports_exact_projection_and_file_result_size(
        self,
    ) -> None:
        _, root = self.make_root()
        project_file_tool = Path(__file__).resolve().parents[2] / "plugins" / "file"
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
            patch(
                "run.conversation_runtime.estimate_messages_tokens",
                side_effect=growing_messages,
            ),
            patch("run.conversation_runtime.estimate_tools_tokens", return_value=0),
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

        terminal = events[-1]
        self.assertEqual(terminal.type, "done")
        self.assertTrue(terminal.metadata["committed"])
        self.assertEqual(terminal.metadata["status"], "limited")
        self.assertEqual(terminal.metadata["stop_reason"], "tool_context_limit")
        guard = terminal.metadata["context_guard"]
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
        window = load_window(
            find_window(root, "alice", "cli", "provider-context-overflow")
        )
        self.assertEqual(window["data"]["rounds"], 1)
        self.assertEqual(window["data"]["round_metrics"][0]["status"], "limited")
        self.assertEqual(
            window["data"]["round_metrics"][0]["stop_reason"],
            "tool_context_limit",
        )

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
            patch(
                "run.conversation_runtime._extract_round_memory",
                side_effect=extract_after_commit,
            ),
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

    def test_extract_round_memory_persists_candidates_and_contains_failures(
        self,
    ) -> None:
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
        result = extract_round_memory(
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
        self.assertEqual(
            runner.input_data["source"], {"source": "round_commit", "round": 3}
        )
        self.assertEqual(
            MemoryStore(root, "alice", {}).get_entry("seven_days", "device.md")[
                "content"
            ],
            "用户设备为 J1900。",
        )

        failed = extract_round_memory(
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

    def test_failed_round_is_skipped_by_later_memory_extraction(self) -> None:
        _, root = self.make_root(stream=True)
        provider = ScriptedProvider(
            streams=[
                [RunEvent(type="error", error={"message": "upstream unavailable"})]
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "failed-memory",
                        "prompt": "do not extract this failed round",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        class RejectingRunner:
            def run(self, *args, **kwargs):
                del args, kwargs
                raise AssertionError("failed round must not reach memory agent")

        path = find_window(root, "alice", "cli", "failed-memory")
        window = load_window(path)
        result = extract_memory_backlog(
            root=root,
            user="alice",
            source="cli",
            session_id="failed-memory",
            directory=path,
            window=window,
            config={"memory": {"extraction_mode": "compression_only"}},
            agent_runner=RejectingRunner(),  # type: ignore[arg-type]
            cancel_event=None,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(result["extraction"]["reason"], "failed_rounds")
        self.assertEqual(result["extraction"]["skipped_rounds"], [1])

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
        self.assertEqual(
            applied[0].metadata["guidance"], ["focus on the revised target"]
        )
        window = load_window(find_window(root, "alice", "cli", "guided"))
        self.assertEqual(
            window["data"]["round_metrics"][0]["guidance"],
            ["focus on the revised target"],
        )

    def test_runtime_attachment_only_guidance_reaches_provider_and_history(
        self,
    ) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        upload = root / "users" / "alice" / "file_upload"
        upload.mkdir(parents=True)
        note = upload / "revised-target.md"
        note.write_text("focus on the media-backed target", "utf-8")
        descriptor = describe_uploaded_asset(
            root,
            "alice",
            {"path": "users/alice/file_upload/revised-target.md"},
        )
        guidance: queue.Queue[GuidanceInput] = queue.Queue()
        guidance.put(
            GuidanceInput(
                id="guidance_attachment_only",
                uploaded_files=[descriptor],
            )
        )
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("g2", "lookup", {"value": "x"})],
                    usage=Usage(),
                ),
                ChatResponse(text="guided by attachment", usage=Usage()),
            ]
        )

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "guided-attachment",
                        "prompt": "start",
                        "run_id": "run_guided_attachment",
                        "_guidance_queue": guidance,
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        guidance_message = next(
            item
            for item in provider.requests[1].messages
            if item.get("role") == "user" and "运行中引导" in item.get("content", "")
        )
        self.assertIn("revised-target.md", guidance_message["content"])
        self.assertIn("multimodal", guidance_message["content"])
        applied = next(event for event in events if event.type == "guidance_applied")
        self.assertEqual(
            applied.metadata["guidance_details"][0]["id"],
            "guidance_attachment_only",
        )
        window = load_window(find_window(root, "alice", "cli", "guided-attachment"))
        detail = window["data"]["round_metrics"][0]["guidance_details"][0]
        self.assertEqual(detail["uploaded_files"][0]["name"], "revised-target.md")
        self.assertNotIn("path", detail["uploaded_files"][0])

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
                    usage={
                        "prompt_tokens": 1,
                        "completion_tokens": 2,
                        "total_tokens": 3,
                    },
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

    def test_stream_without_done_yields_error_and_commits_failed_round(self) -> None:
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
        self.assertTrue(events[0].metadata["committed"])
        self.assertEqual(events[0].metadata["status"], "failed")
        window = load_window(find_window(root, "alice", "cli", "missing-done"))
        self.assertEqual(window["data"]["rounds"], 1)
        self.assertEqual(window["data"]["round_metrics"][0]["status"], "failed")

    def test_provider_congestion_commits_failed_round(self) -> None:
        _, root = self.make_root()
        with (
            patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False),
            patch(
                "run.conversation_runtime.provider_request_slot",
                side_effect=ProviderCongestionError("provider busy"),
            ),
        ):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "provider-busy",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: ScriptedProvider(),
                )
            )

        self.assertEqual([event.type for event in events], ["error"])
        self.assertEqual(events[0].metadata["status"], "failed")
        self.assertEqual(events[0].metadata["stop_reason"], "provider_congestion")
        window = load_window(find_window(root, "alice", "cli", "provider-busy"))
        self.assertEqual(window["data"]["round_metrics"][0]["status"], "failed")

    def test_native_kemo_invalid_tool_arguments_retry_before_tool_execution(
        self,
    ) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")

        class NativeProvider:
            def __init__(self) -> None:
                self.requests = []
                self.responses = [
                    KemoResponse(
                        request_id="placeholder",
                        status=ResponseStatus.REQUIRES_ACTION,
                        model="mock",
                        output=[
                            ToolCallItem(
                                id="invalid-item",
                                call_id="invalid-call",
                                name="lookup",
                                arguments={},
                                arguments_raw='{"value":"unfinished',
                                parse_error={"message": "Unterminated string"},
                            )
                        ],
                    ),
                    KemoResponse(
                        request_id="placeholder",
                        status=ResponseStatus.REQUIRES_ACTION,
                        model="mock",
                        output=[
                            ToolCallItem(
                                id="valid-item",
                                call_id="valid-call",
                                name="lookup",
                                arguments={"value": "safe"},
                            )
                        ],
                    ),
                    KemoResponse(
                        request_id="placeholder",
                        status=ResponseStatus.COMPLETED,
                        model="mock",
                        output=[
                            MessageItem.text(MessageRole.ASSISTANT, "completed")
                        ],
                    ),
                ]

            def create(self, request):
                self.requests.append(request)
                return self.responses.pop(0).model_copy(
                    update={"request_id": request.request_id}
                )

        provider = NativeProvider()
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "native-tool-argument-retry",
                        "prompt": "lookup",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(events[-1].type, "done")
        self.assertFalse(any(event.type == "error" for event in events))
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(len({request.request_id for request in provider.requests}), 3)
        retry_messages = kemo_request_to_chat(provider.requests[1]).messages
        self.assertIn("provider_tool_argument_repair", retry_messages[0]["content"])
        self.assertEqual(provider.requests[1].metadata["tool_argument_retry"], 1)
        self.assertEqual(events[-1].metadata["tool_argument_retries"], 1)
        window = load_window(
            find_window(root, "alice", "cli", "native-tool-argument-retry")
        )
        self.assertEqual(len(window["tool"]["rounds"][0]["calls"]), 1)
        self.assertEqual(
            window["data"]["round_metrics"][0]["tool_argument_retries"], 1
        )

    def test_chat_stream_invalid_tool_arguments_retry_without_duplicate_card(
        self,
    ) -> None:
        _, root = self.make_root(stream=True)
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            streams=[
                [
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="invalid-call",
                        tool_name="lookup",
                        arguments={},
                        metadata={
                            "raw_arguments": '{"value":"unfinished',
                            "parse_error": {"message": "Unterminated string"},
                            "finish_reason": "tool_calls",
                        },
                    ),
                    RunEvent(type="done", metadata={"finish_reason": "tool_calls"}),
                ],
                [
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="valid-call",
                        tool_name="lookup",
                        arguments={"value": "safe"},
                        metadata={"finish_reason": "tool_calls"},
                    ),
                    RunEvent(type="done", metadata={"finish_reason": "tool_calls"}),
                ],
                [
                    RunEvent(type="text_delta", content="completed"),
                    RunEvent(type="done", metadata={"finish_reason": "stop"}),
                ],
            ]
        )

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "chat-tool-argument-retry",
                        "prompt": "lookup",
                        "stream": True,
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(events[-1].type, "done")
        self.assertFalse(any(event.type == "error" for event in events))
        self.assertEqual(
            len([event for event in events if event.type == "tool_call_start"]), 1
        )
        self.assertEqual(len(provider.requests), 3)
        self.assertIn(
            "provider_tool_argument_repair",
            provider.requests[1].messages[0]["content"],
        )

    def test_invalid_tool_arguments_with_visible_output_is_not_retried(self) -> None:
        _, root = self.make_root(stream=True)
        provider = ScriptedProvider(
            streams=[
                [
                    RunEvent(type="text_delta", content="visible"),
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="invalid-call",
                        tool_name="lookup",
                        arguments={},
                        metadata={
                            "raw_arguments": "{",
                            "parse_error": {"message": "invalid"},
                            "finish_reason": "tool_calls",
                        },
                    ),
                    RunEvent(type="done", metadata={"finish_reason": "tool_calls"}),
                ]
            ]
        )

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "visible-invalid-tool-arguments",
                        "prompt": "lookup",
                        "stream": True,
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual([event.type for event in events], ["text_delta", "error"])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(events[-1].error["retry_count"], 0)
        self.assertEqual(events[-1].error["retry_limit"], 2)

    def test_invalid_tool_arguments_retry_limit_commits_one_failed_round(self) -> None:
        _, root = self.make_root()

        def invalid_response(number: int) -> ChatResponse:
            return ChatResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id=f"invalid-{number}",
                        name="expand_call",
                        arguments={},
                        arguments_raw="{",
                        parse_error={"message": "invalid"},
                    )
                ],
                finish_reason="tool_calls",
            )

        provider = ScriptedProvider(
            responses=[invalid_response(1), invalid_response(2), invalid_response(3)]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "invalid-tool-argument-limit",
                        "prompt": "call expand",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(events[-1].type, "error")
        self.assertFalse(any(event.type == "text_delta" for event in events))
        self.assertFalse(any(event.type == "tool_call_start" for event in events))
        self.assertEqual(len([event for event in events if event.type == "error"]), 1)
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(events[-1].error["retry_count"], 2)
        self.assertEqual(events[-1].error["retry_limit"], 2)
        window = load_window(
            find_window(root, "alice", "cli", "invalid-tool-argument-limit")
        )
        self.assertEqual(window["data"]["rounds"], 1)
        self.assertEqual(window["data"]["round_metrics"][0]["status"], "failed")

    def test_unrecoverable_context_error_commits_failed_round(self) -> None:
        _, root = self.make_root()
        provider = ScriptedProvider(
            responses=[
                ProviderError(
                    "oversized request",
                    status_code=400,
                    category="context_length_exceeded",
                )
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "context-unrecoverable",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual([event.type for event in events], ["error"])
        self.assertTrue(events[0].metadata["committed"])
        self.assertEqual(
            events[0].metadata["stop_reason"],
            "provider_context_recovery_failed",
        )
        window = load_window(find_window(root, "alice", "cli", "context-unrecoverable"))
        self.assertEqual(window["data"]["rounds"], 1)
        self.assertEqual(window["data"]["round_metrics"][0]["status"], "failed")

    def test_error_and_cancel_both_commit_terminal_rounds(self) -> None:
        _, root = self.make_root(stream=True)
        error_provider = ScriptedProvider(
            streams=[
                [
                    RunEvent(type="text_delta", content="partial"),
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="pending-error",
                        tool_name="lookup",
                        arguments={"value": "x"},
                    ),
                    RunEvent(
                        type="error",
                        error={
                            "message": "sensitive upstream body",
                            "code": "rate_limit",
                            "provider_status": 429,
                        },
                    ),
                ]
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "err",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: error_provider,
                )
            )
        self.assertEqual(
            [event.type for event in events],
            [
                "text_delta",
                "tool_call_start",
                "error",
            ],
        )
        self.assertTrue(events[-1].metadata["committed"])
        self.assertEqual(events[-1].metadata["status"], "failed")
        self.assertEqual(events[-1].metadata["stop_reason"], "provider_error_event")
        error_path = find_window(root, "alice", "cli", "err")
        error_window = load_window(error_path)
        self.assertEqual(error_window["data"]["rounds"], 1)
        self.assertIn("partial", error_window["text"]["messages"][1]["content"])
        self.assertIn(
            "模型服务错误中断",
            error_window["text"]["messages"][1]["content"],
        )
        failed_metric = error_window["data"]["round_metrics"][0]
        self.assertEqual(failed_metric["status"], "failed")
        self.assertEqual(failed_metric["failure"]["code"], "rate_limit")
        self.assertEqual(failed_metric["failure"]["provider_status"], 429)
        self.assertNotIn(
            "sensitive upstream body",
            json.dumps(error_window, ensure_ascii=False),
        )
        failed_call = error_window["tool"]["rounds"][0]["calls"][0]
        self.assertEqual(failed_call["id"], "pending-error")
        self.assertEqual(failed_call["status"], "failed")
        self.assertEqual(
            failed_call["result"]["error"]["exception_type"],
            "ProviderRunInterrupted",
        )

        cancel = threading.Event()
        cancel_provider = ScriptedProvider(
            streams=[
                [
                    RunEvent(type="text_delta", content="partial"),
                    RunEvent(type="usage", usage={}),
                    RunEvent(type="done", usage={}),
                ]
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            iterator = iter_request_events(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "cancel",
                    "prompt": "go",
                },
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
        self.assertEqual(
            cancel_window["data"]["round_metrics"][0]["status"], "cancelled"
        )
        self.assertEqual(cancel_window["text"]["messages"][0]["content"], "go")
        self.assertIn("partial", cancel_window["text"]["messages"][1]["content"])
        self.assertIn("紧急停止", cancel_window["text"]["messages"][1]["content"])

        next_provider = ScriptedProvider(
            streams=[
                [
                    RunEvent(type="text_delta", content="next"),
                    RunEvent(type="usage", usage={}),
                    RunEvent(type="done", usage={}),
                ]
            ]
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
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall("1", "lookup", {"value": "x"}),
                        ToolCall("2", "lookup", {"value": "x"}),
                    ],
                    usage=Usage(),
                ),
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

    def test_file_live_read_is_executed_again_in_the_same_run(self) -> None:
        _, root = self.make_root()
        project_plugins = Path(__file__).resolve().parents[2] / "plugins"
        shutil.copytree(project_plugins / "file", root / "plugins" / "file")
        arguments = {"action": "list_dir", "path": str(root)}
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall("list-before", "file", arguments),
                        ToolCall("list-after", "file", arguments),
                    ],
                    usage=Usage(),
                ),
                ChatResponse(text="done", usage=Usage()),
            ]
        )
        with (
            patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False),
            patch(
                "run.conversation_runtime.execute_tool",
                side_effect=[
                    {"ok": True, "entries": [{"name": "old"}]},
                    {"ok": True, "entries": [{"name": "new"}]},
                ],
            ) as mocked_execute,
        ):
            handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "file-live-read",
                    "prompt": "list, modify, then list again",
                },
                root=root,
                provider_factory=lambda _: provider,
            )

        self.assertEqual(mocked_execute.call_count, 2)
        window = load_window(find_window(root, "alice", "cli", "file-live-read"))
        calls = window["tool"]["rounds"][0]["calls"]
        self.assertEqual([call["status"] for call in calls], ["completed", "completed"])
        self.assertFalse(calls[1]["duplicate"])
        self.assertEqual(calls[1]["result"]["result"]["entries"][0]["name"], "new")

    def test_expand_status_is_refreshed_after_activation_in_the_same_run(self) -> None:
        _, root = self.make_root()
        project_plugins = Path(__file__).resolve().parents[2] / "plugins"
        shutil.copytree(
            project_plugins / "expand_call",
            root / "plugins" / "expand_call",
        )
        status_arguments = {
            "scope": "global",
            "module": "kemo_graph",
            "command": "status",
        }
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall("status-before", "expand_call", status_arguments),
                        ToolCall(
                            "activate",
                            "expand_call",
                            {
                                "scope": "global",
                                "module": "kemo_graph",
                                "command": "activate",
                                "params": {"base_url": "http://127.0.0.1:8000/api/v1"},
                            },
                        ),
                        ToolCall("status-after", "expand_call", status_arguments),
                    ],
                    usage=Usage(),
                ),
                ChatResponse(text="done", usage=Usage()),
            ]
        )
        with (
            patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False),
            patch(
                "run.conversation_runtime.execute_tool",
                side_effect=[
                    {"status": "inactive", "active": False},
                    {"status": "active", "active": True},
                    {"status": "active", "active": True},
                ],
            ) as mocked_execute,
        ):
            handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "expand-live-status",
                    "prompt": "activate and refresh",
                },
                root=root,
                provider_factory=lambda _: provider,
            )
        self.assertEqual(mocked_execute.call_count, 3)
        window = load_window(find_window(root, "alice", "cli", "expand-live-status"))
        calls = window["tool"]["rounds"][0]["calls"]
        self.assertEqual([call["status"] for call in calls], ["completed"] * 3)
        self.assertFalse(calls[2]["duplicate"])
        self.assertTrue(calls[2]["result"]["result"]["active"])

    def test_expand_mutation_result_keeps_duplicate_side_effect_protection(self) -> None:
        _, root = self.make_root()
        project_plugins = Path(__file__).resolve().parents[2] / "plugins"
        shutil.copytree(
            project_plugins / "expand_call",
            root / "plugins" / "expand_call",
        )
        sync_arguments = {
            "scope": "global",
            "module": "kemo_graph",
            "command": "sync",
            "params": {"library_ids": ["project_docs"]},
        }
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall("sync-first", "expand_call", sync_arguments),
                        ToolCall(
                            "ingest",
                            "expand_call",
                            {
                                "scope": "global",
                                "module": "kemo_graph",
                                "command": "ingest",
                                "params": {
                                    "library_ids": ["project_docs"],
                                    "mode": "both",
                                },
                            },
                        ),
                        ToolCall("sync-repeat", "expand_call", sync_arguments),
                    ],
                    usage=Usage(),
                ),
                ChatResponse(text="done", usage=Usage()),
            ]
        )
        with (
            patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False),
            patch(
                "run.conversation_runtime.execute_tool",
                side_effect=[
                    {"ok": True, "operation": "sync"},
                    {"ok": True, "operation": "ingest"},
                ],
            ) as mocked_execute,
        ):
            handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "expand-mutation-dedup",
                    "prompt": "sync, ingest, then repeat sync",
                },
                root=root,
                provider_factory=lambda _: provider,
            )
        self.assertEqual(mocked_execute.call_count, 2)
        window = load_window(
            find_window(root, "alice", "cli", "expand-mutation-dedup")
        )
        calls = window["tool"]["rounds"][0]["calls"]
        self.assertEqual(
            [call["status"] for call in calls],
            ["completed", "completed", "duplicate_reused"],
        )
        self.assertTrue(calls[2]["duplicate"])

    def test_failed_duplicate_call_executes_again_and_preserves_retry_metadata(
        self,
    ) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall("1", "lookup", {"value": "x"}),
                        ToolCall("2", "lookup", {"value": "x"}),
                    ],
                    usage=Usage(),
                ),
                ChatResponse(text="done", usage=Usage()),
            ]
        )
        failure = ProviderError(
            "temporary tool provider failure",
            category="upstream_error",
            status_code=502,
            retryable=True,
        )
        failure.attempt_count = 2
        with (
            patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False),
            patch(
                "run.conversation_runtime.execute_tool",
                side_effect=[failure, {"value": "x"}],
            ) as mocked_execute,
        ):
            handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "failed-duplicate",
                    "prompt": "go",
                },
                root=root,
                provider_factory=lambda _: provider,
            )

        self.assertEqual(mocked_execute.call_count, 2)
        window = load_window(find_window(root, "alice", "cli", "failed-duplicate"))
        calls = window["tool"]["rounds"][0]["calls"]
        self.assertFalse(calls[0]["duplicate"])
        self.assertFalse(calls[1]["duplicate"])
        self.assertEqual(calls[0]["status"], "failed")
        self.assertEqual(calls[1]["status"], "completed")
        error = calls[0]["result"]["error"]
        self.assertEqual(error["category"], "upstream_error")
        self.assertEqual(error["status_code"], 502)
        self.assertTrue(error["retryable"])
        self.assertEqual(error["attempt_count"], 2)

    def test_cancelled_round_pairs_pending_tool_call_with_cancel_result(self) -> None:
        _, root = self.make_root(stream=True)
        cancel = threading.Event()
        provider = ScriptedProvider(
            streams=[
                [
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="pending-1",
                        tool_name="lookup",
                        arguments={"value": "x"},
                    ),
                    RunEvent(type="usage", usage={}),
                    RunEvent(type="done", usage={}),
                ]
            ]
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
        result_item = next(
            item for item in durable_items if item["type"] == "tool_result"
        )
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

    def test_ninth_consecutive_identical_call_is_blocked_but_changed_arguments_continue(
        self,
    ) -> None:
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
        self.assertTrue(
            all(call["status"] == "duplicate_reused" for call in calls[1:8])
        )
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
                ChatResponse(
                    text="", tool_calls=[ToolCall("f1", "unstable", {"value": "1"})]
                ),
                ChatResponse(
                    text="", tool_calls=[ToolCall("f2", "unstable", {"value": "2"})]
                ),
                ChatResponse(
                    text="", tool_calls=[ToolCall("f3", "unstable", {"value": "3"})]
                ),
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
            responses=[
                ChatResponse(text=f"reply-{index}", usage=Usage())
                for index in range(12)
            ]
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
                    ),
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
        self.assertEqual(
            MemoryStore(root, "alice", {}).get_entry("seven_days", "压缩记忆.md")[
                "content"
            ],
            "old rounds fact",
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
            responses=[
                ChatResponse(text=f"reply-{index}", usage=Usage())
                for index in range(12)
            ]
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

    def test_manual_compression_of_seventy_five_rounds_is_verified_and_next_round_runs(
        self,
    ) -> None:
        _, root = self.make_root()
        global_config_path = root / "config" / "global_config.json"
        global_config = json.loads(global_config_path.read_text("utf-8"))
        global_config.update(
            {
                "agents": {
                    "conserved_rounds": 3,
                    "max_rounds": 80,
                    "rounds_after_compression": 20,
                    "token_limit": 120_000,
                    "token_compression_ratio": 0.6,
                },
                "history": {"recent_full_rounds": 3},
                "memory": {"extraction_mode": "compression_only"},
            }
        )
        global_config_path.write_text(json.dumps(global_config), "utf-8")
        summary = {
            "facts": ["前 55 轮已压缩"],
            "requirements": [],
            "decisions": [],
            "unfinished": [],
            "tool_results": [],
            "entities": [],
            "narrative": "已精炼前 55 轮正文、思考和工具结论",
        }
        seed_provider = ScriptedProvider(
            responses=[
                ChatResponse(text=f"assistant-{number}", usage=Usage())
                for number in range(1, 76)
            ]
        )
        summary_provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text=json.dumps(summary, ensure_ascii=False), usage=Usage()
                )
            ]
        )
        continuation_provider = ScriptedProvider(
            responses=[ChatResponse(text="round-76-reply", usage=Usage())]
        )

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            for number in range(1, 76):
                handle_request(
                    {
                        "user": "alice",
                        "source": "web",
                        "session_id": "manual-75",
                        "prompt": f"user-{number}",
                    },
                    root=root,
                    provider_factory=lambda _: seed_provider,
                )
            compressed = compress_context(
                {
                    "user": "alice",
                    "source": "web",
                    "session_id": "manual-75",
                    "memory_extraction_policy": "queue",
                },
                root=root,
                provider_factory=lambda _: summary_provider,
            )
            archive_before = find_window(root, "alice", "web", "manual-75")
            self.assertIsNotNone(archive_before)
            _, runtime_before = load_runtime_window(
                archive_before, load_window(archive_before)
            )
            self.assertEqual(runtime_before["data"]["rounds"], 20)
            self.assertEqual(
                runtime_before["data"]["context_snapshot"]["workspace_rounds"],
                20,
            )
            continued = handle_request(
                {
                    "user": "alice",
                    "source": "web",
                    "session_id": "manual-75",
                    "prompt": "继续处理",
                },
                root=root,
                provider_factory=lambda _: continuation_provider,
            )

        self.assertTrue(compressed["compressed"])
        self.assertTrue(compressed["compression_verified"])
        self.assertEqual(compressed["context"]["rounds_removed"], 55)
        self.assertEqual(summary_provider.requests[0].max_tokens, 20_000)
        archive_path = find_window(root, "alice", "web", "manual-75")
        self.assertIsNotNone(archive_path)
        stored_archive = load_window(archive_path)
        _, stored_runtime = load_runtime_window(archive_path, stored_archive)
        self.assertEqual(stored_archive["data"]["rounds"], 76)
        self.assertEqual(stored_runtime["data"]["rounds"], 21)
        self.assertEqual(stored_runtime["data"]["context"]["round_offset"], 55)
        self.assertEqual(
            stored_runtime["data"]["context_snapshot"]["workspace_rounds"], 21
        )
        self.assertEqual(continued["text"], "round-76-reply")
        self.assertTrue(
            any(
                message.get("role") == "system"
                and "已精炼前 55 轮" in str(message.get("content") or "")
                for message in continuation_provider.requests[0].messages
            )
        )

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
            iter(
                [
                    RunEvent(type="usage", usage={"total_tokens": 2}),
                    RunEvent(type="done"),
                ]
            ),
            output="json",
            stdout=json_out,
            stderr=io.StringIO(),
            show_reasoning=True,
        )
        self.assertEqual(
            [json.loads(line)["type"] for line in json_out.getvalue().splitlines()],
            ["usage", "done"],
        )

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
            cli.emit_event_stream(
                source,
                output="text",
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                show_reasoning=False,
            )
        self.assertTrue(source.closed)


if __name__ == "__main__":
    unittest.main()
