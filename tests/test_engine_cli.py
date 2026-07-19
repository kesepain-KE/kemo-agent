from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cli
from provider.schema import ChatResponse, Usage
from run.engine import handle_request
from run.history import find_window, load_window


class MockProvider:
    def __init__(self, seen: list[list[dict]], *, stream: bool = False) -> None:
        self.seen = seen
        self.stream = stream

    def chat(self, request):
        self.seen.append(request.messages)
        user_text = request.messages[-1]["content"]
        return ChatResponse(
            text=f"reply:{user_text}",
            usage=Usage(prompt_tokens=2, completion_tokens=3, total_tokens=5, source="mock"),
            model=request.model,
            finish_reason="stop",
        )

    def chat_stream(self, request):
        raise AssertionError("stream path not requested")


class EngineAndCLITests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "config" / "global_soul.md").write_text("GLOBAL", "utf-8")
        (root / "agents.md").write_text("AGENTS", "utf-8")
        (root / "users" / "alice" / "user_soul.md").write_text("USER", "utf-8")
        (root / "users" / "alice" / "memory_temporary_important.md").write_text("HOT", "utf-8")
        provider = {
            "type": "kemo",
            "base_url": "http://127.0.0.1:1/v1",
            "api_key_env": "TEST_KEMO_KEY",
            "model": "mock-model",
            "stream": False,
        }
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {}
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1, "provider": provider}),
            "utf-8",
        )
        return temporary, root

    def test_engine_persists_rounds_and_injects_history(self) -> None:
        _, root = self.make_root()
        seen: list[list[dict]] = []
        factory = lambda _: MockProvider(seen)
        request = {"user": "alice", "source": "cli", "session_id": "s1", "prompt": "one"}
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            first = handle_request(request, root=root, provider_factory=factory)
            request["prompt"] = "two"
            second = handle_request(request, root=root, provider_factory=factory)
        self.assertEqual(first["text"], "reply:one")
        self.assertEqual(second["text"], "reply:two")
        roles = [message["role"] for message in seen[1]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertLess(seen[1][0]["content"].index("USER"), seen[1][0]["content"].index("GLOBAL"))
        self.assertLess(seen[1][0]["content"].index("GLOBAL"), seen[1][0]["content"].index("AGENTS"))
        path = find_window(root, "alice", "cli", "s1")
        window = load_window(path)
        self.assertEqual(window["data"]["rounds"], 2)
        self.assertEqual(window["data"]["token_usage"]["total_tokens"], 10)
        self.assertEqual(len(window["text"]["messages"]), 4)

    def test_provider_failure_does_not_create_window(self) -> None:
        _, root = self.make_root()

        class FailingProvider(MockProvider):
            def chat(self, request):
                raise RuntimeError("failed")

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            with self.assertRaises(RuntimeError):
                handle_request(
                    {"user": "alice", "source": "cli", "session_id": "fail", "prompt": "one"},
                    root=root,
                    provider_factory=lambda _: FailingProvider([]),
                )
        self.assertIsNone(find_window(root, "alice", "cli", "fail"))

    def test_cli_single_stdin_json_and_interactive_use_run_contract(self) -> None:
        _, root = self.make_root()
        seen: list[list[dict]] = []
        factory = lambda _: MockProvider(seen)

        def handler(request):
            return handle_request(request, root=root, provider_factory=factory)

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            text_out = io.StringIO()
            self.assertEqual(
                cli.main(["--user", "alice", "hello"], handler=handler, stdout=text_out, stderr=io.StringIO(), root=root),
                0,
            )
            self.assertEqual(text_out.getvalue(), "reply:hello\n")

            json_out = io.StringIO()
            self.assertEqual(
                cli.main(
                    ["--user", "alice", "--stdin", "--session", "pipe", "--output", "json"],
                    handler=handler,
                    stdin=io.StringIO("piped"),
                    stdout=json_out,
                    stderr=io.StringIO(),
                    root=root,
                ),
                0,
            )
            self.assertEqual(json.loads(json_out.getvalue())["text"], "reply:piped")

            interactive_out = io.StringIO()
            self.assertEqual(
                cli.main(
                    ["--user", "alice", "--interactive", "--session", "chat"],
                    handler=handler,
                    stdin=io.StringIO("a\nb\n/exit\n"),
                    stdout=interactive_out,
                    stderr=io.StringIO(),
                    root=root,
                ),
                0,
            )
            self.assertEqual(interactive_out.getvalue(), "reply:a\nreply:b\n")
            window = load_window(find_window(root, "alice", "cli", "chat"))
            self.assertEqual(window["data"]["rounds"], 2)


if __name__ == "__main__":
    unittest.main()
