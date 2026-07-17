from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cli
from events import RunEvent
from provider.schema import ChatResponse, ToolCall, Usage
from run.engine import handle_request, iter_request_events
from run.history import find_window, load_window
from run.tools import ToolError, discover_tools, execute_tool


class ScriptedProvider:
    def __init__(self, responses: list[ChatResponse] | None = None, streams: list[list[RunEvent]] | None = None) -> None:
        self.responses = list(responses or [])
        self.streams = list(streams or [])
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        return self.responses.pop(0)

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
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "provider": {
                        "type": "kemo",
                        "base_url": "http://127.0.0.1:1/v1",
                        "api_key_env": "TEST_KEMO_KEY",
                        "model": "mock",
                        "stream": stream,
                    },
                    "tools": {"enabled": True, "timeout": 2, "max_iterations": 4},
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
        return temporary, root

    def write_tool(self, base: Path, name: str, source_value: str, *, enabled: bool = True, async_tool: bool = False) -> None:
        directory = base / name
        directory.mkdir(parents=True)
        (directory / "tool.json").write_text(
            json.dumps(
                {
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
            ),
            "utf-8",
        )
        prefix = "async " if async_tool else ""
        await_line = "    await __import__('asyncio').sleep(0)\n" if async_tool else ""
        (directory / "tool.py").write_text(
            f"{prefix}def run(value, *, context):\n{await_line}    return {{'value': value, 'source': '{source_value}', 'user': context['user']}}\n",
            "utf-8",
        )

    def test_four_level_override_and_disabled_lookup(self) -> None:
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
        self.assertEqual(chosen.source, "user_create")
        self.assertEqual(len(chosen.overrides), 3)
        self.assertFalse(chosen.enabled)
        with self.assertRaises(ToolError):
            registry.get("same")

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

    def test_tool_loop_and_transaction_commit(self) -> None:
        _, root = self.make_root()
        self.write_tool(root / "plugins", "lookup", "plugin")
        provider = ScriptedProvider(
            responses=[
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="lookup", arguments={"value": "x"})],
                    finish_reason="tool_calls",
                    usage=Usage(1, 1, 2, source="mock"),
                ),
                ChatResponse(text="final", usage=Usage(2, 2, 4, source="mock"), finish_reason="stop"),
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
        self.assertEqual(window["data"]["token_usage"]["total_tokens"], 6)

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
