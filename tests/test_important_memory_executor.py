from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.memory_temporary_important.executor import execute
from run.agent_runner import AgentOutputError, AgentRunResult


class _Context:
    def __init__(self, root: Path, content: object, *, limit: int = 2000) -> None:
        self.runner = SimpleNamespace(
            root=root,
            user="alice",
            config={"memory": {"important_memory_max_chars": limit}},
        )
        self.content = content

    def run_model(self, input_data):
        return AgentRunResult(
            agent="memory_temporary_important",
            data={"content": self.content},
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

    def test_nonempty_output_is_written_and_empty_output_deletes(self) -> None:
        execute(_Context(self.root, "# Profile\n\n- concise"), {"trigger": "periodic_scan"})
        self.assertEqual(self.path.read_text("utf-8").strip(), "# Profile\n\n- concise")

        execute(_Context(self.root, ""), {"trigger": "daily_consolidate"})
        self.assertFalse(self.path.exists())

    def test_daily_output_over_limit_is_rejected_without_overwrite(self) -> None:
        self.path.write_text("original", "utf-8")
        with self.assertRaisesRegex(AgentOutputError, "超过字符上限"):
            execute(
                _Context(self.root, "123456", limit=5),
                {"trigger": "daily_consolidate"},
            )
        self.assertEqual(self.path.read_text("utf-8"), "original")

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
