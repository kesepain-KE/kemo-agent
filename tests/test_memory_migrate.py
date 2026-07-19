from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from run.memory import MemoryStore
from run.memory_migrate import MemoryMigrationError, migrate_user_memory


CONFIG = {
    "memory": {
        "tiers": {
            "seven_days": {"days": 7, "upgrade_threshold": 3, "next": "one_month"},
            "one_month": {"days": 30, "upgrade_threshold": 10, "next": "half_year"},
            "half_year": {"days": 180, "upgrade_threshold": 60, "next": None},
        }
    }
}


class MemoryMigrationTests(unittest.TestCase):
    def root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for tier in ("seven_days", "one_month", "half_year", "permanent"):
            directory = root / "users" / "alice" / "improve" / tier
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "data.json").write_text("[]", "utf-8")
        return temporary, root

    def test_migrates_body_index_and_unindexed_permanent(self) -> None:
        _, root = self.root()
        base = root / "users" / "alice" / "improve"
        (base / "seven_days" / "data.json").write_text(
            json.dumps(
                [
                    {
                        "content": "用户喜欢川菜。",
                        "tier_weight": 2,
                        "tier_entered_at": "2026-01-01T00:00:00+00:00",
                        "review_at": "2026-01-08T00:00:00+00:00",
                        "updated_at": "2026-01-02T00:00:00+00:00",
                        "last_weight_date": "2026-01-02",
                    }
                ],
                ensure_ascii=False,
            ),
            "utf-8",
        )
        (base / "permanent" / "data.json").write_text(
            json.dumps([{"content": "用户长期维护 kemo-agent。"}], ensure_ascii=False),
            "utf-8",
        )
        report = migrate_user_memory(
            root,
            "alice",
            now=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
        self.assertTrue(report.migrated)
        self.assertEqual(report.files, 2)
        self.assertTrue(Path(report.backup).is_dir())
        self.assertFalse((base / "permanent" / "data.json").exists())
        store = MemoryStore(root, "alice", CONFIG)
        temporary = store.load_tier("seven_days")[0]
        self.assertEqual(temporary["weight"], 2)
        self.assertEqual(temporary["expires_at"], "2026-01-08T00:00:00+00:00")
        self.assertEqual(store.load_tier("permanent")[0]["content"], "用户长期维护 kemo-agent。")
        self.assertEqual(store.integrity_issues(), [])

    def test_dry_run_does_not_change_source(self) -> None:
        _, root = self.root()
        source = root / "users" / "alice" / "improve" / "seven_days" / "data.json"
        source.write_text(json.dumps([{"content": "测试记忆"}], ensure_ascii=False), "utf-8")
        report = migrate_user_memory(root, "alice", dry_run=True)
        self.assertFalse(report.migrated)
        self.assertIsInstance(json.loads(source.read_text("utf-8")), list)

    def test_conflict_aborts_before_writes(self) -> None:
        _, root = self.root()
        base = root / "users" / "alice" / "improve"
        for tier in ("seven_days", "one_month"):
            (base / tier / "data.json").write_text(
                json.dumps([{"content": "同名记忆内容"}], ensure_ascii=False), "utf-8"
            )
        with self.assertRaisesRegex(MemoryMigrationError, "冲突"):
            migrate_user_memory(root, "alice")
        self.assertIsInstance(json.loads((base / "seven_days" / "data.json").read_text("utf-8")), list)

    def test_second_run_is_idempotent(self) -> None:
        _, root = self.root()
        first = migrate_user_memory(root, "alice")
        second = migrate_user_memory(root, "alice")
        self.assertTrue(first.migrated)
        self.assertTrue(second.already_v2)
        self.assertFalse(second.migrated)


if __name__ == "__main__":
    unittest.main()
