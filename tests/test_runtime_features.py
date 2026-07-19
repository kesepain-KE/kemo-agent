from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cli
from events import RunEvent
from provider.schema import ChatResponse, ProviderError, ToolCall, Usage
from run.engine import compress_context, context_status, handle_request, iter_request_events
from run.history import find_window, load_runtime_window, load_window
from run.tools import apply_runtime_tool_policy, discover_tools, execute_tool


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


class RuntimeFeatureTests(unittest.TestCase):
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
                "kemo_graph": {"enabled": True},
            },
        )
        self.assertEqual(set(graph_replaced.tools), {"clock", "weather"})

    def test_tavily_tool_is_exposed_only_when_api_key_is_available(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "web_search", "search")
        self.write_tool(root / "plugins", "clock", "clock")

        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}, clear=False):
            unavailable = apply_runtime_tool_policy(
                discover_tools(root, "alice"),
                {},
            )
        self.assertEqual(set(unavailable.tools), {"clock"})

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
        self.assertEqual(window["data"]["token_usage"]["cached_prompt_tokens"], 2)
        self.assertEqual(window["data"]["round_metrics"][0]["usage"]["cache_miss_tokens"], 1)
        done = events[-1]
        self.assertEqual(done.usage["cached_prompt_tokens"], 2)
        self.assertAlmostEqual(done.usage["cache_hit_rate"], 2 / 3, places=5)
        self.assertGreaterEqual(done.metadata["elapsed_ms"], 0)

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
            result = handle_request(
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
        guidance_messages = [
            item["content"]
            for item in provider.requests[1].messages
            if item.get("role") == "user" and "运行中引导" in item.get("content", "")
        ]
        self.assertEqual(len(guidance_messages), 1)
        self.assertIn("focus on the revised target", guidance_messages[0])
        self.assertEqual(result["guidance_count"], 1)
        self.assertEqual(result["run_id"], "run_guided_test")
        window = load_window(find_window(root, "alice", "cli", "guided"))
        self.assertEqual(
            window["data"]["round_metrics"][0]["guidance"],
            ["focus on the revised target"],
        )

    def test_error_and_cancel_do_not_commit(self) -> None:
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
            self.assertEqual(list(iterator), [])
        self.assertIsNone(find_window(root, "alice", "cli", "cancel"))

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

    def test_max_per_round_commits_and_returns_confirmation_signal(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        global_path = root / "config" / "global_config.json"
        config = json.loads(global_path.read_text("utf-8"))
        config["tools"]["max_per_round"] = 1
        global_path.write_text(json.dumps(config), "utf-8")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall("first", "lookup", {"value": "a"}),
                        ToolCall("second", "lookup", {"value": "b"}),
                    ],
                    usage=Usage(),
                )
            ]
        )
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "soft-limit",
                        "prompt": "go",
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                )
            )
        results = [event for event in events if event.type == "tool_call_result"]
        self.assertEqual(
            [event.metadata["status"] for event in results],
            ["completed", "deferred"],
        )
        self.assertTrue(events[-1].metadata["awaiting_tool_confirmation"])
        self.assertEqual(events[-1].metadata["tool_pause"]["limit"], 1)
        window = load_window(find_window(root, "alice", "cli", "soft-limit"))
        self.assertEqual(window["data"]["rounds"], 1)
        self.assertEqual(
            [call["status"] for call in window["tool"]["rounds"][0]["calls"]],
            ["completed", "deferred"],
        )

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
                    ChatResponse(
                        text=json.dumps(
                            {
                                "candidates": [
                                    {
                                        "action": "upsert",
                                        "filename": "压缩记忆",
                                        "content": "old rounds fact",
                                        "explicit": False,
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        ),
                        usage=Usage(1, 1, 2, source="mock"),
                    ),
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
        self.assertEqual(len(summary_provider.requests), 2)
        self.assertTrue(result["context"]["summary"]["generated"])
        window = load_window(find_window(root, "alice", "cli", "long"))
        self.assertEqual(window["data"]["rounds"], 12)
        self.assertEqual(len(window["text"]["messages"]), 24)
        self.assertTrue(
            (root / "users" / "alice" / "improve" / "seven_days" / "压缩记忆.md").is_file()
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
                ChatResponse(text=json.dumps({"candidates": []}), usage=Usage(1, 1, 2)),
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
        self.assertEqual(len(provider.requests), 7)
        archive_path = find_window(root, "alice", "cli", "retry")
        self.assertIsNotNone(archive_path)
        archive = load_window(archive_path)
        _, runtime = load_runtime_window(archive_path, archive)
        self.assertEqual(archive["data"]["rounds"], 4)
        self.assertEqual(runtime["data"]["rounds"], 4)

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
