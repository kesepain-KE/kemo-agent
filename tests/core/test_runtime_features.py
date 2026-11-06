from __future__ import annotations

from tests.card_history_support import session_history

from provider.protocol.enums import ErrorCode

from typing import Sequence

from tests.type_assertions import require_present

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
from provider.protocol.enums import MessagePhase, MessageRole, ResponseStatus
from provider.protocol.models import (
    KemoResponse,
    MessageItem,
    ModelCapabilities,
    ProviderState,
    ReasoningItem,
    ToolCallItem,
    UnifiedError,
)
from provider.schema import ChatResponse, ProviderError, ToolCall, Usage
from run.agents import AgentRunResult
from run.extensions import describe_uploaded_asset
from run.conversation import GuidanceInput
from run.engine import (
    compress_context,
    context_status,
    handle_request,
    iter_request_events,
)
from run.tools import execution_watchdog_snapshot

from run.history import find_record as find_history_record
from run.memory import MemoryStore
from run.memory import extract_round_memory
from run.tasks import PlanStore, normalize_plan
from run.tools import (
    ConsecutiveIdenticalToolCallTracker,
    MAX_TOOL_RESULT_CHARS,
    ToolCancelledError,
    ToolDefinition,
    ToolExecutionCapacityError,
    ToolRegistry,
    ToolResultTooLargeError,
    ToolTimeoutError,
    ToolValidationError,
    apply_runtime_tool_policy,
    discover_tools,
    execute_tool,
    resolve_tool_timeout,
)


class ScriptedProvider:
    def __init__(
        self,
        responses: Sequence[ChatResponse | BaseException] | None = None,
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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
        window = require_present(session_history(root, 'alice', 'web', 'uploaded-file')).refresh()
        self.assertEqual(window.messages[0]['content'], '请读取附件')

    def make_root(
        self, *, stream: bool = False
    ) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        for path in (
            root / "plugins",
            root / "skills",
            root / "users" / "alice" / "history",
            root / "users" / "alice" / "user_skills" / "agent_create",
            root / "users" / "alice" / "user_skills" / "user_create",
        ):
            path.mkdir(parents=True, exist_ok=True)
        provider = {
            "type": "kemo",
            "base_url": "http://127.0.0.1:1/v1",
            "api_key_env": "KEMO_API_KEY",
            "model": "mock",
            "stream": stream,
        }
        (root / "config" / "global_config.yaml").write_text(
            json.dumps({"tools": {"enabled": True, "timeout": 2, "max_iterations": 4}}),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.yaml").write_text(
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
        permission: str = "write",
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
            "execution_mode": "thread",
            "permission": permission,
        }
        (directory / "SKILL.md").write_text(
            f"# {name}\n{source_value}\n\n## Tool\n\n```json\n"
            f"{json.dumps(manifest, ensure_ascii=False)}\n```\n",
            "utf-8",
        )
        prefix = "async " if async_tool else ""
        await_line = "    await __import__('asyncio').sleep(0)\n" if async_tool else ""
        (directory / "tool.py").write_text(
            f"{prefix}def run(value, *, context):\n{await_line}    return {{'ok': True, 'value': value, 'source': '{source_value}', 'user': context['user'], 'knowledge_scopes': context.get('knowledge_scopes')}}\n",
            "utf-8",
        )

    def test_only_plugins_supply_executable_tools(self) -> None:
        _, root = self.make_root()
        locations = [
            root / "plugins",
            root / "skills",
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

    def test_tool_result_limit_counts_serialized_unicode_and_escaping(self) -> None:
        # The contract is measured after JSON serialization, in characters
        # (not UTF-8 bytes), so CJK text remains one character per code point.
        unicode_value = "界" * (MAX_TOOL_RESULT_CHARS - 2)
        tool = ToolDefinition(
            name="unicode_tool",
            description="unicode",
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
            _callable=lambda: unicode_value,
        )
        self.assertEqual(
            len(json.dumps(unicode_value, ensure_ascii=False, default=str)),
            MAX_TOOL_RESULT_CHARS,
        )
        self.assertEqual(
            execute_tool(
                tool,
                {},
                context={"root": str(Path.cwd()), "user": "alice"},
                timeout=2,
            ),
            unicode_value,
        )

        escaped_value = "line\\n" * (MAX_TOOL_RESULT_CHARS // 6)
        escaped_tool = ToolDefinition(
            name="escaped_tool",
            description="escaped",
            input_schema=tool.input_schema,
            version="1.0.0",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=Path.cwd(),
            _callable=lambda: escaped_value,
        )
        self.assertGreater(
            len(json.dumps(escaped_value, ensure_ascii=False, default=str)),
            MAX_TOOL_RESULT_CHARS,
        )
        with self.assertRaises(ToolResultTooLargeError):
            execute_tool(
                escaped_tool,
                {},
                context={"root": str(Path.cwd()), "user": "alice"},
                timeout=2,
            )

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

    def test_explicit_timeout_can_add_a_bounded_manifest_cleanup_grace(self) -> None:
        tool = ToolDefinition(
            name="timeout_tool",
            description="timeout",
            input_schema={
                "type": "object",
                "properties": {"timeout": {"type": "number"}},
                "additionalProperties": False,
            },
            version="1.0.0",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=Path.cwd(),
            timeout_grace_seconds=2,
        )
        self.assertEqual(
            resolve_tool_timeout(
                tool,
                {"timeout": 10},
                default_timeout=1,
            ),
            12,
        )
        tool.timeout_grace_seconds = 31
        with self.assertRaisesRegex(ToolValidationError, "0..30"):
            resolve_tool_timeout(
                tool,
                {"timeout": 10},
                default_timeout=1,
            )

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

    def test_non_cooperative_tool_timeout_is_marked_still_running(self) -> None:
        release = threading.Event()

        def ignore_cancel(*, context: dict[str, Any]) -> None:
            del context
            release.wait(1)

        tool = ToolDefinition(
            name="non_cooperative_tool",
            description="timeout",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            version="1.0.0",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=Path.cwd(),
            _callable=ignore_cancel,
        )
        try:
            with patch("run.tools._TOOL_TIMEOUT_CLEANUP_GRACE", 0.01):
                with self.assertRaises(ToolTimeoutError) as raised:
                    execute_tool(
                        tool,
                        {},
                        context={"root": str(Path.cwd()), "user": "alice"},
                        timeout=0.01,
                    )
            self.assertTrue(raised.exception.still_running)
            self.assertFalse(raised.exception.retryable)
            with self.assertRaisesRegex(ToolExecutionCapacityError, "拒绝重复启动"):
                execute_tool(
                    tool,
                    {},
                    context={"root": str(Path.cwd()), "user": "alice"},
                    timeout=0.01,
                )
        finally:
            release.set()
            deadline = time.monotonic() + 1
            while execution_watchdog_snapshot()["abandoned"] and time.monotonic() < deadline:
                time.sleep(0.01)

    def test_manifest_tool_process_is_terminated_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plugin = Path(directory)
            marker = plugin / "marker.txt"
            (plugin / "tool.py").write_text(
                """
from pathlib import Path
import os
import time

def run(path, *, context):
    marker = Path(path)
    while True:
        with marker.open("a", encoding="utf-8") as stream:
            stream.write("x")
        time.sleep(0.01)
""".strip()
                + "\n",
                "utf-8",
            )
            tool = ToolDefinition(
                name="isolated_timeout_tool",
                description="timeout",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                version="1.0.0",
                enabled=True,
                entrypoint="tool.py:run",
                source="test",
                directory=plugin,
                execution_mode="process",
            )

            with self.assertRaises(ToolTimeoutError) as raised:
                execute_tool(
                    tool,
                    {"path": str(marker)},
                    context={"root": str(Path.cwd()), "user": "alice"},
                    timeout=0.4,
                )

            self.assertFalse(raised.exception.still_running)
            size_after_timeout = marker.stat().st_size if marker.exists() else 0
            time.sleep(0.15)
            self.assertEqual(
                marker.stat().st_size if marker.exists() else 0,
                size_after_timeout,
            )

    def test_manifest_tool_process_returns_result_from_different_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plugin = Path(directory)
            (plugin / "tool.py").write_text(
                """
import os

def run(*, context):
    return {"pid": os.getpid(), "cancelled": context["cancel_event"].is_set()}
""".strip()
                + "\n",
                "utf-8",
            )
            tool = ToolDefinition(
                name="isolated_result_tool",
                description="result",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                version="1.0.0",
                enabled=True,
                entrypoint="tool.py:run",
                source="test",
                directory=plugin,
                execution_mode="process",
            )

            result = execute_tool(
                tool,
                {},
                context={"root": str(Path.cwd()), "user": "alice"},
                timeout=5,
            )

            self.assertNotEqual(result["pid"], os.getpid())
            self.assertFalse(result["cancelled"])

    def test_manifest_tool_process_keeps_safe_stdio_without_console(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plugin = Path(directory)
            (plugin / "tool.py").write_text(
                """
import sys

def run(*, context):
    print("worker-diagnostic")
    return {"stdout_available": sys.stdout is not None}
""".strip()
                + "\n",
                "utf-8",
            )
            tool = ToolDefinition(
                name="isolated_stdio_tool",
                description="stdio",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                version="1.0.0",
                enabled=True,
                entrypoint="tool.py:run",
                source="test",
                directory=plugin,
                execution_mode="process",
            )

            result = execute_tool(
                tool,
                {},
                context={"root": str(Path.cwd()), "user": "alice"},
                timeout=5,
            )

        self.assertTrue(result["stdout_available"])

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

    def test_plugin_permission_policy_filters_tool_consequences(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "reader", "read", permission="read")
        self.write_tool(root / "plugins", "writer", "write", permission="write")
        registry = discover_tools(root, "alice")

        read_only = apply_runtime_tool_policy(
            registry,
            {"plugins": {"permissions": ["read"]}},
        )
        self.assertEqual(set(read_only.tools), {"reader"})

        child_restricted = apply_runtime_tool_policy(
            registry,
            {},
            allowed_permissions={"read"},
        )
        self.assertEqual(set(child_restricted.tools), {"reader"})

        intersection = apply_runtime_tool_policy(
            registry,
            {"plugins": {"permissions": ["read"]}},
            allowed_permissions={"write"},
        )
        self.assertEqual(intersection.tools, {})

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
        user_config = root / "users" / "alice" / "user_config.yaml"
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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
                "usage",
                "tool_call_start",
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
        self.assertNotIn("reasoning_content", assistant_tool_message)
        path = session_history(root, 'alice', 'cli', 'tool')
        window = require_present(path).refresh()
        self.assertEqual(window.messages[-1]['content'], 'final')
        self.assertEqual(window.tools[0]['status'], 'completed')
        self.assertGreaterEqual(window.tools[0]['elapsed_ms'], 0)
        self.assertEqual(window.usage['total_tokens'], 6)
        self.assertEqual(window.usage['provider_request_count'], 2)
        snapshot = context_status({"user": "alice", "source": "cli", "session_id": "tool"}, root=root)["context"]
        self.assertEqual(snapshot["last_seq"], len(window.cards))
        self.assertGreater(snapshot["history_tokens"], 0)
        self.assertEqual(window.usage['cached_prompt_tokens'], 2)
        self.assertEqual(window.integrations[0]['usage']['cache_miss_tokens'], 1)
        done = events[-1]
        self.assertEqual(require_present(done.usage)["cached_prompt_tokens"], 2)
        self.assertEqual(require_present(done.usage)["provider_request_count"], 2)
        self.assertAlmostEqual(require_present(done.usage)["cache_hit_rate"], 2 / 3, places=5)
        self.assertGreaterEqual(done.metadata["elapsed_ms"], 0)

    def test_provider_request_refresh_gates_expand_and_perception_by_user_switches(
        self,
    ) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")

        global_expand = root / "expand" / "global"
        global_expand.mkdir(parents=True)
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

        global_sense = root / "sense"
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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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

        first_system = provider.requests[0].messages[-1]["content"]
        second_system = provider.requests[1].messages[-1]["content"]
        self.assertIn("EXPAND_REQUEST_ONE", first_system)
        self.assertIn("SENSE_REQUEST_ONE", first_system)
        self.assertNotIn("EXPAND_REQUEST_TWO", first_system)
        self.assertIn("EXPAND_REQUEST_TWO", second_system)
        self.assertIn("SENSE_REQUEST_TWO", second_system)
        self.assertNotIn("EXPAND_REQUEST_ONE", second_system)
        self.assertNotIn("SENSE_REQUEST_ONE", second_system)

        user_config_path = root / "users" / "alice" / "user_config.yaml"
        user_config = json.loads(user_config_path.read_text("utf-8"))
        user_config["expand"] = {
            "global_whitelist": [],
            "shared_whitelist": [],
            "prompt_injection": False,
        }
        user_config["perception"] = {
            "global_whitelist": [],
            "prompt_injection": False,
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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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

        for request in realtime_provider.requests:
            rendered = json.dumps(request.messages, ensure_ascii=False)
            for marker in ("EXPAND_REQUEST_ONE", "EXPAND_REQUEST_TWO",
                           "SENSE_REQUEST_ONE", "SENSE_REQUEST_TWO"):
                self.assertNotIn(marker, rendered)
        self.assertEqual(
            realtime_provider.requests[0].messages[0],
            realtime_provider.requests[1].messages[0],
        )

    def test_oversized_tool_result_preserves_complete_raw_and_provider_content(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "large_result", "large")
        (root / "plugins" / "large_result" / "tool.py").write_text(
            "def run(value, *, context):\n"
            f"    return {{'ok': True, 'content': 'X' * {MAX_TOOL_RESULT_CHARS + 1}, 'value': value}}\n",
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

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "oversized-tool-result",
                        "prompt": "读取",
                        "_auto_retry_max_attempts": 1,
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        result_event = next(
            event for event in events if event.type == "tool_call_result"
        )
        self.assertEqual(result_event.metadata["status"], "completed")
        self.assertTrue(result_event.result["ok"])
        receipt = result_event.result["result"]
        self.assertEqual(receipt["content"], "X" * (MAX_TOOL_RESULT_CHARS + 1))
        self.assertEqual(len(provider.requests), 2)
        self.assertIn("X" * 100, json.dumps(provider.requests[1].messages, ensure_ascii=False))
        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        history = require_present(session_history(root, "alice", "cli", "oversized-tool-result"))
        self.assertEqual(history.tools[0]["result"]["result"], receipt)

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
                                id="rs_1",
                                content=f"原生推理内容 {index}",
                                provider_state=ProviderState(
                                    kind="opaque",
                                    data=f"opaque-state-{index}",
                                    provider="stateful-provider",
                                    model=request.model,
                                ),
                            ),
                            ToolCallItem(
                                id=f"call_tool_{index}",
                                call_id=f"callid_native_{index}",
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
                            phase=MessagePhase.FINAL_ANSWER,
                        )
                    ],
                )

        provider = NativeStateProvider()
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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

        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        second_reasoning = next(
            item
            for item in provider.requests[1].input
            if isinstance(item, ReasoningItem)
        )
        self.assertIsNone(second_reasoning.content)
        self.assertIsNotNone(second_reasoning.provider_state)
        self.assertEqual(require_present(second_reasoning.provider_state).data, "opaque-state-1")
        third_reasoning = [
            item
            for item in provider.requests[2].input
            if isinstance(item, ReasoningItem)
        ]
        self.assertEqual(
            [require_present(item.provider_state).data for item in third_reasoning],
            ["opaque-state-1", "opaque-state-2"],
        )
        self.assertEqual(len({item.id for item in third_reasoning}), 2)

        # 新一轮对话会从已提交历史中回放前一轮的两个 reasoning item。
        # Provider 允许在不同响应中重复使用 item id，桥接层必须在构造
        # 新 KemoRequest 时重新保证请求内唯一，而不是拒绝第二轮对话。
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
            [require_present(item.provider_state).data for item in historical_reasoning],
            ["opaque-state-1", "opaque-state-2"],
        )
        self.assertEqual(len({item.id for item in historical_reasoning}), 2)
        committed = require_present(session_history(root, 'alice', 'cli', 'native-provider-state')).refresh()
        self.assertNotIn('provider_state', json.dumps(committed.integrations, ensure_ascii=False))

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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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

        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        assistant_tool_message = next(
            message
            for message in provider.requests[1].messages
            if message.get("tool_calls")
        )
        self.assertNotIn("reasoning_content", assistant_tool_message)

    def test_tool_calls_finish_in_one_integration_without_count_boundary(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(responses=[
            ChatResponse(text="正在查询", tool_calls=[
                ToolCall(id="first", name="lookup", arguments={"value": "x"}),
                ToolCall(id="second", name="lookup", arguments={"value": "y"}),
            ], usage=Usage(10, 2, 12)),
            ChatResponse(text="全部完成", usage=Usage(5, 2, 7)),
        ])
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(iter_request_events({
                "user": "alice", "source": "cli", "session_id": "long-tools", "prompt": "开始",
            }, root=root, provider_factory=lambda _: provider))
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(events[-1].metadata["status"], "completed")
        self.assertEqual(events[-1].metadata["tool_calls"], 2)
        saved = require_present(session_history(root, "alice", "cli", "long-tools"))
        self.assertEqual(len(saved.integrations), 1)
        self.assertEqual([tool["status"] for tool in saved.tools], ["completed", "completed"])
        self.assertEqual(saved.messages[-1]["content"], "全部完成")

    def test_task_plan_creation_stops_only_the_current_conversation_run(self) -> None:
        _, root = self.make_root()
        side_effects: list[str] = []

        def create_plan(*, action, agent, input, wait, context):
            self.assertEqual((action, agent, wait), ("call", "task_plan", True))
            plan = PlanStore(root, "alice").create(
                normalize_plan(
                    plan_id="plan_12345678",
                    title="A 对话计划",
                    description=str(input.get("goal") or "测试计划"),
                    user="alice",
                    source=context["source"],
                    session_id=context["session_id"],
                    steps=[{
                        "step_id": "step_1",
                        "title": "后续执行",
                        "description": "必须批准后执行",
                        "critical": True,
                    }],
                    auto_accept=False,
                    reminder="当前任务计划已创建，请让用户点击批准后执行",
                )
            )
            return {
                "ok": True,
                "status": "completed",
                "agent": "task_plan",
                "data": {"action": "create"},
                "plan": plan,
            }

        def mutate(*, value, context):
            side_effects.append(f"{context['session_id']}:{value}")
            return {"ok": True, "value": value}

        dispatch = ToolDefinition(
            name="subagent_dispatch",
            description="dispatch",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "agent": {"type": "string"},
                    "input": {"type": "object"},
                    "wait": {"type": "boolean"},
                },
                "required": ["action", "agent", "input", "wait"],
                "additionalProperties": False,
            },
            version="1",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=root / "plugins" / "subagent_dispatch",
            _callable=create_plan, execution_mode="thread",
        )
        mutation = ToolDefinition(
            name="mutation",
            description="mutation",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            version="1",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=root / "plugins" / "mutation",
            _callable=mutate, execution_mode="thread",
        )
        registry = ToolRegistry({
            "subagent_dispatch": dispatch,
            "mutation": mutation,
        })
        provider_a = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="我先创建计划",
                    tool_calls=[
                        ToolCall(
                            id="create-plan",
                            name="subagent_dispatch",
                            arguments={
                                "action": "call",
                                "agent": "task_plan",
                                "input": {"action": "create", "goal": "A 任务"},
                                "wait": True,
                            },
                        ),
                        ToolCall(
                            id="must-not-run",
                            name="mutation",
                            arguments={"value": "after-plan"},
                        ),
                    ],
                    finish_reason="tool_calls",
                    usage=Usage(10, 2, 12),
                ),
                ChatResponse(text="这一响应绝不能被请求"),
            ]
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events_a = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "web",
                        "session_id": "conversation-a",
                        "run_id": "run-a",
                        "prompt": "创建任务计划",
                    },
                    root=root,
                    provider_factory=lambda _: provider_a,
                    tool_registry_factory=lambda *_: registry,
                )
            )

        self.assertEqual(len(provider_a.requests), 1)
        self.assertEqual(side_effects, [])
        terminal_a = events_a[-1]
        self.assertEqual(terminal_a.type, "done")
        self.assertEqual(terminal_a.metadata["status"], "limited")
        self.assertEqual(
            terminal_a.metadata["stop_reason"],
            "task_plan_approval_required",
        )
        self.assertTrue(terminal_a.metadata["awaiting_user_approval"])
        self.assertEqual(terminal_a.metadata["plan_id"], "plan_12345678")
        skipped = next(
            event for event in events_a
            if event.type == "tool_call_result" and event.tool_name == "mutation"
        )
        self.assertEqual(skipped.metadata["status"], "not_executed")
        self.assertFalse(skipped.result["execution"]["executed"])
        self.assertFalse(any(
            event.type == "tool_call_start" and event.tool_name == "mutation"
            for event in events_a
        ))
        window_a = require_present(session_history(root, 'alice', 'web', 'conversation-a')).refresh()
        self.assertEqual(len(window_a.integrations), 1)
        self.assertEqual(window_a.integrations[0]['stop_reason'], 'task_plan_approval_required')
        self.assertTrue(window_a.integrations[0]['awaiting_user_approval'])
        self.assertEqual(len(window_a.tools), 2)
        saved_mutation = next(tool for tool in window_a.tools if tool["name"] == "mutation")
        self.assertEqual(saved_mutation["status"], "not_executed")

        provider_b = ScriptedProvider(
            responses=[ChatResponse(text="B 对话正常继续", usage=Usage(4, 2, 6))]
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events_b = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "web",
                        "session_id": "conversation-b",
                        "run_id": "run-b",
                        "prompt": "这是 B 对话",
                    },
                    root=root,
                    provider_factory=lambda _: provider_b,
                    tool_registry_factory=lambda *_: registry,
                )
            )

        self.assertEqual(events_b[-1].type, "done")
        self.assertEqual(events_b[-1].metadata.get("stop_reason"), None)
        self.assertEqual(len(provider_b.requests), 1)
        system_b = next(
            (message.get("content") or "")
            for message in provider_b.requests[0].messages
            if message.get("role") == "system"
        )
        self.assertNotIn("A 对话计划", system_b)
        self.assertNotIn("plan_12345678", system_b)
        self.assertEqual(side_effects, [])
        self.assertEqual(len(require_present(session_history(root, 'alice', 'web', 'conversation-b')).refresh().integrations), 1)

    def test_tool_continuation_records_distinct_consistent_card_snapshots(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(responses=[
            ChatResponse(text="", tool_calls=[ToolCall("c1", "lookup", {"value": "x"})], usage=Usage(100, 1, 101)),
            ChatResponse(text="finished", usage=Usage(150, 1, 151)),
        ])
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(iter_request_events(
                {"user": "alice", "source": "cli", "session_id": "snapshots", "prompt": "go"},
                root=root, provider_factory=lambda _: provider))
        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        self.assertEqual(len(provider.requests), 2)
        history = require_present(session_history(root, "alice", "cli", "snapshots"))
        self.assertEqual([card.seq for card in history.cards], list(range(1, len(history.cards) + 1)))
        self.assertEqual(history.usage["provider_request_count"], 2)
        self.assertEqual(len(events[-1].metadata["provider_request_diagnostics"]), 2)
        self.assertEqual(events[-1].metadata["context"]["last_seq"], len(history.cards))

    def test_huge_tool_card_rejects_request_without_truncating_raw(self) -> None:
        from run.cards import CardCapacityError, CardStore, prepare_request
        _, root = self.make_root()
        store = CardStore(root, "alice")
        store.ensure_session("cli", "huge")
        payload = {"execution_id": "exec_huge", "original_call": {"name": "file", "arguments": {"path": "large.txt"}},
                   "execution": {"status": "completed", "execution_live": False},
                   "result": {"ok": True, "content": "完整原文" * 10000}}
        card = store.append("cli", "huge", "i_huge", "tool_result", payload, tool_call_id="huge")
        before = store.snapshot("cli", "huge")
        with self.assertRaises(CardCapacityError):
            prepare_request(store, "cli", "huge", system_prompt="", dynamic_tail=lambda: {},
                            tools=[], input_limit=128)
        self.assertEqual(store.snapshot("cli", "huge"), before)
        self.assertEqual(store.raw_page("cli", "huge")[0].raw_payload, card.raw_payload)

    def test_cardstream_input_capacity_respects_provider_declared_limit(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        global_config_path = root / "config" / "global_config.yaml"
        global_config = json.loads(global_config_path.read_text("utf-8"))
        global_config["agents"] = {
            "token_limit": 300_000,
            "token_compression_ratio": 0.9,
        }
        global_config_path.write_text(json.dumps(global_config), "utf-8")

        class LimitedProvider(ScriptedProvider):
            def capabilities(
                self,
                model: str,
                *,
                capabilities_url: str | None = None,
            ) -> ModelCapabilities:
                del capabilities_url
                return ModelCapabilities.model_validate({
                           'protocol_version': "2.0",
                           'model': model,
                           'provider_id': "mock",
                           'provider_model': model,
                           'limits': {"max_input_tokens": 150_000},
                       })

        provider = LimitedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="limited-call",
                            name="lookup",
                            arguments={"value": "x"},
                        )
                    ],
                    finish_reason="tool_calls",
                    usage=Usage(1_000, 1, 1_001, estimated=False),
                )
            ]
        )

        from run.extensions import context_input_budget
        budget = context_input_budget(300_000, provider.capabilities("mock"), output_reserve=8192)
        self.assertEqual(budget["effective_token_limit"], 150_000)
        self.assertEqual(budget["configured_token_limit"], 300_000)


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
            require_present(runner.input_data)["source"], {"source": "round_commit", "round": 3, "evidence_dates": {}}
        )
        self.assertEqual(
            require_present(MemoryStore(root, "alice", {}).get_entry("seven_days", "device.md"))[
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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
            if item.get("role") == "user" and ("focus on the revised target" in str(item.get("content", "")) or "revised-target.md" in str(item.get("content", "")))
        ]
        self.assertEqual(len(guidance_messages), 1)
        self.assertIn("focus on the revised target", guidance_messages[0])
        self.assertEqual(result["guidance_count"], 1)
        self.assertEqual(result["run_id"], "run_guided_test")
        applied = [event for event in events if event.type == "guidance_applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(
            applied[0].content, "focus on the revised target"
        )
        window = require_present(session_history(root, 'alice', 'cli', 'guided')).refresh()
        self.assertEqual(window.integrations[0]['guidance'], ['focus on the revised target'])

    def test_retry_preserves_consumed_guidance_without_duplicate_history(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        guidance: queue.Queue[str] = queue.Queue()
        guidance.put("continue with the revised target")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("retry-guidance-call", "lookup", {"value": "x"})],
                ),
                ProviderError("gateway disconnected", status_code=502, category="upstream_error"),
                ChatResponse(text="recovered"),
            ]
        )

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "retry-guidance",
                        "prompt": "start",
                        "run_id": "run_retry_guidance",
                        "_guidance_queue": guidance,
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        self.assertEqual(len([event for event in events if event.type == "retrying"]), 1)
        replayed = [
            message
            for message in provider.requests[-1].messages
            if message.get("role") == "user"
            and "continue with the revised target" in str(message.get("content") or "")
        ]
        self.assertEqual(len(replayed), 1)
        self.assertIn("continue with the revised target", replayed[0]["content"])
        metric = require_present(session_history(root, 'alice', 'cli', 'retry-guidance')).refresh().integrations[0]
        self.assertEqual(metric["guidance"], ["continue with the revised target"])

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

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
            if item.get("role") == "user" and ("focus on the revised target" in str(item.get("content", "")) or "revised-target.md" in str(item.get("content", "")))
        )
        self.assertIn("revised-target.md", guidance_message["content"])
        applied = next(event for event in events if event.type == "guidance_applied")
        self.assertEqual(
            applied.metadata["id"],
            "guidance_attachment_only",
        )
        window = require_present(session_history(root, 'alice', 'cli', 'guided-attachment')).refresh()
        detail = window.integrations[0]['guidance_details'][0]
        self.assertEqual(detail["uploaded_files"][0]["name"], "revised-target.md")
        self.assertNotIn("path", detail["uploaded_files"][0])
        guidance_card = next(card for card in window.cards
                             if card.raw_kind == "user_text"
                             and card.raw_level == "guidance")
        self.assertEqual(guidance_card.raw_payload["guidance_id"], "guidance_attachment_only")
        self.assertEqual(guidance_card.raw_payload["attachments"][0]["name"], "revised-target.md")

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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
        window = require_present(session_history(root, 'alice', 'cli', 'incremental-stream')).refresh()
        self.assertEqual(window.messages[-1]['content'], 'AB')

    def test_stream_tool_call_is_forwarded_only_after_complete_batch_validation(self) -> None:
        _, root = self.make_root(stream=True)
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(streams=[
            [RunEvent(type="tool_call_start", tool_call_id="callid_live", tool_name="lookup",
                      arguments={"value": "x"}), RunEvent(type="done", usage={})],
            [RunEvent(type="text_delta", content="done"), RunEvent(type="done", usage={})],
        ])
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(iter_request_events(
                {"user": "alice", "source": "cli", "session_id": "incremental-tool", "prompt": "go", "stream": True},
                root=root, provider_factory=lambda _: provider))
        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        starts = [e for e in events if e.type == "tool_call_start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0].tool_call_id, "callid_live")
        history = require_present(session_history(root, "alice", "cli", "incremental-tool"))
        self.assertEqual(history.tools[0]["status"], "completed")

    def test_stream_without_done_yields_error_and_commits_failed_round(self) -> None:
        _, root = self.make_root(stream=True)

        class EmptyStreamProvider:
            def stream(self, request):
                del request
                return iter(())

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "missing-done",
                        "prompt": "go",
                        "stream": True,
                        "_auto_retry": False,
                    },
                    root=root,
                    provider_factory=lambda _: EmptyStreamProvider(),
                )
            )
        self.assertEqual([event.type for event in events], ["error"])
        self.assertIn("完整终态", require_present(events[0].error)["message"])
        self.assertTrue(events[0].metadata["committed"])
        self.assertEqual(events[0].metadata["status"], "failed")
        window = require_present(session_history(root, 'alice', 'cli', 'missing-done')).refresh()
        self.assertEqual(len(window.integrations), 1)
        failed_metric = window.integrations[0]
        self.assertEqual(failed_metric["status"], "failed")
        self.assertEqual(failed_metric["tool_argument_retries"], 0)
        self.assertEqual(failed_metric["error"]["exception_type"], "ConnectionError")
        self.assertEqual(len(failed_metric["provider_request_diagnostics"]), 1)
        self.assertTrue(failed_metric["provider_request_diagnostics"][0]["request_id"])

    def test_provider_congestion_commits_failed_round(self) -> None:
        _, root = self.make_root()
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.conversation.provider_exchange.provider_request_slot",
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
                        "_auto_retry_max_attempts": 1,
                    },
                    root=root,
                    provider_factory=lambda _: ScriptedProvider(),
                )
            )

        self.assertEqual([event.type for event in events], ["error"])
        self.assertEqual(events[0].metadata["status"], "failed")
        self.assertEqual(events[0].error["exception_type"], "ProviderCongestionError")
        window = require_present(session_history(root, 'alice', 'cli', 'provider-busy')).refresh()
        self.assertEqual(window.integrations[0]['status'], 'failed')

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
                        request_id="req_placeholder",
                        status=ResponseStatus.REQUIRES_ACTION,
                        model="mock",
                        output=[
                            ToolCallItem(
                                id="call_invalid_item",
                                call_id="callid_invalid_call",
                                name="lookup",
                                arguments={},
                                arguments_raw='{"value":"unfinished',
                                parse_error={"message": "Unterminated string"},
                            )
                        ],
                    ),
                    KemoResponse(
                        request_id="req_placeholder",
                        status=ResponseStatus.REQUIRES_ACTION,
                        model="mock",
                        output=[
                            ToolCallItem(
                                id="call_valid_item",
                                call_id="callid_valid_call",
                                name="lookup",
                                arguments={"value": "safe"},
                            )
                        ],
                    ),
                    KemoResponse(
                        request_id="req_placeholder",
                        status=ResponseStatus.COMPLETED,
                        model="mock",
                        output=[
                            MessageItem.text(
                                MessageRole.ASSISTANT,
                                "completed",
                                phase=MessagePhase.FINAL_ANSWER,
                            )
                        ],
                    ),
                ]

            def create(self, request):
                self.requests.append(request)
                return self.responses.pop(0).model_copy(
                    update={"request_id": request.request_id}
                )

        provider = NativeProvider()
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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

        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        self.assertFalse(any(event.type == "error" for event in events))
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(len({request.request_id for request in provider.requests}), 3)
        retry_messages = kemo_request_to_chat(provider.requests[1]).messages
        self.assertIn("invalid_tool_batch", json.dumps(retry_messages, ensure_ascii=False))
        self.assertEqual(events[-1].metadata["tool_argument_retries"], 1)
        window = require_present(session_history(root, 'alice', 'cli', 'native-tool-argument-retry')).refresh()
        self.assertEqual([tool["status"] for tool in window.tools], ["invalid_arguments", "completed"])
        self.assertEqual(window.cards[1].raw_payload["arguments_raw"], '{"value":"unfinished')
        self.assertEqual(window.integrations[0]['tool_argument_retries'], 1)

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
                        tool_call_id="callid_invalid_call",
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
                        tool_call_id="callid_valid_call",
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

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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

        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        self.assertFalse(any(event.type == "error" for event in events))
        self.assertEqual(
            len([event for event in events if event.type == "tool_call_start"]), 1
        )
        self.assertEqual(len(provider.requests), 3)
        self.assertIn(
            "invalid_tool_batch",
            json.dumps(provider.requests[1].messages, ensure_ascii=False),
        )

    def test_invalid_tool_arguments_with_visible_output_is_retried(self) -> None:
        _, root = self.make_root(stream=True)
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            streams=[
                [
                    RunEvent(type="text_delta", content="visible"),
                    RunEvent(type="reasoning_delta", content="thinking"),
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="callid_invalid_call",
                        tool_name="lookup",
                        arguments={},
                        metadata={
                            "raw_arguments": "{",
                            "parse_error": {"message": "invalid"},
                            "finish_reason": "tool_calls",
                        },
                    ),
                    RunEvent(type="done", metadata={"finish_reason": "tool_calls"}),
                ],
                [
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="callid_valid_call",
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

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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

        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        self.assertFalse(any(event.type == "error" for event in events))
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(
            len([event for event in events if event.type == "tool_call_start"]),
            1,
        )
        self.assertEqual(events[-1].metadata["tool_argument_retries"], 1)
        window = require_present(session_history(root, 'alice', 'cli', 'visible-invalid-tool-arguments')).refresh()
        self.assertIn('visible', ''.join(item['content'] for item in window.messages))
        self.assertEqual(''.join(item['content'] for item in window.messages).count('visible'), 1)
        self.assertIn('thinking', ''.join(item['content'] for item in window.reasoning))

    def test_parallel_valid_and_invalid_tool_calls_retry_without_stale_card(
        self,
    ) -> None:
        _, root = self.make_root(stream=True)
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            streams=[
                [
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="discarded-valid-call",
                        tool_name="lookup",
                        arguments={"value": "discarded"},
                        metadata={"finish_reason": "tool_calls"},
                    ),
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="callid_invalid_call",
                        tool_name="lookup",
                        arguments={},
                        metadata={
                            "raw_arguments": "{",
                            "parse_error": {"message": "invalid"},
                            "finish_reason": "tool_calls",
                        },
                    ),
                    RunEvent(type="done", metadata={"finish_reason": "tool_calls"}),
                ],
                [
                    RunEvent(
                        type="tool_call_start",
                        tool_call_id="replacement-call",
                        tool_name="lookup",
                        arguments={"value": "replacement"},
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

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "parallel-invalid-tool-arguments",
                        "prompt": "lookup",
                        "stream": True,
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        starts = [event for event in events if event.type == "tool_call_start"]
        self.assertEqual(len(starts), 1)
        self.assertTrue(starts[0].tool_call_id.startswith("callid_"))
        results = [event for event in events if event.type == "tool_call_result"]
        self.assertEqual([event.metadata["status"] for event in results], ["invalid_arguments", "invalid_arguments", "completed"])
        self.assertTrue(results[-1].tool_call_id.startswith("callid_"))
        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        self.assertEqual(events[-1].metadata["tool_argument_retries"], 1)

    def test_visible_provider_output_is_preserved_without_replay(self) -> None:
        _, root = self.make_root(stream=True)
        provider = ScriptedProvider(streams=[[
            RunEvent(type="text_delta", content="已输出的正文"),
            RunEvent(type="error", error={"message": "interrupted", "category": "upstream_error", "status_code": 503}),
        ]])
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(iter_request_events(
                {"user": "alice", "source": "cli", "session_id": "visible-no-replay", "prompt": "go", "stream": True},
                root=root, provider_factory=lambda _: provider))
        self.assertEqual([e.type for e in events], ["text_delta", "error"])
        self.assertEqual(len(provider.requests), 1)
        history = require_present(session_history(root, "alice", "cli", "visible-no-replay"))
        self.assertEqual([m["content"] for m in history.messages], ["go", "已输出的正文"])
        self.assertEqual(history.integrations[0]["status"], "failed")

    def test_default_provider_retries_five_times_without_restarting_integration(self) -> None:
        _, root = self.make_root()
        provider = ScriptedProvider(
            responses=[
                ProviderError(
                    "网关不可用",
                    status_code=503,
                    category="upstream_error",
                    retryable=True,
                )
                for _ in range(6)
            ]
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "auto-retry-exhausted",
                        "prompt": "执行任务",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual([event.type for event in events].count("retrying"), 5)
        self.assertEqual([event.type for event in events].count("error"), 1)
        self.assertTrue(events[-1].metadata["committed"])
        self.assertEqual(len(provider.requests), 6)
        self.assertTrue(require_present(events[-1].error)["retry_budget_exhausted"])
        self.assertEqual(require_present(events[-1].error)["attempt_count"], 6)
        window = require_present(session_history(root, 'alice', 'cli', 'auto-retry-exhausted')).refresh()
        self.assertEqual(len(window.integrations), 1)
        self.assertEqual(len(window.integrations), 1)
        self.assertEqual(window.integrations[0]['status'], 'failed')

    def test_progress_resets_retry_budget_inside_one_run(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        responses: list[ChatResponse | BaseException] = []
        for index in range(3):
            responses.extend(
                [
                    ChatResponse(
                        text="",
                        tool_calls=[
                            ToolCall(
                                f"progress-call-{index}",
                                "lookup",
                                {"value": str(index)},
                            )
                        ],
                    ),
                    ProviderError(
                        "temporary upstream interruption",
                        status_code=503,
                        category="upstream_error",
                        retryable=True,
                    ),
                ]
            )
        responses.append(ChatResponse(text="最终完成"))
        provider = ScriptedProvider(responses=responses)

        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch("provider.retry.RetryManager.delay", return_value=0),
        ):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "retry-episode-progress",
                        "run_id": "run_retry_episode_progress",
                        "prompt": "执行长工具链",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        retries = [event for event in events if event.type == "retrying"]
        self.assertEqual(len(retries), 3)
        self.assertEqual(
            [event.metadata["retry_attempt"] for event in retries],
            [1] * 3,
        )
        self.assertTrue(all(event.metadata["replay_safe"] for event in retries))
        self.assertEqual(len(provider.requests), 7)
        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        self.assertEqual(events[-1].metadata["text"], "最终完成")
        window = require_present(session_history(root, 'alice', 'cli', 'retry-episode-progress')).refresh()
        self.assertEqual(len(window.integrations), 1)
        self.assertEqual(len(window.integrations), 1)

    def test_independent_integrations_each_receive_default_retry_budget(self) -> None:
        _, root = self.make_root()

        def failing_provider(label: str) -> ScriptedProvider:
            return ScriptedProvider(
                responses=[
                    ProviderError(
                        f"{label} unavailable",
                        status_code=503,
                        category="upstream_error",
                        retryable=True,
                    )
                    for _ in range(6)
                ]
            )

        first = failing_provider("first")
        second = failing_provider("second")
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch("provider.retry.RetryManager.delay", return_value=0),
        ):
            first_events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "fresh-retry-budget",
                        "run_id": "run_first_failure",
                        "prompt": "first",
                    },
                    root=root,
                    provider_factory=lambda _: first,
                )
            )
            second_events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "fresh-retry-budget",
                        "run_id": "run_second_failure",
                        "prompt": "second",
                    },
                    root=root,
                    provider_factory=lambda _: second,
                )
            )

        self.assertEqual(len(first.requests), 6)
        self.assertEqual(len(second.requests), 6)
        self.assertEqual([event.type for event in first_events].count("retrying"), 5)
        self.assertEqual([event.type for event in second_events].count("retrying"), 5)
        self.assertTrue(require_present(first_events[-1].error)["retry_budget_exhausted"])
        self.assertTrue(require_present(second_events[-1].error)["retry_budget_exhausted"])

    def test_tool_failure_reaches_model_without_automatic_side_effect_retry(
        self,
    ) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("tool-failure", "lookup", {"value": "x"})],
                ),
                ChatResponse(text="工具失败后已恢复"),
            ]
        )
        tool_failure = ProviderError(
            "temporary tool gateway failure",
            category="gateway_error",
            status_code=502,
            retryable=True,
        )

        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.tools.runtime.execute_tool",
                side_effect=tool_failure,
            ) as mocked_execute,
        ):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "retryable-tool-failure",
                        "prompt": "执行工具",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual([event.type for event in events].count("retrying"), 0)
        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        self.assertEqual(events[-1].metadata["text"], "工具失败后已恢复")
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(mocked_execute.call_count, 1)
        recovery_messages = provider.requests[1].messages
        self.assertTrue(
            any(
                message.get("role") == "tool"
                and "temporary tool gateway failure" in message.get("content", "")
                for message in recovery_messages
            )
        )

    def test_explicit_non_retryable_provider_error_still_uses_run_retry_budget(self) -> None:
        # R1 3.3: preserve the legacy test name, replace its obsolete semantics.
        _, root = self.make_root()
        provider = ScriptedProvider(
            responses=[
                ProviderError(
                    "authentication rejected",
                    status_code=401,
                    category="auth_error",
                    retryable=False,
                )
                for _ in range(6)
            ]
        )

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "explicit-no-retry",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual([event.type for event in events].count("retrying"), 0)
        self.assertEqual(events[-1].type, "error")
        self.assertFalse(events[-1].metadata["retryable"])
        self.assertTrue(events[-1].metadata["committed"])
        self.assertFalse(require_present(events[-1].error).get("retry_budget_exhausted", False))
        self.assertEqual(len(provider.requests), 1)
        window = require_present(session_history(root, 'alice', 'cli', 'explicit-no-retry')).refresh()
        self.assertEqual(window.integrations[0]['status'], 'failed')

    def test_deterministic_incomplete_reason_uses_full_run_retry_budget(
        self,
    ) -> None:
        _, root = self.make_root()

        class TruncatedProvider:
            def __init__(_self) -> None:
                _self.requests = []

            def create(_self, request):
                _self.requests.append(request)
                return KemoResponse.model_validate({
                           'request_id': request.request_id,
                           'status': ResponseStatus.INCOMPLETE,
                           'model': request.model,
                           'incomplete_details': {
                        "reason": "output_truncated",
                        "details": {
                            "finish_reason": "max_tokens", "category": "upstream_error",
                            "status_code": 503, "retryable": True,
                        },
                    },
                       })

        provider = TruncatedProvider()
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "deterministic-incomplete",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(len(provider.requests), 1)
        self.assertEqual([event.type for event in events].count("retrying"), 0)
        self.assertEqual(events[-1].type, "error")
        self.assertFalse(require_present(events[-1].error)["retryable"])
        self.assertFalse(require_present(events[-1].error).get("retry_budget_exhausted", False))
        self.assertEqual(require_present(events[-1].error)["stop_reason"], "output_truncated")
        self.assertTrue(events[-1].metadata["committed"])

    def test_transient_incomplete_metadata_retries_and_recovers(self) -> None:
        _, root = self.make_root()

        class EmptyThenCompleteProvider:
            def __init__(_self) -> None:
                _self.requests = []

            def create(_self, request):
                _self.requests.append(request)
                if len(_self.requests) == 1:
                    return KemoResponse.model_validate({
                               'request_id': request.request_id,
                               'status': ResponseStatus.INCOMPLETE,
                               'model': request.model,
                               'incomplete_details': {
                            "reason": "empty_output",
                            "details": {"category": "upstream_error", "status_code": 503, "retry_after_ms": 0},
                        },
                           })
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.COMPLETED,
                    model=request.model,
                    output=[
                        MessageItem.text(
                            MessageRole.ASSISTANT,
                            "recovered",
                            phase=MessagePhase.FINAL_ANSWER,
                        )
                    ],
                )

        provider = EmptyThenCompleteProvider()
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch("provider.retry.RetryManager.delay", return_value=0) as sleep,
        ):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "transient-incomplete",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(len(provider.requests), 2)
        self.assertEqual([event.type for event in events].count("retrying"), 1)
        self.assertEqual(events[-1].type, "done", events[-1].to_dict())
        self.assertEqual(len(events[-1].metadata["provider_request_diagnostics"]), 2)
        sleep.assert_called_once()

    def test_cancelled_protocol_response_never_retries_provider_error(self) -> None:
        _, root = self.make_root()

        class CancelledProvider:
            def __init__(_self) -> None:
                _self.requests = []

            def create(_self, request):
                _self.requests.append(request)
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.CANCELLED,
                    model=request.model,
                )

        provider = CancelledProvider()
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "cancelled-protocol-response",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(len(provider.requests), 1)
        self.assertFalse(any(event.type == "retrying" for event in events))
        self.assertEqual(events[-1].type, "done")
        self.assertTrue(events[-1].metadata["provider_error"]["cancelled"])
        self.assertFalse(events[-1].metadata["provider_error"]["retryable"])
        self.assertTrue(events[-1].metadata["committed"])

    def test_transport_retry_budget_exhaustion_does_not_start_outer_retry(self) -> None:
        _, root = self.make_root()
        provider = ScriptedProvider(
            responses=[
                ProviderError(
                    "chat transport exhausted",
                    category="connection_error",
                    retryable=False,
                    retry_budget_exhausted=True,
                    attempt_count=2,
                )
            ]
        )

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "transport-budget-exhausted",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(len(provider.requests), 1)
        self.assertFalse(any(event.type == "retrying" for event in events))
        self.assertEqual(events[-1].type, "error")
        self.assertTrue(require_present(events[-1].error)["retry_budget_exhausted"])
        self.assertEqual(require_present(events[-1].error)["attempt_count"], 2)
        self.assertTrue(events[-1].metadata["committed"])

    def test_unknown_incomplete_http_400_uses_full_run_retry_budget(self) -> None:
        _, root = self.make_root()

        class InvalidRequestProvider:
            def __init__(_self) -> None:
                _self.requests = []

            def create(_self, request):
                _self.requests.append(request)
                return KemoResponse.model_validate({
                           'request_id': request.request_id,
                           'status': ResponseStatus.INCOMPLETE,
                           'model': request.model,
                           'incomplete_details': {"reason": "other", "details": {
                               "provider_reason": "provider_specific_stop",
                               "category": "provider_error", "status_code": 400,
                           }},
                       })

        provider = InvalidRequestProvider()
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "incomplete-http-400",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual(len(provider.requests), 1)
        self.assertEqual([event.type for event in events].count("retrying"), 0)
        self.assertEqual(events[-1].type, "error")
        self.assertIsInstance(events[-1].error, dict)
        self.assertFalse(require_present(events[-1].error)["retryable"])
        self.assertFalse(require_present(events[-1].error).get("retry_budget_exhausted", False))
        self.assertEqual(require_present(events[-1].error)["status_code"], 400)

    def test_retry_after_is_bounded_and_used_for_outer_retry(self) -> None:
        _, root = self.make_root()
        provider = ScriptedProvider(
            responses=[
                ProviderError(
                    "gateway busy",
                    status_code=503,
                    category="upstream_error",
                    retry_after_ms=999_999,
                ),
                ChatResponse(text="recovered"),
            ]
        )

        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch("provider.retry.RetryManager.delay", return_value=0) as sleep,
        ):
            result = handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "retry-after",
                    "prompt": "go",
                },
                root=root,
                provider_factory=lambda _: provider,
            )

        self.assertEqual(result["text"], "recovered")
        sleep.assert_called_once_with(1, retry_after_ms=999_999)
        from provider.retry import RetryManager
        self.assertEqual(RetryManager().delay(1, retry_after_ms=999_999), 8.0)

    def test_retry_blocks_duplicate_side_effect_without_reusing_result(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("first-call", "lookup", {"value": "x"})],
                ),
                ProviderError("网关断开", status_code=502, category="upstream_error"),
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("retry-call", "lookup", {"value": "x"})],
                ),
                ChatResponse(text="已完成"),
            ]
        )
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch("run.tools.runtime.execute_tool", wraps=execute_tool) as mocked_execute,
        ):
            result = handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "auto-retry-tool-dedup",
                    "prompt": "查一下",
                },
                root=root,
                provider_factory=lambda _: provider,
            )

        self.assertEqual(result["text"], "已完成")
        self.assertEqual(mocked_execute.call_count, 1)

    def test_retry_does_not_repeat_failed_tool_side_effect(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("failed-first", "lookup", {"value": "x"})],
                ),
                ProviderError("gateway disconnected", status_code=502, category="upstream_error"),
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("failed-retry", "lookup", {"value": "x"})],
                ),
                ChatResponse(text="completed after blocked retry"),
            ]
        )
        tool_failure = ProviderError("tool side effect failed", retryable=True)

        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.tools.runtime.execute_tool",
                side_effect=tool_failure,
            ) as mocked_execute,
        ):
            result = handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "retry-blocked-tool",
                    "prompt": "go",
                },
                root=root,
                provider_factory=lambda _: provider,
            )

        self.assertEqual(result["text"], "completed after blocked retry")
        self.assertEqual(mocked_execute.call_count, 1)
        calls = require_present(session_history(root, 'alice', 'cli', 'retry-blocked-tool')).refresh().tools
        self.assertEqual(
            [call["status"] for call in calls],
            ["failed", "duplicate_blocked"],
        )

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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "invalid-tool-argument-limit",
                        "prompt": "call expand",
                        "_auto_retry_max_attempts": 1,
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
        history = require_present(session_history(root, "alice", "cli", "invalid-tool-argument-limit"))
        self.assertEqual(len(history.integrations), 1)
        self.assertEqual(history.integrations[0]["status"], "failed")
        self.assertEqual(history.integrations[0]["tool_argument_retries"], 2)
        self.assertEqual([tool["status"] for tool in history.tools], ["invalid_arguments"] * 3)
        self.assertTrue(all(card.raw_payload["arguments_raw"] == "{" for card in history.cards if card.raw_kind == "tool_call"))

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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "context-unrecoverable",
                        "prompt": "go",
                        "_auto_retry_max_attempts": 1,
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )

        self.assertEqual([event.type for event in events], ["error"])
        self.assertTrue(events[0].metadata["committed"])
        self.assertEqual(
            events[0].error["category"],
            "context_length_exceeded",
        )
        window = require_present(session_history(root, 'alice', 'cli', 'context-unrecoverable')).refresh()
        self.assertEqual(len(window.integrations), 1)
        self.assertEqual(window.integrations[0]['status'], 'failed')

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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "err",
                        "prompt": "go",
                        "_auto_retry_max_attempts": 1,
                    },
                    root=root,
                    provider_factory=lambda _: error_provider,
                )
            )
        self.assertEqual(
            [event.type for event in events],
            [
                "text_delta",
                "error",
            ],
        )
        self.assertTrue(events[-1].metadata["committed"])
        self.assertEqual(events[-1].metadata["status"], "failed")
        error_path = session_history(root, 'alice', 'cli', 'err')
        error_window = require_present(error_path).refresh()
        self.assertEqual(len(error_window.integrations), 1)
        self.assertIn('partial', error_window.messages[1]['content'])
        self.assertEqual(error_window.messages[1]['content'], 'partial')
        failed_metric = error_window.integrations[0]
        self.assertEqual(failed_metric["status"], "failed")
        self.assertNotIn(
            "sensitive upstream body",
            json.dumps(error_window.integrations, ensure_ascii=False),
        )
        self.assertEqual(error_window.tools, [])

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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
        self.assertEqual([event.type for event in cancelled_events if event.type != "usage"], ["done"])
        self.assertTrue(cancelled_events[-1].metadata["committed"])
        self.assertEqual(cancelled_events[-1].metadata["status"], "cancelled")
        cancel_window = require_present(session_history(root, 'alice', 'cli', 'cancel')).refresh()
        self.assertEqual(len(cancel_window.integrations), 1)
        self.assertEqual(cancel_window.integrations[0]['status'], 'cancelled')
        self.assertEqual(cancel_window.messages[0]['content'], 'go')
        self.assertIn('partial', cancel_window.messages[1]['content'])
        self.assertEqual(cancel_window.messages[1]['content'], 'partial')

        next_provider = ScriptedProvider(
            streams=[
                [
                    RunEvent(type="text_delta", content="next"),
                    RunEvent(type="usage", usage={}),
                    RunEvent(type="done", usage={}),
                ]
            ]
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
        resumed_window = require_present(session_history(root, 'alice', 'cli', 'cancel')).refresh()
        self.assertEqual(len(resumed_window.integrations), 2)
        self.assertEqual(resumed_window.messages[-2]['content'], 'continue')

    def test_same_batch_duplicate_does_not_start_another_execution(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "web_search", "plugin")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall("1", "web_search", {"value": "x"}),
                        ToolCall("2", "web_search", {"value": "x"}),
                    ],
                    usage=Usage(),
                ),
                ChatResponse(text="done", usage=Usage()),
            ]
        )
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch("run.tools.runtime.execute_tool", wraps=execute_tool) as mocked_execute,
        ):
            handle_request(
                {"user": "alice", "source": "cli", "session_id": "dup", "prompt": "go"},
                root=root,
                provider_factory=lambda _: provider,
            )
        window = require_present(session_history(root, 'alice', 'cli', 'dup')).refresh()
        calls = window.tools
        self.assertEqual(mocked_execute.call_count, 1)
        self.assertEqual(calls[1]["status"], "duplicate_blocked")
        self.assertFalse(calls[1]["result"]["ok"])

    def test_file_live_read_is_executed_again_in_the_same_run(self) -> None:
        _, root = self.make_root()
        project_plugins = Path(__file__).resolve().parents[2] / "plugins"
        shutil.copytree(project_plugins / "file", root / "plugins" / "file")
        arguments = {"action": "list_dir", "path": str(root)}
        provider = ScriptedProvider(
            responses=[
                ChatResponse(text="", tool_calls=[ToolCall("list-before", "file", arguments)], usage=Usage()),
                    ChatResponse(text="", tool_calls=[ToolCall("list-after", "file", arguments)], usage=Usage()),
                ChatResponse(text="done", usage=Usage()),
            ]
        )
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.tools.runtime.execute_tool",
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
        window = require_present(session_history(root, 'alice', 'cli', 'file-live-read')).refresh()
        calls = window.tools
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
                ChatResponse(text="", tool_calls=[ToolCall("status-before", "expand_call", status_arguments)], usage=Usage()),
                    ChatResponse(text="", tool_calls=[ToolCall(
                            "activate",
                            "expand_call",
                            {
                                "scope": "global",
                                "module": "kemo_graph",
                                "command": "activate",
                                "params": {"base_url": "http://127.0.0.1:8000/api/v1"},
                            },
                        )], usage=Usage()),
                    ChatResponse(text="", tool_calls=[ToolCall("status-after", "expand_call", status_arguments)], usage=Usage()),
                ChatResponse(text="done", usage=Usage()),
            ]
        )
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.tools.runtime.execute_tool",
                side_effect=[
                    {"ok": True, "status": "inactive", "active": False},
                    {"ok": True, "status": "active", "active": True},
                    {"ok": True, "status": "active", "active": True},
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
        window = require_present(session_history(root, 'alice', 'cli', 'expand-live-status')).refresh()
        calls = window.tools
        self.assertEqual([call["status"] for call in calls], ["completed"] * 3)
        self.assertFalse(calls[2]["duplicate"])
        self.assertTrue(calls[2]["result"]["result"]["active"])

    def test_undeclared_expand_mutation_is_reexecuted_not_silently_replayed(self) -> None:
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
                ChatResponse(text="", tool_calls=[ToolCall("sync-first", "expand_call", sync_arguments)], usage=Usage()),
                    ChatResponse(text="", tool_calls=[ToolCall(
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
                        )], usage=Usage()),
                    ChatResponse(text="", tool_calls=[ToolCall("sync-repeat", "expand_call", sync_arguments)], usage=Usage()),
                ChatResponse(text="done", usage=Usage()),
            ]
        )
        with (
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.tools.runtime.execute_tool",
                side_effect=[
                    {"ok": True, "operation": "sync"},
                    {"ok": True, "operation": "ingest"},
                    {"ok": True, "operation": "sync"},
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
        self.assertEqual(mocked_execute.call_count, 3)
        window = require_present(session_history(root, 'alice', 'cli', 'expand-mutation-dedup')).refresh()
        calls = window.tools
        self.assertEqual(
            [call["status"] for call in calls],
            ["completed", "completed", "completed"],
        )
        self.assertFalse(calls[2]["duplicate"])

    def test_failed_duplicate_call_executes_again_and_preserves_retry_metadata(
        self,
    ) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(text="", tool_calls=[ToolCall("1", "lookup", {"value": "x"})], usage=Usage()),
                    ChatResponse(text="", tool_calls=[ToolCall("2", "lookup", {"value": "x"})], usage=Usage()),
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
            patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False),
            patch(
                "run.tools.runtime.execute_tool",
                side_effect=[failure, {"ok": True, "value": "x"}],
            ) as mocked_execute,
        ):
            handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "failed-duplicate",
                    "prompt": "go",
                    "_auto_retry_max_attempts": 1,
                },
                root=root,
                provider_factory=lambda _: provider,
            )

        self.assertEqual(mocked_execute.call_count, 2)
        window = require_present(session_history(root, 'alice', 'cli', 'failed-duplicate')).refresh()
        calls = window.tools
        self.assertFalse(calls[0]["duplicate"])
        self.assertFalse(calls[1]["duplicate"])
        self.assertEqual(calls[0]["status"], "failed")
        self.assertEqual(calls[1]["status"], "completed")
        error = calls[0]["result"]["error"]
        self.assertEqual(error["category"], "upstream_error")
        self.assertEqual(error["status_code"], 502)
        self.assertTrue(error["retryable"])
        self.assertEqual(error["attempt_count"], 2)

    def test_cancelled_tool_preserves_original_execution_identity(self) -> None:
        _, root = self.make_root(stream=True)
        self.write_tool(root / "plugins", "lookup", "plugin")
        cancel, exited = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            cancel.wait(3)
            exited.set()
            raise ToolCancelledError("cancelled")
        provider = ScriptedProvider(streams=[[
            RunEvent(type="tool_call_start", tool_call_id="callid_pending_1", tool_name="lookup", arguments={"value": "x"}),
            RunEvent(type="done", usage={}),
        ]])
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}), patch("run.tools.runtime.execute_tool", side_effect=blocked):
            iterator = iter_request_events(
                {"user": "alice", "source": "cli", "session_id": "cancel-tool", "prompt": "go", "stream": True},
                root=root, provider_factory=lambda _: provider, cancel_event=cancel)
            started = next(event for event in iterator if event.type == "tool_call_start")
            cancel.set()
            terminal = list(iterator)[-1]
            self.assertTrue(exited.wait(3))
        self.assertEqual(terminal.type, "done")
        self.assertIn(terminal.metadata["status"], {"cancelled", "partial"})
        history = require_present(session_history(root, "alice", "cli", "cancel-tool"))
        self.assertEqual([c.tool_call_id for c in history.cards if c.raw_kind == "tool_call"], ["callid_pending_1"])
        self.assertTrue(all(c.raw_payload["execution_id"] == started.metadata["execution_id"] for c in history.cards if c.raw_kind == "tool_result"))

    def test_identical_call_tracker_uses_name_and_canonical_arguments(self) -> None:
        tracker = ConsecutiveIdenticalToolCallTracker(2)
        self.assertEqual(tracker.record("lookup", {"a": 1, "b": 2}), 1)
        self.assertEqual(tracker.record("lookup", {"b": 2, "a": 1}), 2)
        count = tracker.record("lookup", {"a": 1, "b": 2})
        self.assertEqual(count, 3)
        self.assertTrue(tracker.is_blocked(count))
        self.assertEqual(tracker.record("lookup", {"a": 2, "b": 2}), 1)
        self.assertEqual(tracker.record("other", {"a": 2, "b": 2}), 1)

    def test_duplicate_side_effects_do_not_consume_real_execution_limit(
        self,
    ) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        global_path = root / "config" / "global_config.yaml"
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
        provider = ScriptedProvider(responses=require_present(responses))
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
        window = require_present(session_history(root, 'alice', 'cli', 'identical-limit')).refresh()
        calls = window.tools
        self.assertEqual(calls[0]["status"], "completed")
        self.assertTrue(all(call["status"] == "duplicate_blocked" for call in calls[1:9]))
        self.assertTrue(all(call["result"]["execution"]["executed"] is False for call in calls[1:9]))
        self.assertEqual(calls[9]["status"], "completed")

    def test_explicit_false_result_is_failure_not_reported_success(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        (root / "plugins/lookup/tool.py").write_text(
            "def run(value, *, context):\n    return {'ok': False, 'error': 'injected failure'}\n", "utf-8"
        )
        path = root / "config/global_config.yaml"
        config = json.loads(path.read_text("utf-8"))
        config["history"] = {"consecutive_tool_fail_limit": 2}
        path.write_text(json.dumps(config), "utf-8")
        provider = ScriptedProvider(responses=[
            ChatResponse(text="", tool_calls=[ToolCall(f"negative-{i}", "lookup", {"value": f"same-{i}"})])
            for i in range(3)
        ] + [ChatResponse(text="stop retrying")])
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(iter_request_events(
                {"user": "alice", "source": "cli", "session_id": "negative-result", "prompt": "go", "_auto_retry_max_attempts": 1},
                root=root, provider_factory=lambda _: provider,
            ))
        results = [event for event in events if event.type == "tool_call_result"]
        self.assertEqual([event.metadata["status"] for event in results], ["failed", "failed", "temporarily_unavailable"])
        self.assertTrue(all(event.result["ok"] is False for event in results))
        self.assertTrue(all(not event.metadata.get("duplicate", False) for event in results))
        self.assertIsNone(provider.requests[2].tools)

    def test_consecutive_failures_temporarily_remove_tool_schema(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "unstable", "plugin")
        (root / "plugins" / "unstable" / "tool.py").write_text(
            "def run(value, *, context):\n    raise RuntimeError('boom')\n",
            "utf-8",
        )
        global_path = root / "config" / "global_config.yaml"
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
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "failure-limit",
                        "prompt": "go",
                        "_auto_retry_max_attempts": 1,
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
        self.assertTrue(results[2].result["error"]["temporarily_unavailable"])
        self.assertIsNone(provider.requests[2].tools)
        self.assertEqual(events[-1].type, "done", events[-1].to_dict())







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

    def test_invalid_parallel_tool_batch_is_atomic_before_execution(self) -> None:
        _, root = self.make_root()
        for name in ("first", "second"):
            self.write_tool(root / "plugins", name, name)
            (root / "plugins" / name / "tool.py").write_text(
                "from pathlib import Path\n"
                "def run(value, *, context):\n"
                "    marker = Path(context['root']) / f'marker_{value}.txt'\n"
                "    marker.write_text(value, encoding='utf-8')\n"
                "    return {'ok': True, 'value': value}\n",
                "utf-8",
            )
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall("bad-a", "first", {"value": "one"}),
                        ToolCall("bad-b", "second", {}),
                    ],
                ),
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall("good-a", "first", {"value": "one"}),
                        ToolCall("good-b", "second", {"value": "two"}),
                    ],
                ),
                ChatResponse(text="finished"),
            ]
        )
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "invalid-parallel-atomic",
                        "prompt": "run both",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )
        assert events[-1].type == "done"
        assert events[-1].metadata["tool_argument_retries"] == 1
        assert len(provider.requests) == 3
        results = [event for event in events if event.type == "tool_call_result"]
        assert sorted(event.tool_name for event in results if event.metadata["status"] == "completed") == ["first", "second"]
        assert len([e for e in results if e.metadata["status"] == "invalid_arguments"]) == 2
        assert (root / "marker_one.txt").read_text(encoding="utf-8") == "one"
        assert (root / "marker_two.txt").read_text(encoding="utf-8") == "two"


if __name__ == "__main__":
    unittest.main()
