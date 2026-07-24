from __future__ import annotations

import tempfile
import unittest
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from run.memory import (
    FILENAME_MAX_CHARS,
    MemoryError,
    MemoryStore,
    contains_sensitive_credential,
    memory_extraction_mode,
    normalize_memory_filename,
    tier_rules,
)
from run.agents import discover_agents


CONFIG = {
    "memory": {
        "temporary_injection_limits": {
            "seven_days": 3,
            "one_month": 4,
            "half_year": 3,
        },
        "tiers": {
            "seven_days": {"days": 7, "upgrade_threshold": 3, "next": "one_month"},
            "one_month": {"days": 30, "upgrade_threshold": 10, "next": "half_year"},
            "half_year": {"days": 180, "upgrade_threshold": 60, "next": None},
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

    def add(
        self,
        filename: str,
        content: str | None = None,
        *,
        explicit: bool = False,
        now: datetime | None = None,
    ) -> str:
        result = self.store.upsert_candidates(
            [
                {
                    "filename": filename,
                    "content": content or filename,
                    "explicit": explicit,
                    "action": "upsert",
                }
            ],
            source={"session_id": "s", "round": 1},
            now=now or self.start,
        )
        return (result["created"] or result["updated"])[0]

    def seed_temporary(
        self,
        tier: str,
        filename: str,
        *,
        content: str,
        weight: int,
        expires_at: datetime,
    ) -> str:
        normalized = normalize_memory_filename(filename)
        path = self.store.fragment_path(tier, normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, "utf-8")
        index = self.store.load_index(tier)
        index[normalized] = {
            "weight": weight,
            "updated_at": self.start.isoformat(),
            "last_weight_date": None,
            "expires_at": expires_at.isoformat(),
        }
        self.store.write_index(tier, index)
        return normalized

    def test_filename_contract_and_tier_rules(self) -> None:
        self.assertEqual(normalize_memory_filename("  用户喜欢川菜.md "), "用户喜欢川菜.md")
        self.assertEqual(normalize_memory_filename("a/b:c"), "abc.md")
        filename = normalize_memory_filename("超长记忆文件标题" * 5)
        self.assertLessEqual(len(Path(filename).stem), FILENAME_MAX_CHARS)
        with self.assertRaises(MemoryError):
            normalize_memory_filename("***")
        self.assertEqual(tier_rules(CONFIG)["seven_days"].next, "one_month")

    def test_extraction_mode_resolves_explicit_and_legacy_configuration(self) -> None:
        self.assertEqual(memory_extraction_mode({"memory": {}}), "compression_only")
        self.assertEqual(
            memory_extraction_mode({"memory": {"auto_extract_on_commit": True}}),
            "on_commit",
        )
        self.assertEqual(
            memory_extraction_mode({"memory": {"auto_extract_on_commit": False}}),
            "compression_only",
        )
        self.assertEqual(
            memory_extraction_mode({"memory": {"extraction_mode": "background"}}),
            "background",
        )
        with self.assertRaisesRegex(MemoryError, "extraction_mode"):
            memory_extraction_mode({"memory": {"extraction_mode": "invalid"}})

    def test_v2_index_is_atomically_upgraded_with_split_timestamps(self) -> None:
        filename = normalize_memory_filename("旧时间字段")
        fragment = self.store.fragment_path("seven_days", filename)
        fragment.parent.mkdir(parents=True, exist_ok=True)
        fragment.write_text("旧正文", "utf-8")
        os.utime(fragment, (self.start.timestamp(), self.start.timestamp()))
        used_at = self.start + timedelta(hours=2)
        self.store.path("seven_days").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "files": {
                        filename: {
                            "weight": 1,
                            "updated_at": used_at.isoformat(),
                            "last_weight_date": "2026-01-01",
                            "expires_at": (self.start + timedelta(days=7)).isoformat(),
                        }
                    },
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )

        meta = self.store.load_index("seven_days")[filename]

        self.assertEqual(meta["created_at"], self.start.isoformat())
        self.assertEqual(meta["content_updated_at"], self.start.isoformat())
        self.assertEqual(meta["last_used_at"], used_at.isoformat())
        self.assertEqual(
            json.loads(self.store.path("seven_days").read_text("utf-8"))[
                "schema_version"
            ],
            3,
        )

    def test_new_temporary_index_contains_only_lifecycle_metadata(self) -> None:
        filename = self.add("用户在成都上学", "用户在成都上学。")
        index = self.store.load_index("seven_days")
        self.assertEqual(
            set(index[filename]),
            {
                "weight",
                "created_at",
                "content_updated_at",
                "updated_at",
                "last_used_at",
                "last_weight_date",
                "tier_entered_at",
                "expires_at",
            },
        )
        self.assertEqual(index[filename]["weight"], 0)
        self.assertEqual(index[filename]["created_at"], self.start.isoformat())
        self.assertEqual(index[filename]["content_updated_at"], self.start.isoformat())
        self.assertIsNone(index[filename]["last_used_at"])
        self.assertEqual(
            index[filename]["expires_at"],
            (self.start + timedelta(days=7)).isoformat(),
        )
        raw = self.store.path("seven_days").read_text("utf-8")
        self.assertNotIn("用户在成都上学。", raw)
        self.assertEqual(self.store.fragment_path("seven_days", filename).read_text("utf-8").strip(), "用户在成都上学。")

    def test_same_filename_updates_in_place_and_explicit_moves_to_permanent(self) -> None:
        filename = self.add("用户喜欢川菜", "用户喜欢川菜。")
        result = self.store.upsert_candidates(
            [{"filename": "用户喜欢川菜", "content": "用户明确喜欢川菜。", "explicit": True, "action": "upsert"}],
            source={},
            now=self.start + timedelta(hours=1),
        )
        self.assertEqual(result["updated"], [filename])
        self.assertEqual(self.store.load_index("seven_days"), {})
        permanent = self.store.load_tier("permanent")
        self.assertEqual([(item["filename"], item["content"]) for item in permanent], [(filename, "用户明确喜欢川菜。")])
        self.assertFalse((self.store.tier_dir("permanent") / "data.json").exists())

    def test_sensitive_credentials_are_rejected(self) -> None:
        self.assertTrue(contains_sensitive_credential("API Key: sk-abcdefghijk"))
        result = self.store.upsert_candidates(
            [{"filename": "密钥", "content": "API Key: sk-abcdefghijk", "action": "upsert"}],
            source={},
            now=self.start,
        )
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(self.store.list_items(), [])

    def test_modify_and_reference_share_one_daily_weight_lock_without_sliding_expiry(self) -> None:
        filename = self.add("长期项目", "用户维护长期项目。")
        before = self.store.load_index("seven_days")[filename]["expires_at"]
        self.store.upsert_candidates(
            [{"filename": "长期项目", "content": "用户持续维护长期项目。", "action": "upsert"}],
            source={},
            now=self.start + timedelta(hours=1),
        )
        after_modify = self.store.load_index("seven_days")[filename]
        self.assertEqual(after_modify["weight"], 1)
        self.assertEqual(after_modify["expires_at"], before)
        self.assertEqual(self.store.mark_used([filename], now=self.start + timedelta(hours=2)), [])
        after_reference = self.store.load_index("seven_days")[filename]
        self.assertEqual(after_reference["weight"], 1)
        self.assertEqual(after_reference["expires_at"], before)
        self.assertEqual(
            after_reference["content_updated_at"],
            (self.start + timedelta(hours=1)).isoformat(),
        )
        self.assertEqual(
            after_reference["last_used_at"],
            (self.start + timedelta(hours=2)).isoformat(),
        )
        self.assertEqual(self.store.mark_used([filename], now=self.start + timedelta(days=1)), [filename])
        after_next_day = self.store.load_index("seven_days")[filename]
        self.assertEqual(after_next_day["weight"], 2)
        self.assertEqual(
            after_next_day["content_updated_at"],
            (self.start + timedelta(hours=1)).isoformat(),
        )

    def test_unchanged_match_weights_once_per_day(self) -> None:
        filename = self.add("稳定事实", "稳定事实。")
        self.store.upsert_candidates(
            [{"filename": "稳定事实", "content": "稳定事实。", "action": "upsert"}],
            source={},
            now=self.start + timedelta(hours=1),
        )
        self.assertEqual(self.store.load_index("seven_days")[filename]["weight"], 1)

    def test_operation_id_replay_does_not_repeat_update_or_weight(self) -> None:
        first = self.store.upsert_candidates(
            [{"filename": "批次事实", "content": "用户维护批次任务。", "action": "upsert"}],
            operation_id="memory_batch_alice_web_session_1_5",
            now=self.start,
        )
        second = self.store.upsert_candidates(
            [{"filename": "批次事实", "content": "用户维护批次任务。", "action": "upsert"}],
            operation_id="memory_batch_alice_web_session_1_5",
            now=self.start + timedelta(days=1),
        )

        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        filename = first["created"][0]
        self.assertEqual(self.store.load_index("seven_days")[filename]["weight"], 0)
        self.assertEqual(second["created"], first["created"])

    def test_due_upgrade_resets_and_due_failure_deletes(self) -> None:
        upgraded = self.seed_temporary(
            "seven_days", "反复使用", content="会反复使用的记忆。", weight=3, expires_at=self.start
        )
        deleted = self.seed_temporary(
            "seven_days", "未再使用", content="未再使用的记忆。", weight=2, expires_at=self.start
        )
        result = self.store.review_due(now=self.start)
        self.assertEqual(result, {"upgraded": [upgraded], "deleted": [deleted]})
        self.assertEqual(self.store.load_index("seven_days"), {})
        item = self.store.load_tier("one_month")[0]
        self.assertEqual(item["filename"], upgraded)
        self.assertEqual(item["weight"], 0)
        self.assertEqual(item["expires_at"], (self.start + timedelta(days=30)).isoformat())

    def test_half_year_upgrades_to_unindexed_permanent(self) -> None:
        filename = self.seed_temporary(
            "half_year", "长期稳定事实", content="长期稳定事实。", weight=60, expires_at=self.start
        )
        self.assertEqual(self.store.review_due(now=self.start)["upgraded"], [filename])
        self.assertEqual(self.store.load_index("half_year"), {})
        permanent = self.store.load_tier("permanent")
        self.assertEqual(permanent[0]["filename"], filename)
        self.assertIsNone(permanent[0]["expires_at"])
        self.assertEqual(self.store.review_due(now=self.start + timedelta(days=10000)), {"upgraded": [], "deleted": []})

    def test_filename_search_and_forget(self) -> None:
        first = self.add("川菜偏好", "用户喜欢非常辣的川菜。")
        self.add("早餐偏好", "用户喜欢清淡早餐。")
        matches = self.store.search("继续讨论川菜偏好", limit=1)
        self.assertEqual([item["filename"] for item in matches], [first])
        self.assertEqual(self.store.forget("川菜偏好"), [first])
        self.assertNotIn(first, [item["filename"] for item in self.store.list_items()])

    def test_self_improve_compact_manifest_uses_trigger_and_loose_schema(self) -> None:
        definition = discover_agents(Path(__file__).parents[1]).get("self_improve")
        self.assertEqual(
            definition.output_schema,
            {"type": "object", "additionalProperties": True},
        )
        self.assertEqual(definition.trigger_file, "trigger.md")
        self.assertIn("candidates", definition.trigger_content)
        self.assertIn("最长 20 字符", definition.trigger_content)

    def test_permanent_prompt_selection_is_unlimited(self) -> None:
        self.add("永久一", "永久记忆一。", explicit=True)
        self.add("永久二", "永久记忆二。", explicit=True)
        selection = self.store.select_tier_for_prompt("permanent", max_files=0)
        self.assertEqual(selection.injected_items, 2)
        self.assertFalse(selection.truncated)
        self.assertEqual(len(selection.source_files), 2)

    def test_temporary_prompt_reads_only_selected_markdown(self) -> None:
        high = self.seed_temporary(
            "seven_days",
            "高权重",
            content="高权重正文。",
            weight=9,
            expires_at=self.start + timedelta(days=7),
        )
        low = self.seed_temporary(
            "seven_days",
            "低权重",
            content="低权重正文。",
            weight=1,
            expires_at=self.start + timedelta(days=7),
        )
        self.store.fragment_path("seven_days", low).unlink()
        selection = self.store.select_tier_for_prompt("seven_days", max_files=1)
        self.assertEqual(selection.selected_ids, (high,))
        self.assertIn("高权重正文", selection.text)
        self.assertTrue(selection.truncated)

    def test_missing_indexed_fragment_is_skipped_and_reported(self) -> None:
        missing = self.seed_temporary(
            "seven_days",
            "缺失正文",
            content="即将被删除。",
            weight=10,
            expires_at=self.start + timedelta(days=7),
        )
        valid = self.seed_temporary(
            "seven_days",
            "有效正文",
            content="仍可注入。",
            weight=1,
            expires_at=self.start + timedelta(days=7),
        )
        self.store.fragment_path("seven_days", missing).unlink()

        with self.assertLogs("run.memory", level="WARNING") as captured:
            selection = self.store.select_tier_for_prompt("seven_days", max_files=1)

        issue = f"missing_file:seven_days/{missing}"
        self.assertEqual(selection.selected_ids, (valid,))
        self.assertEqual(selection.integrity_warnings, (issue,))
        self.assertIn("仍可注入", selection.text)
        self.assertTrue(selection.truncated)
        self.assertTrue(any(issue in line for line in captured.output))
        self.assertEqual(
            [item["filename"] for item in self.store.load_tier("seven_days")],
            [valid],
        )

    def test_atomic_markdown_failure_leaves_existing_target(self) -> None:
        filename = self.add("原子写入", "原始内容。")
        path = self.store.fragment_path("seven_days", filename)
        with patch("run.memory.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.upsert_candidates(
                    [{"filename": "原子写入", "content": "替换内容。", "action": "upsert"}],
                    source={},
                    now=self.start + timedelta(days=1),
                )
        self.assertEqual(path.read_text("utf-8").strip(), "原始内容。")

    def test_integrity_and_user_isolation(self) -> None:
        alice = self.add("用户记忆", "Alice 的记忆。")
        bob = MemoryStore(self.root, "bob", CONFIG)
        bob.upsert_candidates(
            [{"filename": "用户记忆", "content": "Bob 的记忆。", "action": "upsert"}],
            source={},
            now=self.start,
        )
        self.assertEqual([item["content"] for item in self.store.list_items()], ["Alice 的记忆。"])
        self.assertEqual([item["content"] for item in bob.list_items()], ["Bob 的记忆。"])
        self.store.fragment_path("seven_days", "孤立文件").write_text("orphan", "utf-8")
        self.assertIn("orphan_file:seven_days/孤立文件.md", self.store.integrity_issues())
        self.assertEqual(self.store.locate(alice).filename, alice)


if __name__ == "__main__":
    unittest.main()
