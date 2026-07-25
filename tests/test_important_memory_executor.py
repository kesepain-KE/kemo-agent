from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.memory_temporary_important.executor import execute
from run.agent_runner import AgentOutputError, AgentRunResult
from run.memory import MemoryStore


class _Context:
    def __init__(
        self,
        root: Path,
        content: object,
        *,
        featured: object | None = None,
        reconciliations: object | None = None,
        limit: int = 2000,
    ) -> None:
        self.runner = SimpleNamespace(
            root=root,
            user="alice",
            config={"memory": {"important_memory_max_chars": limit}},
        )
        self.content = content
        self.featured = [] if featured is None else featured
        self.reconciliations = [] if reconciliations is None else reconciliations

    def run_model(self, input_data):
        return AgentRunResult(
            agent="memory_temporary_important",
            data={
                "content": self.content,
                "featured": self.featured,
                "permanent_reconciliations": self.reconciliations,
            },
            raw_text="",
            usage={"total_tokens": 1},
            model="mock",
        )


class ImportantMemoryExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "users" / "alice").mkdir(parents=True)
        self.path = self.root / "users" / "alice" / "memory_temporary_important.md"

    def _store(self) -> MemoryStore:
        return MemoryStore(
            self.root,
            "alice",
            {"memory": {"important_memory_max_chars": 2000}},
        )

    def test_nonempty_output_is_written_and_empty_output_restores_placeholder(self) -> None:
        execute(_Context(self.root, "# Profile\n\n- concise"), {"trigger": "periodic_scan"})
        self.assertEqual(self.path.read_text("utf-8").strip(), "# Profile\n\n- concise")

        execute(_Context(self.root, ""), {"trigger": "daily_consolidate"})
        self.assertTrue(self.path.is_file())
        self.assertIn("暂无可提取的重要记忆", self.path.read_text("utf-8"))

    def test_daily_output_over_limit_is_rejected_without_overwrite(self) -> None:
        self.path.write_text("original", "utf-8")
        with self.assertRaisesRegex(AgentOutputError, "超过字符上限"):
            execute(
                _Context(self.root, "123456", limit=5),
                {"trigger": "daily_consolidate"},
            )
        self.assertEqual(self.path.read_text("utf-8"), "original")

    def test_periodic_view_keeps_source_and_excludes_it_from_regular_prompt(self) -> None:
        store = self._store()
        result = store.upsert_candidates(
            [{"filename": "沟通偏好", "content": "用户偏好简洁回复。"}],
        )
        filename = result["created"][0]

        execute(
            _Context(
                self.root,
                "# 临时重要记忆\n\n- 用户偏好简洁回复。",
                featured=[{"tier": "seven_days", "filename": filename}],
            ),
            {"trigger": "periodic_scan"},
        )

        self.assertIsNotNone(store.locate_in_tier("seven_days", filename))
        self.assertEqual(store.load_important_view_sources(), {filename})
        self.assertTrue(store.important_view_is_current())
        selection = store.select_tier_for_prompt("seven_days", max_files=10)
        self.assertEqual(selection.selected_ids, ())

        store.upsert_candidates(
            [{"filename": filename, "content": "用户偏好非常简洁的回复。"}],
        )
        self.assertEqual(store.load_important_view_sources(), set())
        self.assertFalse(store.important_view_is_current())
        refreshed = store.select_tier_for_prompt("seven_days", max_files=10)
        self.assertEqual(refreshed.selected_ids, (filename,))

    def test_periodic_can_drop_full_permanent_duplicate_atomically(self) -> None:
        store = self._store()
        permanent = store.upsert_candidates(
            [{"filename": "用户偏好", "content": "用户偏好简洁回复。", "explicit": True}],
        )["created"][0]
        temporary = store.upsert_candidates(
            [{"filename": "重复偏好", "content": "用户偏好简洁回复。"}],
        )["created"][0]

        execute(
            _Context(
                self.root,
                "",
                reconciliations=[
                    {
                        "action": "drop_duplicate",
                        "tier": "seven_days",
                        "filename": temporary,
                        "permanent_filename": permanent,
                    }
                ],
            ),
            {"trigger": "periodic_scan"},
        )

        self.assertIsNone(store.locate_in_tier("seven_days", temporary))
        self.assertEqual(
            store.locate_in_tier("permanent", permanent).path.read_text("utf-8").strip(),
            "用户偏好简洁回复。",
        )

    def test_periodic_can_merge_partial_permanent_coverage(self) -> None:
        store = self._store()
        permanent = store.upsert_candidates(
            [{"filename": "用户偏好", "content": "用户偏好简洁回复。", "explicit": True}],
        )["created"][0]
        temporary = store.upsert_candidates(
            [{"filename": "偏好新增", "content": "代码修改后需要运行测试。"}],
        )["created"][0]
        merged = "用户偏好简洁回复；代码修改后需要运行测试。"

        execute(
            _Context(
                self.root,
                "",
                reconciliations=[
                    {
                        "action": "merge_permanent",
                        "tier": "seven_days",
                        "filename": temporary,
                        "permanent_filename": permanent,
                        "content": merged,
                    }
                ],
            ),
            {"trigger": "periodic_scan"},
        )

        self.assertIsNone(store.locate_in_tier("seven_days", temporary))
        self.assertEqual(
            store.locate_in_tier("permanent", permanent).path.read_text("utf-8").strip(),
            merged,
        )

    def test_invalid_or_sensitive_output_is_rejected(self) -> None:
        with self.assertRaisesRegex(AgentOutputError, "content 字符串"):
            execute(_Context(self.root, None), {"trigger": "periodic_scan"})
        with self.assertRaisesRegex(AgentOutputError, "敏感凭据"):
            execute(
                _Context(self.root, "token=abcdef12345"),
                {"trigger": "periodic_scan"},
            )


if __name__ == "__main__":
    unittest.main()
