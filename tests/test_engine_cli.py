from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cli
from provider.protocol.enums import MessagePhase, MessageRole, ResponseStatus
from provider.protocol.models import (
    KemoResponse,
    Measurement,
    MessageItem,
    Usage,
    text_from_content,
)
from run.engine import handle_request
from run.attachments import describe_uploaded_asset
from run.history import find_window, load_window, runtime_window_path


class MockProvider:
    def __init__(self, seen: list, *, stream: bool = False) -> None:
        self.seen = seen
        self.stream = stream

    def create(self, request):
        self.seen.append(request)
        user_message = next(
            item
            for item in reversed(request.input)
            if isinstance(item, MessageItem) and item.role == MessageRole.USER
        )
        user_text = text_from_content(user_message.content)
        return KemoResponse(
            request_id=request.request_id,
            status=ResponseStatus.COMPLETED,
            model=request.model,
            output=[
                MessageItem.text(
                    MessageRole.ASSISTANT,
                    f"reply:{user_text}",
                    phase=MessagePhase.FINAL_ANSWER,
                )
            ],
            usage=Usage(
                input_tokens=2,
                output_tokens=3,
                total_tokens=5,
                measurement=Measurement(mode="provider", exact=True),
            ),
        )


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
            "base_url": "http://127.0.0.1:1",
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
        seen: list = []

        def factory(_):
            return MockProvider(seen)

        request = {"user": "alice", "source": "cli", "session_id": "s1", "prompt": "one"}
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            first = handle_request(request, root=root, provider_factory=factory)
            request["prompt"] = "two"
            second = handle_request(request, root=root, provider_factory=factory)
        self.assertEqual(first["text"], "reply:one")
        self.assertEqual(second["text"], "reply:two")
        roles = [str(item.role) for item in seen[1].input if isinstance(item, MessageItem)]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertLess(seen[1].system_prompt.index("USER"), seen[1].system_prompt.index("GLOBAL"))
        self.assertLess(seen[1].system_prompt.index("GLOBAL"), seen[1].system_prompt.index("AGENTS"))
        path = find_window(root, "alice", "cli", "s1")
        window = load_window(path)
        self.assertEqual(window["data"]["rounds"], 2)
        self.assertEqual(window["data"]["token_usage"]["total_tokens"], 10)
        self.assertEqual(len(window["text"]["messages"]), 4)

    def test_archive_is_unbounded_while_temp_is_bounded_and_recoverable(self) -> None:
        _, root = self.make_root()
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "agents": {
                        "max_rounds": 2,
                        "rounds_after_compression": 2,
                    },
                    "history": {"recent_full_rounds": 2},
                }
            ),
            "utf-8",
        )
        seen: list = []

        def factory(_):
            return MockProvider(seen)

        request = {
            "user": "alice",
            "source": "cli",
            "session_id": "bounded-temp",
            "prompt": "",
        }

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            for number in range(1, 5):
                request["prompt"] = f"round-{number}"
                handle_request(request, root=root, provider_factory=factory)

            archive_path = find_window(root, "alice", "cli", "bounded-temp")
            self.assertIsNotNone(archive_path)
            assert archive_path is not None
            temp_path = runtime_window_path(archive_path)
            archive = load_window(archive_path)
            temp = load_window(temp_path)

            self.assertEqual(archive["data"]["rounds"], 4)
            self.assertEqual(len(archive["text"]["messages"]), 8)
            self.assertEqual(
                [item["round"] for item in archive["data"]["round_metrics"]],
                [1, 2, 3, 4],
            )
            self.assertNotIn("context", archive["data"])
            self.assertEqual(temp["data"]["rounds"], 2)
            self.assertEqual(len(temp["text"]["messages"]), 4)
            self.assertEqual(
                [item["content"] for item in temp["text"]["messages"]],
                ["round-3", "reply:round-3", "round-4", "reply:round-4"],
            )
            self.assertEqual(temp["data"]["context"]["round_offset"], 2)

            shutil.rmtree(temp_path)
            request["prompt"] = "round-5"
            handle_request(request, root=root, provider_factory=factory)

        archive = load_window(archive_path)
        temp = load_window(temp_path)
        self.assertEqual(archive["data"]["rounds"], 5)
        self.assertEqual(len(archive["text"]["messages"]), 10)
        self.assertEqual(
            [item["round"] for item in archive["data"]["round_metrics"]],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [item["round"] for item in archive["think"]["rounds"]],
            [1, 2, 3, 4, 5],
        )
        self.assertNotIn("context", archive["data"])
        self.assertEqual(temp["data"]["rounds"], 2)
        self.assertEqual(len(temp["text"]["messages"]), 4)
        self.assertEqual(
            [item["content"] for item in temp["text"]["messages"]],
            ["round-4", "reply:round-4", "round-5", "reply:round-5"],
        )
        self.assertEqual(temp["data"]["context"]["round_offset"], 3)

    def test_provider_failure_commits_round_and_next_request_continues(self) -> None:
        _, root = self.make_root()
        seen: list = []

        class FailingProvider(MockProvider):
            def create(self, request):
                raise RuntimeError("failed")

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            with self.assertRaises(RuntimeError):
                handle_request(
                    {"user": "alice", "source": "cli", "session_id": "fail", "prompt": "one"},
                    root=root,
                    provider_factory=lambda _: FailingProvider([]),
                )
        path = find_window(root, "alice", "cli", "fail")
        self.assertIsNotNone(path)
        failed_window = load_window(path)
        self.assertEqual(failed_window["data"]["rounds"], 1)
        self.assertEqual(failed_window["text"]["messages"][0]["content"], "one")
        self.assertIn(
            "模型服务错误中断",
            failed_window["text"]["messages"][1]["content"],
        )
        failed_metric = failed_window["data"]["round_metrics"][0]
        self.assertEqual(failed_metric["status"], "failed")
        self.assertEqual(
            failed_metric["failure"]["exception_type"], "RuntimeError"
        )
        self.assertEqual(
            failed_metric["failure"]["message"], "模型服务调用未完成"
        )
        self.assertNotIn("failed", json.dumps(failed_metric["failure"]))

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            result = handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "fail",
                    "prompt": "continue",
                },
                root=root,
                provider_factory=lambda _: MockProvider(seen),
            )

        self.assertEqual(result["text"], "reply:continue")
        resumed_window = load_window(path)
        self.assertEqual(resumed_window["data"]["rounds"], 2)
        history_messages = [
            item for item in seen[0].input if isinstance(item, MessageItem)
        ]
        self.assertEqual(
            [str(item.role) for item in history_messages],
            ["user", "assistant", "user"],
        )
        self.assertIn(
            "模型服务错误中断",
            text_from_content(history_messages[1].content),
        )

    def test_provider_failure_with_attachment_only_keeps_valid_history(self) -> None:
        _, root = self.make_root()
        upload = root / "users" / "alice" / "file_upload"
        upload.mkdir(parents=True, exist_ok=True)
        note = upload / "failure-note.txt"
        note.write_text("attachment body", "utf-8")
        descriptor = describe_uploaded_asset(root, "alice", {"path": str(note)})

        class FailingProvider(MockProvider):
            def create(self, request):
                raise RuntimeError("failed")

        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            with self.assertRaises(RuntimeError):
                handle_request(
                    {
                        "user": "alice",
                        "source": "web",
                        "session_id": "attachment-fail",
                        "prompt": "",
                        "uploaded_files": [descriptor],
                    },
                    root=root,
                    provider_factory=lambda _: FailingProvider([]),
                )

        path = find_window(root, "alice", "web", "attachment-fail")
        self.assertIsNotNone(path)
        assert path is not None
        failed_window = load_window(path)
        user_item = next(
            item
            for item in failed_window["items"]["items"]
            if item.get("type") == "message" and item.get("role") == "user"
        )
        self.assertTrue(user_item["content"])
        self.assertIn(
            descriptor["path"],
            json.dumps(user_item["content"], ensure_ascii=False),
        )
        failed_attachment = user_item["metadata"]["input_attachments"][0]
        self.assertEqual(failed_attachment["asset_id"], descriptor["asset_id"])
        self.assertEqual(failed_attachment["relative_path"], "failure-note.txt")
        self.assertEqual(
            failed_window["text"]["messages"][0]["attachments"],
            [failed_attachment],
        )
        self.assertEqual(
            failed_window["data"]["round_metrics"][0]["input_attachments"],
            [failed_attachment],
        )
        self.assertNotIn("path", failed_attachment)

        seen: list = []
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            result = handle_request(
                {
                    "user": "alice",
                    "source": "web",
                    "session_id": "attachment-fail",
                    "prompt": "继续",
                },
                root=root,
                provider_factory=lambda _: MockProvider(seen),
            )

        self.assertEqual(result["text"], "reply:继续")
        history_messages = [
            item for item in seen[0].input if isinstance(item, MessageItem)
        ]
        self.assertEqual(
            [str(item.role) for item in history_messages],
            ["user", "assistant", "user"],
        )
        self.assertIn(
            descriptor["path"],
            text_from_content(history_messages[0].content),
        )

    def test_cli_single_stdin_json_and_interactive_use_run_contract(self) -> None:
        _, root = self.make_root()
        seen: list = []

        def factory(_):
            return MockProvider(seen)

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

    def test_default_cli_resolves_the_shared_web_interactive_session(self) -> None:
        _, root = self.make_root()
        received: list[dict[str, str]] = []

        def handler(request: dict[str, str]) -> str:
            received.append(request)
            return "ok"

        with (
            patch("cli.resolve_handler", return_value=handler),
            patch("cli.resolve_stream_handler", return_value=None),
        ):
            code = cli.main(
                ["--user", "alice", "--no-stream", "hello"],
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                root=root,
            )

        self.assertEqual(code, 0)
        self.assertEqual(received[0]["source"], "web")
        self.assertTrue(received[0]["session_id"].startswith("conv_"))
        self.assertEqual(
            received[0]["session_id"],
            cli.resolve_interactive_context("alice", root)["session_id"],
        )


if __name__ == "__main__":
    unittest.main()
