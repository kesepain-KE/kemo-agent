from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.adapters.compat import chat_response_to_kemo, kemo_request_to_chat
from provider.schema import ChatResponse, Usage
from run.engine import handle_request
from run.knowledge import select_knowledge_index
from run.prompt import build_system_prompt


class MockProvider:
    def __init__(self, seen: list[list[dict]]) -> None:
        self.seen = seen

    def chat(self, request):
        self.seen.append(request.messages)
        return ChatResponse(
            text="ok",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model="mock",
        )

    def chat_stream(self, request):
        raise AssertionError("stream path not requested")

    def create(self, request):
        return chat_response_to_kemo(self.chat(kemo_request_to_chat(request)), request)


class PromptKnowledgeTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path, dict]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "global_knowledge").mkdir()
        (root / "shared_knowledge").mkdir()
        (root / "users" / "alice" / "knowledge").mkdir(parents=True)
        (root / "users" / "alice" / "history").mkdir()
        (root / "users" / "alice" / "improve").mkdir()
        (root / "config" / "global_soul.md").write_text("GLOBAL", "utf-8")
        (root / "users" / "alice" / "user_soul.md").write_text("USER", "utf-8")
        (root / "agents.md").write_text("AGENTS", "utf-8")
        (root / "users" / "alice" / "memory_temporary_important.md").write_text(
            "HOT", "utf-8"
        )
        config = {
            "provider": {
                "type": "kemo",
                "base_url": "http://127.0.0.1:1/v1",
                "api_key_env": "TEST_KEMO_KEY",
                "model": "mock",
                "stream": False,
            },
            "knowledge": {},
        }
        (root / "config" / "global_config.json").write_text("{}", "utf-8")
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": config["provider"],
                    "knowledge": config["knowledge"],
                }
            ),
            "utf-8",
        )
        return temporary, root, config

    def test_prompt_order_is_fixed(self) -> None:
        _, root, config = self.make_root()
        prompt = build_system_prompt(root, "alice", config)
        ordered = ["USER", "GLOBAL", "AGENTS"]
        offsets = [prompt.index(item) for item in ordered]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn("HOT", prompt)

    def test_engine_injects_knowledge_and_reports_source(self) -> None:
        _, root, _ = self.make_root()
        (root / "users" / "alice" / "knowledge" / "data_structure.md").write_text(
            "# Project Alpha Index\nproject alpha index entry",
            "utf-8",
        )
        (root / "users" / "alice" / "knowledge" / "project.md").write_text(
            "ORDINARY_BODY_MUST_NOT_BE_INJECTED",
            "utf-8",
        )
        seen: list[list[dict]] = []
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            result = handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "knowledge",
                    "prompt": "project alpha",
                },
                root=root,
                provider_factory=lambda _: MockProvider(seen),
            )
        self.assertIn("Project Alpha Index", seen[0][0]["content"])
        self.assertNotIn("ORDINARY_BODY_MUST_NOT_BE_INJECTED", seen[0][0]["content"])
        self.assertEqual(
            result["knowledge"]["documents"][0]["path"], "data_structure.md"
        )

    def test_indexes_are_full_ordered_and_only_named_index_files(self) -> None:
        _, root, _ = self.make_root()
        large = "U" * 9000
        (root / "users" / "alice" / "knowledge" / "INDEX.MD").write_text(
            large,
            "utf-8",
        )
        (root / "users" / "alice" / "knowledge" / "body.md").write_text("BODY", "utf-8")
        (root / "shared_knowledge" / "目录.md").write_text("SHARED INDEX", "utf-8")
        (root / "global_knowledge" / "索引.md").write_text("GLOBAL INDEX", "utf-8")

        selection = select_knowledge_index(root, "alice")

        self.assertEqual(
            [item.scope for item in selection.documents],
            ["user", "shared", "global"],
        )
        self.assertIn(large, selection.text)
        self.assertNotIn("BODY", selection.text)
        self.assertEqual(selection.original_chars, selection.injected_chars)
        self.assertEqual(selection.original_items, selection.injected_items)
        self.assertFalse(selection.truncated)


if __name__ == "__main__":
    unittest.main()
