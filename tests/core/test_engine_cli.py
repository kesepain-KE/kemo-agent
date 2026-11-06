from __future__ import annotations

from tests.card_history_support import session_history

from tests.type_assertions import require_present

import io
import json
import os
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
from run.extensions import describe_uploaded_asset



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
                measurement=Measurement.model_validate({
                                'mode': "provider",
                                'exact': True,
                            }),
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
        (root / "users" / "alice" / "memory_temporary_important.md").write_text(
            "HOT", "utf-8"
        )
        provider = {
            "type": "kemo",
            "base_url": "http://127.0.0.1:1",
            "api_key_env": "KEMO_API_KEY",
            "model": "mock-model",
            "stream": False,
        }
        (root / "config" / "global_config.yaml").write_text(
            json.dumps({}),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.yaml").write_text(
            json.dumps({"schema_version": 1, "provider": provider}),
            "utf-8",
        )
        return temporary, root

    def test_engine_persists_rounds_and_injects_history(self) -> None:
        _, root = self.make_root()
        seen: list = []

        def factory(_):
            return MockProvider(seen)

        request = {
            "user": "alice",
            "source": "cli",
            "session_id": "s1",
            "prompt": "one",
        }
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            first = handle_request(request, root=root, provider_factory=factory)
            request["prompt"] = "two"
            second = handle_request(request, root=root, provider_factory=factory)
        self.assertEqual(first["text"], "reply:one")
        self.assertEqual(second["text"], "reply:two")
        roles = [
            str(item.role) for item in seen[1].input if isinstance(item, MessageItem)
        ]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertLess(
            seen[1].system_prompt.index("USER"), seen[1].system_prompt.index("GLOBAL")
        )
        self.assertLess(
            seen[1].system_prompt.index("GLOBAL"), seen[1].system_prompt.index("AGENTS")
        )
        path = session_history(root, 'alice', 'cli', 's1')
        window = require_present(path).refresh()
        self.assertEqual(len(window.integrations), 2)
        self.assertEqual(window.usage['total_tokens'], 10)
        self.assertEqual(len(window.messages), 4)

    def test_closed_conversation_cannot_be_silently_resumed(self) -> None:
        _, root = self.make_root()
        from run.history import close_session, queue_memory_extraction
        from run.infra import EngineError

        request = {"user": "alice", "source": "cli", "session_id": "ended", "prompt": "one"}
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            handle_request(request, root=root, provider_factory=lambda _: MockProvider([]))
        queue_memory_extraction(root, "alice", "cli", "ended")
        close_session(root, "alice", "cli", "ended")
        request["prompt"] = "must not append"
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            with self.assertRaisesRegex(EngineError, "已经结束"):
                handle_request(request, root=root, provider_factory=lambda _: MockProvider([]))
        self.assertEqual(len(require_present(session_history(root, 'alice', 'cli', 'ended')).refresh().integrations), 1)

    def test_deleted_conversation_is_rejected_before_provider_execution(self) -> None:
        _, root = self.make_root()
        from run.history import delete_session
        from run.infra import EngineError

        request = {"user": "alice", "source": "cli", "session_id": "deleted", "prompt": "one"}
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            handle_request(request, root=root, provider_factory=lambda _: MockProvider([]))
        self.assertEqual(delete_session(root, "alice", "cli", "deleted"), 1)
        invoked = []
        request["prompt"] = "must not execute"
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            with self.assertRaisesRegex(EngineError, "已经删除"):
                handle_request(
                    request, root=root,
                    provider_factory=lambda _: invoked.append(True) or MockProvider([]),
                )
        self.assertEqual(invoked, [])


    def test_provider_failure_commits_round_and_next_request_continues(self) -> None:
        _, root = self.make_root()
        seen: list = []

        class FailingProvider(MockProvider):
            def create(self, request):
                raise RuntimeError("failed")

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            with self.assertRaises(RuntimeError):
                handle_request(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "fail",
                        "prompt": "one",
                    },
                    root=root,
                    provider_factory=lambda _: FailingProvider([]),
                )
        path = session_history(root, 'alice', 'cli', 'fail')
        self.assertIsNotNone(path)
        failed_window = require_present(path).refresh()
        self.assertEqual(len(failed_window.integrations), 1)
        self.assertEqual(failed_window.messages[0]['content'], 'one')
        self.assertEqual(len(failed_window.messages), 1)
        failed_metric = failed_window.integrations[0]
        self.assertEqual(failed_metric["status"], "failed")
        self.assertEqual(failed_metric["error"]["exception_type"], "RuntimeError")
        self.assertNotIn("failed", json.dumps(failed_metric["error"]))
        self.assertTrue(failed_metric["provider_request_diagnostics"])
        self.assertEqual(
            failed_metric["provider_request_diagnostics"][0]["scope"],
            "agent_before_provider_mapping",
        )

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
        resumed_window = require_present(path).refresh()
        self.assertEqual(len(resumed_window.integrations), 2)
        history_messages = [
            item for item in seen[0].input if isinstance(item, MessageItem)
        ]
        self.assertEqual(
            [str(item.role) for item in history_messages],
            ["user", "user"],
        )
        self.assertEqual(text_from_content(history_messages[0].content), "one")

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

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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

        path = session_history(root, 'alice', 'web', 'attachment-fail')
        self.assertIsNotNone(path)
        assert path is not None
        failed_window = path.refresh()
        user_item = next((item for item in failed_window.items if item.get('type') == 'message' and item.get('role') == 'user'))
        self.assertTrue(user_item["content"])
        self.assertIn(
            descriptor["path"],
            json.dumps(user_item["content"], ensure_ascii=False),
        )
        failed_attachment = failed_window.cards[0].raw_payload["attachments"][0]
        self.assertEqual(failed_attachment["asset_id"], descriptor["asset_id"])
        self.assertEqual(failed_attachment["relative_path"], "failure-note.txt")
        self.assertEqual(failed_window.messages[0]['attachments'], [failed_attachment])
        self.assertEqual(failed_window.cards[0].raw_payload["attachments"], [failed_attachment])
        self.assertNotIn("path", failed_attachment)

        seen: list = []
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
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
            ["user", "user"],
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

        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            text_out = io.StringIO()
            self.assertEqual(
                cli.main(
                    ["--user", "alice", "hello"],
                    handler=handler,
                    stdout=text_out,
                    stderr=io.StringIO(),
                    root=root,
                ),
                0,
            )
            self.assertEqual(text_out.getvalue(), "reply:hello\n")

            json_out = io.StringIO()
            self.assertEqual(
                cli.main(
                    [
                        "--user",
                        "alice",
                        "--stdin",
                        "--session",
                        "pipe",
                        "--output",
                        "json",
                    ],
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
            window = require_present(session_history(root, 'alice', 'cli', 'chat')).refresh()
            self.assertEqual(len(window.integrations), 2)

    def test_default_cli_owns_and_retires_its_own_session(self) -> None:
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
        self.assertEqual(received[0]["source"], "cli")
        self.assertTrue(received[0]["session_id"].startswith("conv_"))
        from run.history import find_record
        self.assertEqual(
            find_record(root, "alice", "cli", received[0]["session_id"])["lifecycle"],
            "closed",
        )
        self.assertNotEqual(
            received[0]["session_id"],
            cli.resolve_interactive_context("alice", root)["session_id"],
        )

    def test_interactive_new_retires_previous_and_use_rejects_closed(self) -> None:
        _, root = self.make_root()
        seen: list = []

        def handler(request):
            return handle_request(
                request, root=root, provider_factory=lambda _: MockProvider(seen)
            )

        output = io.StringIO()
        with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
            self.assertEqual(
                cli.main(
                    ["--user", "alice", "--interactive", "--session", "first"],
                    handler=handler,
                    stdin=io.StringIO("one\n/new second\ntwo\n/exit\n"),
                    stdout=output,
                    stderr=io.StringIO(),
                    root=root,
                ),
                0,
            )
        from run.history import find_record
        self.assertEqual(find_record(root, "alice", "cli", "first")["lifecycle"], "closed")
        self.assertEqual(find_record(root, "alice", "cli", "first")["memory_status"], "queued")
        self.assertEqual(find_record(root, "alice", "cli", "second")["lifecycle"], "closed")
        handled, selected = cli._interactive_command(
            "/use first", root=root, user="alice", source="cli",
            session_id="second", stdout=output,
        )
        self.assertTrue(handled)
        self.assertEqual(selected, "second")
        self.assertIn("会话已经结束，只能查看历史", output.getvalue())


if __name__ == "__main__":
    unittest.main()
