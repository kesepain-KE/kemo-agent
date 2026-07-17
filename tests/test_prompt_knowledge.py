from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.schema import ChatResponse, Usage
from run.engine import handle_request
from run.knowledge import build_index, select_knowledge
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
        (root / "users" / "alice" / "memory_temporary_important.md").write_text("HOT", "utf-8")
        config = {
            "provider": {
                "type": "kemo",
                "base_url": "http://127.0.0.1:1/v1",
                "api_key_env": "TEST_KEMO_KEY",
                "model": "mock",
                "stream": False,
            },
            "memory": {"injection_enabled": False, "extraction_enabled": False},
            "knowledge": {
                "enabled": True,
                "max_items": 4,
                "max_chars": 1000,
                "minimum_score": 1,
            },
        }
        (root / "config" / "global_config.json").write_text(json.dumps(config), "utf-8")
        (root / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
        for tier in ("seven_days", "one_month", "half_year", "permanent"):
            folder = root / "users" / "alice" / "improve" / tier
            folder.mkdir()
            (folder / "data.json").write_text("[]", "utf-8")
        return temporary, root, config

    def test_prompt_order_is_fixed(self) -> None:
        _, root, config = self.make_root()
        prompt = build_system_prompt(
            root,
            "alice",
            config,
            memory_text="MEMORY",
            knowledge_text="KNOWLEDGE",
        )
        ordered = ["GLOBAL", "USER", "AGENTS", "HOT", "MEMORY", "KNOWLEDGE"]
        offsets = [prompt.index(item) for item in ordered]
        self.assertEqual(offsets, sorted(offsets))

    def test_index_orders_user_shared_global_and_skips_binary(self) -> None:
        _, root, _ = self.make_root()
        (root / "users" / "alice" / "knowledge" / "user.md").write_text("# User\nalpha", "utf-8")
        (root / "shared_knowledge" / "shared.md").write_text("# Shared\nalpha", "utf-8")
        (root / "global_knowledge" / "global.md").write_text("# Global\nalpha", "utf-8")
        (root / "global_knowledge" / "ignore.bin").write_bytes(b"alpha")
        documents = build_index(root, "alice")
        self.assertEqual([item.scope for item in documents], ["user", "shared", "global"])

    def test_retrieval_prefers_user_and_obeys_budget(self) -> None:
        _, root, config = self.make_root()
        (root / "users" / "alice" / "knowledge" / "python.md").write_text(
            "# Python Notes\npython asyncio user detail " * 30, "utf-8"
        )
        (root / "shared_knowledge" / "python.md").write_text(
            "# Python Notes\npython asyncio shared detail", "utf-8"
        )
        (root / "global_knowledge" / "python.md").write_text(
            "# Python Notes\npython asyncio global detail", "utf-8"
        )
        config["knowledge"]["max_chars"] = 160
        selection = select_knowledge(root, "alice", "python asyncio", config)
        self.assertTrue(selection.documents)
        self.assertEqual(selection.documents[0].scope, "user")
        self.assertLessEqual(len(selection.text), 160)

    def test_retrieval_prefers_shared_before_global_at_equal_score(self) -> None:
        _, root, config = self.make_root()
        (root / "shared_knowledge" / "guide.md").write_text(
            "# Runtime Guide\nruntime transport rules", "utf-8"
        )
        (root / "global_knowledge" / "guide.md").write_text(
            "# Runtime Guide\nruntime transport rules", "utf-8"
        )
        selection = select_knowledge(root, "alice", "runtime transport", config)
        self.assertEqual([item.scope for item in selection.documents], ["shared", "global"])

    def test_unrelated_query_does_not_inject(self) -> None:
        _, root, config = self.make_root()
        (root / "global_knowledge" / "python.md").write_text("python asyncio", "utf-8")
        config["knowledge"]["minimum_score"] = 2
        self.assertEqual(select_knowledge(root, "alice", "cooking recipe", config).text, "")

    def test_engine_injects_knowledge_and_reports_source(self) -> None:
        _, root, _ = self.make_root()
        (root / "users" / "alice" / "knowledge" / "project.md").write_text(
            "# Project Alpha\nproject alpha uses event streams", "utf-8"
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
        self.assertIn("Project Alpha", seen[0][0]["content"])
        self.assertEqual(result["knowledge"]["documents"][0]["path"], "project.md")


if __name__ == "__main__":
    unittest.main()
