from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from run.memory import MemoryError, MemoryStore, contains_sensitive_credential, tier_rules


CONFIG = {
    "memory": {
        "injection_max_chars": 240,
        "injection_max_items": 2,
        "tiers": {
            "seven_days": {"days": 7, "upgrade_threshold": 3, "next": "one_month"},
            "one_month": {"days": 30, "upgrade_threshold": 10, "next": "half_year"},
            "half_year": {"days": 180, "upgrade_threshold": 60, "next": "permanent"},
            "permanent": {"days": None, "upgrade_threshold": None, "next": None},
        },
    }
}


class MemoryLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = MemoryStore(self.root, "alice", CONFIG)
        self.start = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

    def add(self, content: str, *, explicit: bool = False, now=None, keywords=None):
        result = self.store.upsert_candidates(
            [
                {
                    "content": content,
                    "type": "fact",
                    "confidence": 0.9,
                    "importance": 0.8,
                    "entities": [],
                    "keywords": keywords or [],
                    "explicit": explicit,
                    "action": "upsert",
                }
            ],
            source={"session_id": "s", "round": 1},
            now=now or self.start,
        )
        return (result["created"] or result["updated"])[0]

    def test_contract_and_old_list_migration(self) -> None:
        path = self.store.path("seven_days")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps([{"text": "用户在成都上学", "weight": 2}]), "utf-8")
        item = self.store.load_tier("seven_days", now=self.start)[0]
        required = {
            "schema_version", "id", "content", "type", "keywords", "entities",
            "source", "confidence", "importance", "status", "tier", "tier_weight",
            "tier_entered_at", "review_at", "last_weight_date", "created_at",
            "updated_at", "explicit", "version",
        }
        self.assertTrue(required <= set(item))
        self.assertEqual(item["tier_weight"], 2)
        self.assertEqual(item["review_at"], (self.start + timedelta(days=7)).isoformat())

    def test_upsert_deduplicates_and_explicit_promotes(self) -> None:
        memory_id = self.add("用户喜欢川菜", keywords=["川菜"])
        result = self.store.upsert_candidates(
            [{
                "content": "用户喜欢川菜", "type": "preference", "confidence": 1.0,
                "importance": 1.0, "entities": [], "keywords": ["川菜"],
                "explicit": True, "action": "upsert",
            }],
            source={"session_id": "s", "round": 2}, now=self.start,
        )
        self.assertEqual(result["updated"], [memory_id])
        item = self.store.list_items()[0]
        self.assertEqual(item["tier"], "permanent")
        self.assertEqual(item["tier_weight"], 0)
        self.assertIsNone(item["review_at"])

    def test_sensitive_credentials_are_rejected(self) -> None:
        self.assertTrue(contains_sensitive_credential("API Key: sk-abcdefghijk"))
        result = self.store.upsert_candidates(
            [{"content": "API Key: sk-abcdefghijk", "action": "upsert"}],
            source={}, now=self.start,
        )
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(self.store.list_items(), [])

    def test_candidates_do_not_weight_and_selected_usage_does(self) -> None:
        selected_id = self.add("用户正在开发 kemo-agent", keywords=["kemo-agent"])
        other_id = self.add("用户喜欢川菜", keywords=["川菜"])
        selection = self.store.select_for_injection("继续开发 kemo-agent")
        self.assertIn(selected_id, selection.candidate_ids)
        self.assertIn(selected_id, selection.selected_ids)
        before = {item["id"]: item["tier_weight"] for item in self.store.list_items()}
        self.assertEqual(before[selected_id], 0)
        self.assertEqual(before[other_id], 0)
        self.store.mark_used(selection.selected_ids, now=self.start)
        after = {item["id"]: item["tier_weight"] for item in self.store.list_items()}
        self.assertEqual(after[selected_id], 1)
        self.assertEqual(after[other_id], 0)

    def test_same_local_day_once_cross_day_and_unbounded(self) -> None:
        memory_id = self.add("用户维护长期项目", keywords=["项目"])
        self.assertEqual(self.store.mark_used([memory_id], now=self.start), [memory_id])
        self.assertEqual(self.store.mark_used([memory_id], now=self.start + timedelta(hours=2)), [])
        self.assertEqual(self.store.mark_used([memory_id], now=self.start + timedelta(days=1)), [memory_id])
        items = self.store.load_all()
        items[0]["tier_weight"] = 100000
        self.store._write_partition(items)
        self.store.mark_used([memory_id], now=self.start + timedelta(days=2))
        self.assertEqual(self.store.load_all()[0]["tier_weight"], 100001)

    def test_due_upgrade_resets_and_due_failure_deletes(self) -> None:
        upgrade_id = self.add("会反复使用的记忆", now=self.start)
        delete_id = self.add("未再使用的记忆", now=self.start)
        items = self.store.load_all(now=self.start)
        for item in items:
            item["review_at"] = self.start.isoformat()
            item["tier_weight"] = 3 if item["id"] == upgrade_id else 2
        self.store._write_partition(items)
        result = self.store.review_due(now=self.start)
        self.assertEqual(result["upgraded"], [upgrade_id])
        self.assertEqual(result["deleted"], [delete_id])
        item = self.store.load_all()[0]
        self.assertEqual(item["tier"], "one_month")
        self.assertEqual(item["tier_weight"], 0)
        self.assertEqual(item["review_at"], (self.start + timedelta(days=30)).isoformat())

    def test_half_year_upgrades_to_permanent_and_permanent_stays(self) -> None:
        memory_id = self.add("长期稳定事实")
        item = self.store.load_all()[0]
        item.update({"tier": "half_year", "tier_weight": 60, "review_at": self.start.isoformat()})
        self.store._write_partition([item])
        result = self.store.review_due(now=self.start)
        self.assertEqual(result["upgraded"], [memory_id])
        permanent = self.store.load_all()[0]
        self.assertEqual(permanent["tier"], "permanent")
        self.assertEqual(permanent["tier_weight"], 0)
        self.assertIsNone(permanent["review_at"])
        self.assertEqual(self.store.review_due(now=self.start + timedelta(days=10000)), {"upgraded": [], "deleted": []})

    def test_forget_and_injection_budget(self) -> None:
        first = self.add("用户喜欢非常辣的川菜", keywords=["川菜"])
        self.add("用户也喜欢清淡早餐", keywords=["早餐"])
        selection = self.store.select_for_injection("川菜 早餐", max_chars=120, max_items=1)
        self.assertLessEqual(selection.chars, 120)
        self.assertEqual(len(selection.selected_ids), 1)
        self.assertGreaterEqual(len(selection.candidate_ids), len(selection.selected_ids))
        self.assertIn("当前用户指令和当前事实优先", selection.text)
        self.assertEqual(self.store.forget(first), [first])
        self.assertNotIn(first, [item["id"] for item in self.store.list_items()])

    def test_atomic_write_failure_leaves_existing_target(self) -> None:
        path = self.store.path("seven_days")
        path.parent.mkdir(parents=True)
        original = "[]\n"
        path.write_text(original, "utf-8")
        with patch("run.memory.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.add("不会写入")
        self.assertEqual(path.read_text("utf-8"), original)

    def test_users_are_isolated(self) -> None:
        self.add("Alice 的记忆")
        bob = MemoryStore(self.root, "bob", CONFIG)
        bob.upsert_candidates([{"content": "Bob 的记忆", "action": "upsert"}], source={}, now=self.start)
        self.assertEqual([item["content"] for item in self.store.list_items()], ["Alice 的记忆"])
        self.assertEqual([item["content"] for item in bob.list_items()], ["Bob 的记忆"])


if __name__ == "__main__":
    unittest.main()
