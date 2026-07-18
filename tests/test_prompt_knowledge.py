from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.knowledge_search.tool import run as knowledge_search
from provider.schema import ChatResponse, Usage
from run.engine import handle_request
from run.knowledge import build_index, select_knowledge, select_knowledge_index
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
            if tier != "permanent":
                (folder / "data.json").write_text(
                    json.dumps({"schema_version": 2, "files": {}}), "utf-8"
                )
        return temporary, root, config

    def test_prompt_order_is_fixed(self) -> None:
        _, root, config = self.make_root()
        prompt = build_system_prompt(
            root,
            "alice",
            config,
        )
        ordered = ["USER", "GLOBAL", "AGENTS"]
        offsets = [prompt.index(item) for item in ordered]
        self.assertEqual(offsets, sorted(offsets))
        self.assertNotIn("HOT", prompt)

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
        (root / "users" / "alice" / "knowledge" / "data_structure.md").write_text(
            "# Project Alpha Index\nproject alpha index entry", "utf-8"
        )
        (root / "users" / "alice" / "knowledge" / "project.md").write_text(
            "ORDINARY_BODY_MUST_NOT_BE_INJECTED", "utf-8"
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
        self.assertEqual(result["knowledge"]["documents"][0]["path"], "data_structure.md")

    def test_prompt_knowledge_selection_only_reads_index_names(self) -> None:
        _, root, _ = self.make_root()
        (root / "users" / "alice" / "knowledge" / "INDEX.MD").write_text("USER INDEX", "utf-8")
        (root / "users" / "alice" / "knowledge" / "body.md").write_text("BODY", "utf-8")
        (root / "shared_knowledge" / "目录.md").write_text("SHARED INDEX", "utf-8")
        (root / "global_knowledge" / "索引.md").write_text("GLOBAL INDEX", "utf-8")
        selection = select_knowledge_index(root, "alice", max_chars=1000)
        self.assertEqual([item.scope for item in selection.documents], ["user", "shared", "global"])
        self.assertNotIn("BODY", selection.text)

    def test_user_knowledge_switches_control_prompt_retrieval_and_tool_scopes(self) -> None:
        _, root, config = self.make_root()
        (root / "users" / "alice" / "knowledge" / "data_structure.md").write_text(
            "USER_SCOPE_TOKEN", "utf-8"
        )
        (root / "shared_knowledge" / "data_structure.md").write_text(
            "SHARED_SCOPE_TOKEN", "utf-8"
        )
        (root / "global_knowledge" / "data_structure.md").write_text(
            "GLOBAL_SCOPE_TOKEN", "utf-8"
        )
        config["knowledge"].update({"use_shared": False, "use_global": True})
        prompt = build_system_prompt(root, "alice", config)
        self.assertIn("USER_SCOPE_TOKEN", prompt)
        self.assertNotIn("SHARED_SCOPE_TOKEN", prompt)
        self.assertIn("GLOBAL_SCOPE_TOKEN", prompt)
        selected = select_knowledge(root, "alice", "scope_token", config)
        self.assertNotIn("shared", [item.scope for item in selected.documents])

        tool_result = knowledge_search(
            "scope_token",
            context={
                "root": str(root),
                "user": "alice",
                "knowledge_enabled": True,
                "knowledge_scopes": ["user", "global"],
            },
        )
        self.assertEqual(tool_result["effective_scopes"], ["global", "user"])
        self.assertNotIn("shared", [item["scope"] for item in tool_result["matches"]])

    def test_knowledge_disabled_produces_empty_prompt_retrieval_and_tool_results(self) -> None:
        _, root, config = self.make_root()
        (root / "global_knowledge" / "data_structure.md").write_text(
            "DISABLED_KNOWLEDGE_TOKEN", "utf-8"
        )
        config["knowledge"]["enabled"] = False
        prompt = build_system_prompt(root, "alice", config)
        self.assertNotIn("DISABLED_KNOWLEDGE_TOKEN", prompt)
        self.assertEqual(select_knowledge(root, "alice", "disabled_knowledge_token", config).text, "")
        result = knowledge_search(
            "disabled_knowledge_token",
            context={
                "root": str(root),
                "user": "alice",
                "knowledge_enabled": False,
                "knowledge_scopes": ["user", "shared", "global"],
            },
        )
        self.assertEqual(result["effective_scopes"], [])
        self.assertEqual(result["matches"], [])


if __name__ == "__main__":
    unittest.main()
