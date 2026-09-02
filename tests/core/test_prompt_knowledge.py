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
from run.config import select_knowledge_index
from run.config import build_system_prompt
from run.config.prompt_sources import PromptSourceError


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
        for base in (
            root / "users" / "alice" / "knowledge",
            root / "shared_knowledge",
            root / "global_knowledge",
        ):
            runtime = base / "kemo-graph-storage" / "content" / "markdown"
            runtime.mkdir(parents=True)
            (runtime / "data_structure.md").write_text(
                "RUNTIME_GRAPH_INDEX_MUST_BE_IGNORED",
                "utf-8",
            )

        selection = select_knowledge_index(root, "alice")

        self.assertEqual(
            [item.scope for item in selection.documents],
            ["user", "shared", "global"],
        )
        self.assertIn(large, selection.text)
        self.assertNotIn("BODY", selection.text)
        self.assertNotIn("RUNTIME_GRAPH_INDEX_MUST_BE_IGNORED", selection.text)
        self.assertEqual(selection.original_chars, selection.injected_chars)
        self.assertEqual(selection.original_items, selection.injected_items)
        self.assertFalse(selection.truncated)

    def test_index_selection_never_follows_symlinked_sources(self) -> None:
        _, root, _ = self.make_root()
        outside = root / "outside-knowledge"
        outside.mkdir()
        (outside / "index.md").write_text("OUTSIDE_INDEX", "utf-8")
        user_base = root / "users" / "alice" / "knowledge"
        linked_file = user_base / "index.md"
        linked_dir = user_base / "linked-scope"
        try:
            linked_file.symlink_to(outside / "index.md")
            linked_dir.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("当前系统不允许测试进程创建符号链接")

        selection = select_knowledge_index(root, "alice")

        self.assertEqual(selection.documents, ())
        self.assertNotIn("OUTSIDE_INDEX", selection.text)

    def test_index_selection_rejects_windows_junction_sources(self) -> None:
        _, root, _ = self.make_root()
        outside = root / "outside-junction"
        outside.mkdir()
        (outside / "index.md").write_text("OUTSIDE_JUNCTION_INDEX", "utf-8")
        user_base = root / "users" / "alice" / "knowledge"
        is_junction = getattr(Path, "is_junction", None)
        if is_junction is None:
            self.skipTest("当前 Python 不支持 Path.is_junction")

        original = is_junction

        def fake_is_junction(path: Path) -> bool:
            return path == user_base or original(path)

        with patch.object(Path, "is_junction", fake_is_junction):
            selection = select_knowledge_index(root, "alice")

        self.assertEqual(selection.documents, ())
        self.assertNotIn("OUTSIDE_JUNCTION_INDEX", selection.text)

    def test_index_selection_skips_a_source_that_becomes_unreadable(self) -> None:
        _, root, _ = self.make_root()
        first = root / "users" / "alice" / "knowledge" / "index.md"
        second = root / "users" / "alice" / "knowledge" / "data_structure.md"
        first.write_text("FIRST", "utf-8")
        second.write_text("SECOND", "utf-8")

        def read(path: Path) -> str:
            if path == first:
                raise PromptSourceError("源文件在扫描后不可读")
            return path.read_text("utf-8")

        with patch("run.config.knowledge.read_required_text", side_effect=read):
            selection = select_knowledge_index(root, "alice")

        self.assertEqual([item.relative_path for item in selection.documents], ["data_structure.md"])
        self.assertEqual(selection.text, "[user:data_structure.md]\nSECOND")

    def test_index_selection_tolerates_enumerator_failure(self) -> None:
        _, root, _ = self.make_root()

        with patch(
            "run.config.knowledge.iter_files",
            side_effect=OSError("知识库目录在扫描期间不可访问"),
        ):
            selection = select_knowledge_index(root, "alice")

        self.assertEqual(selection.documents, ())
        self.assertEqual(selection.text, "")


if __name__ == "__main__":
    unittest.main()
