from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.self_improve.executor import execute as execute_self_improve
from cron.review_due import scan_and_promote
from cron.scheduler import (
    MEMORY_PROMOTION_SYSTEM_KEY,
    ensure_memory_promotion_task,
)
from plugins.skill_creater.tool import run as run_skill_creater
from run.agents import AgentOutputError, AgentRunResult
from run.memory import MemoryStore, normalize_memory_filename
from run.memory.store import connection as memory_connection
from tests.support.memory_db import update_fragment_metadata
from run.memory import extract_compressed_round_memory


TIERS = {
    "seven_days": {"days": 7, "upgrade_threshold": 3, "next": "one_month"},
    "one_month": {"days": 30, "upgrade_threshold": 10, "next": "half_year"},
    "half_year": {"days": 180, "upgrade_threshold": 60, "next": None},
}
CONFIG = {"memory": {"tiers": TIERS}}


def _result(*, candidates=None, promotions=None) -> AgentRunResult:
    data = {}
    if candidates is not None:
        data["candidates"] = candidates
    if promotions is not None:
        data["promotions"] = promotions
    return AgentRunResult(
        agent="self_improve",
        data=data,
        raw_text="",
        usage={"total_tokens": 3},
        model="reasoning-model",
    )


class SelfImproveRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "users" / "alice").mkdir(parents=True)

    def _seed(
        self,
        tier: str,
        name: str,
        *,
        content: str,
        weight: int,
        expires_at: datetime,
    ) -> str:
        store = MemoryStore(self.root, "alice", CONFIG)
        filename = normalize_memory_filename(name)
        store.create_fragment(tier, filename, content, now=expires_at)
        update_fragment_metadata(
            store,
            tier,
            filename,
            weight=weight,
            expires_at=expires_at,
        )
        return filename

    def test_context_compression_passes_complete_rounds_and_persists_candidates(
        self,
    ) -> None:
        rounds = [
            {
                "round": 4,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                    {"role": "assistant", "content": "world"},
                ],
                "tools": {"calls": [{"name": "lookup", "result": {"ok": True}}]},
            }
        ]

        class Runner:
            def __init__(self):
                self.input_data = None

            def run(self, name, input_data, **kwargs):
                self.input_data = input_data
                return _result(
                    candidates=[
                        {
                            "action": "upsert",
                            "filename": "batch fact",
                            "content": "A fact from complete rounds.",
                            "explicit": False,
                        }
                    ]
                )

        runner = Runner()
        extract_compressed_round_memory(
            root=self.root,
            user="alice",
            config=CONFIG,
            rounds=rounds,
            trigger="token_limit",
            agent_runner=runner,
        )

        self.assertEqual(runner.input_data["trigger"], "context_compression")
        self.assertEqual(runner.input_data["rounds"], rounds)
        self.assertEqual(runner.input_data["source"]["trigger"], "token_limit")
        self.assertEqual(
            MemoryStore(self.root, "alice", CONFIG).load_tier("seven_days")[0][
                "content"
            ],
            "A fact from complete rounds.",
        )

    def test_due_scan_deletes_low_weight_and_dispatches_eligible_batch(self) -> None:
        now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        high = self._seed(
            "seven_days", "high", content="high content", weight=3, expires_at=now
        )
        low = self._seed(
            "seven_days", "low", content="low content", weight=2, expires_at=now
        )
        calls = []

        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, name, input_data, **kwargs):
                calls.append((name, input_data))
                return _result(
                    promotions=[
                        {
                            "from_tier": "seven_days",
                            "to_tier": "one_month",
                            "filename": high,
                            "merged_with": None,
                            "skill_created": False,
                        }
                    ]
                )

        with patch("cron.review_due.AgentRunner", Runner):
            result = scan_and_promote(
                root=self.root,
                user="alice",
                config=CONFIG,
                now=now,
            )

        self.assertEqual(result["requested"], 1)
        self.assertEqual(result["deleted"], [low])
        self.assertEqual(calls[0][0], "self_improve")
        self.assertEqual(calls[0][1]["trigger"], "memory_promotion")
        self.assertEqual(calls[0][1]["promotions"][0]["content"], "high content")
        store = MemoryStore(self.root, "alice", CONFIG)
        self.assertIsNone(store.get_entry("seven_days", high))
        self.assertIsNone(store.get_entry("seven_days", low))
        self.assertIsNotNone(store.get_entry("one_month", high))

    def test_due_scan_promotes_every_eligible_fragment_across_all_batches(
        self,
    ) -> None:
        now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        filenames = [
            self._seed(
                "seven_days",
                f"batch-{index:02d}",
                content=f"batch content {index}",
                weight=3,
                expires_at=now,
            )
            for index in range(45)
        ]
        calls: list[list[dict[str, object]]] = []

        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, name, input_data, **kwargs):
                batch = input_data["promotions"]
                calls.append(batch)
                return _result(
                    promotions=[
                        {
                            "from_tier": item["from_tier"],
                            "to_tier": item["to_tier"],
                            "filename": item["filename"],
                            "merged_with": None,
                            "skill_created": False,
                        }
                        for item in batch
                    ]
                )

        with patch("cron.review_due.AgentRunner", Runner):
            result = scan_and_promote(
                root=self.root,
                user="alice",
                config=CONFIG,
                now=now,
            )

        self.assertEqual([len(batch) for batch in calls], [20, 20, 5])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["requested"], 45)
        self.assertEqual(result["applied"], filenames)
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["total_batches"], 3)
        store = MemoryStore(self.root, "alice", CONFIG)
        self.assertEqual(store.load_tier("seven_days"), [])
        self.assertEqual(
            [item["filename"] for item in store.load_tier("one_month")],
            filenames,
        )

    def test_permanent_promotions_use_smaller_batches_without_total_cap(self) -> None:
        now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        filenames = [
            self._seed(
                "half_year",
                f"permanent-batch-{index:02d}",
                content=f"stable work memory {index}",
                weight=60,
                expires_at=now,
            )
            for index in range(18)
        ]
        calls: list[list[dict[str, object]]] = []

        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, name, input_data, **kwargs):
                batch = input_data["promotions"]
                calls.append(batch)
                return _result(
                    promotions=[
                        {
                            "from_tier": item["from_tier"],
                            "to_tier": item["to_tier"],
                            "filename": item["filename"],
                            "merged_with": None,
                            "skill_created": True,
                        }
                        for item in batch
                    ]
                )

        with patch("cron.review_due.AgentRunner", Runner):
            result = scan_and_promote(
                root=self.root,
                user="alice",
                config=CONFIG,
                now=now,
            )

        self.assertEqual([len(batch) for batch in calls], [8, 8, 2])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["applied"], filenames)
        self.assertEqual(result["pending"], [])
        store = MemoryStore(self.root, "alice", CONFIG)
        self.assertEqual(store.load_tier("half_year"), [])
        self.assertEqual(
            [item["filename"] for item in store.load_tier("permanent")],
            filenames,
        )

    def test_missing_batch_decision_remains_due_and_is_promoted_on_next_scan(
        self,
    ) -> None:
        now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        filenames = [
            self._seed(
                "seven_days",
                f"retry-{index}",
                content=f"retry content {index}",
                weight=3,
                expires_at=now,
            )
            for index in range(3)
        ]
        run_count = 0

        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, name, input_data, **kwargs):
                nonlocal run_count
                run_count += 1
                batch = input_data["promotions"]
                selected = batch[:2] if run_count == 1 else batch
                return _result(
                    promotions=[
                        {
                            "from_tier": item["from_tier"],
                            "to_tier": item["to_tier"],
                            "filename": item["filename"],
                            "merged_with": None,
                            "skill_created": False,
                        }
                        for item in selected
                    ]
                )

        with patch("cron.review_due.AgentRunner", Runner):
            first = scan_and_promote(
                root=self.root,
                user="alice",
                config=CONFIG,
                now=now,
            )
            second = scan_and_promote(
                root=self.root,
                user="alice",
                config=CONFIG,
                now=now,
            )

        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["pending"], [filenames[2]])
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["applied"], [filenames[2]])
        store = MemoryStore(self.root, "alice", CONFIG)
        self.assertEqual(store.load_tier("seven_days"), [])
        self.assertEqual(
            [item["filename"] for item in store.load_tier("one_month")],
            filenames,
        )

    def test_merged_promotion_overwrites_target_and_resets_lifecycle(self) -> None:
        now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        filename = self._seed(
            "seven_days", "same", content="source", weight=3, expires_at=now
        )
        store = MemoryStore(self.root, "alice", CONFIG)
        location = store.locate_in_tier("seven_days", filename)
        self.assertIsNotNone(location)
        store._promote_location(
            location,
            "one_month",
            now,
            merged_content="merged content",
        )

        self.assertIsNone(store.get_entry("seven_days", filename))
        target = store.get_entry("one_month", filename)
        self.assertEqual(target["weight"], 0)
        self.assertEqual(
            target["expires_at"],
            (now + timedelta(days=30)).isoformat(),
        )
        self.assertEqual(
            store.get_entry("one_month", filename)["content"],
            "merged content",
        )

    def test_due_scan_applies_semantic_merge_decision_to_existing_target(self) -> None:
        now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        source = self._seed(
            "seven_days", "source", content="source fact", weight=3, expires_at=now
        )
        target = self._seed(
            "one_month",
            "target",
            content="target fact",
            weight=7,
            expires_at=now + timedelta(days=1),
        )

        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, name, input_data, **kwargs):
                return _result(
                    promotions=[
                        {
                            "from_tier": "seven_days",
                            "to_tier": "one_month",
                            "filename": source,
                            "merged_with": target,
                            "content": "merged semantic fact",
                            "skill_created": False,
                        }
                    ]
                )

        with patch("cron.review_due.AgentRunner", Runner):
            result = scan_and_promote(
                root=self.root,
                user="alice",
                config=CONFIG,
                now=now,
            )

        store = MemoryStore(self.root, "alice", CONFIG)
        self.assertEqual(result["applied"], [source])
        self.assertIsNone(store.get_entry("seven_days", source))
        self.assertEqual(store.get_entry("one_month", target)["weight"], 0)
        self.assertEqual(
            store.get_entry("one_month", target)["content"],
            "merged semantic fact",
        )

    def test_due_scan_atomically_splits_oversized_promotion(self) -> None:
        now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        source = self._seed(
            "one_month",
            "oversized-profile",
            content="甲" * 1500,
            weight=10,
            expires_at=now,
        )
        store = MemoryStore(self.root, "alice", CONFIG)
        before = store.get_entry("one_month", source)
        with memory_connection(self.root, "alice", write=True) as database:
            source_row = database.execute(
                "SELECT id FROM memory_fragments WHERE filename_key=?",
                (source.casefold(),),
            ).fetchone()
            database.execute(
                "INSERT INTO memory_weight_events(fragment_id, evidence_date, reason, created_at) "
                "VALUES(?, ?, ?, ?)",
                (int(source_row["id"]), "2026-09-16", "test", now.isoformat()),
            )

        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, name, input_data, **kwargs):
                return _result(
                    promotions=[
                        {
                            "from_tier": "one_month",
                            "to_tier": "half_year",
                            "filename": source,
                            "memory_type": "A",
                            "merged_with": "ignored-because-split.md",
                            "split_into": [
                                {"filename": "用户身份与背景", "content": "甲" * 800},
                                {"filename": "用户审美与偏好", "content": "乙" * 700},
                            ],
                        }
                    ]
                )

        with patch("cron.review_due.AgentRunner", Runner), self.assertLogs(
            "cron.review_due", level="WARNING"
        ):
            result = scan_and_promote(
                root=self.root,
                user="alice",
                config=CONFIG,
                now=now,
            )

        self.assertEqual(result["applied"], [source])
        self.assertEqual(result["promotions"][0]["split_count"], 2)
        self.assertIsNone(store.get_entry("one_month", source))
        children = store.load_tier("half_year")
        self.assertEqual(len(children), 2)
        self.assertTrue(all(item["weight"] == 0 for item in children))
        self.assertTrue(
            all(item["expires_at"] == before["expires_at"] for item in children)
        )
        self.assertTrue(
            all(
                item["tier_entered_at"] == before["tier_entered_at"]
                for item in children
            )
        )
        with memory_connection(self.root, "alice") as database:
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM memory_weight_events").fetchone()[0],
                0,
            )

    def test_split_promotion_suffixes_conflicts_without_overwrite(self) -> None:
        now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        source = self._seed(
            "one_month", "source-profile", content="source", weight=10, expires_at=now
        )
        existing = self._seed(
            "half_year", "用户身份与背景", content="keep me", weight=1,
            expires_at=now + timedelta(days=1),
        )
        store = MemoryStore(self.root, "alice", CONFIG)
        location = store.locate_in_tier("one_month", source)

        created = store._split_promote_location(
            location,
            "half_year",
            now,
            [
                {"filename": "用户身份与背景", "content": "new identity"},
                {"filename": "用户身份与背景", "content": "new preference"},
            ],
        )

        self.assertEqual(
            created,
            ["用户身份与背景-2.md", "用户身份与背景-3.md"],
        )
        self.assertEqual(store.get_entry("half_year", existing)["content"], "keep me")
        self.assertIsNone(store.get_entry("one_month", source))

    def test_split_promotion_rolls_back_entire_batch_on_invalid_child(self) -> None:
        now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        source = self._seed(
            "one_month", "rollback-source", content="source", weight=10, expires_at=now
        )
        store = MemoryStore(self.root, "alice", CONFIG)
        before = store.get_entry("one_month", source)
        location = store.locate_in_tier("one_month", source)

        with self.assertRaisesRegex(Exception, "内容不能为空"):
            store._split_promote_location(
                location,
                "half_year",
                now,
                [
                    {"filename": "valid-child", "content": "valid"},
                    {"filename": "invalid-child", "content": ""},
                ],
            )

        self.assertEqual(store.get_entry("one_month", source), before)
        self.assertEqual(store.load_tier("half_year"), [])

    def test_due_scan_rejects_oversized_unsplit_promotion_and_keeps_source(self) -> None:
        now = datetime(2026, 9, 25, tzinfo=timezone.utc)
        source = self._seed(
            "one_month",
            "oversized-unsplit",
            content="甲" * 151,
            weight=10,
            expires_at=now,
        )

        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, name, input_data, **kwargs):
                return _result(
                    promotions=[
                        {
                            "from_tier": "one_month",
                            "to_tier": "half_year",
                            "filename": source,
                            "merged_with": None,
                        }
                    ]
                )

        with patch("cron.review_due.AgentRunner", Runner), self.assertLogs(
            "cron.review_due", level="WARNING"
        ):
            result = scan_and_promote(
                root=self.root,
                user="alice",
                config=CONFIG,
                now=now,
            )

        store = MemoryStore(self.root, "alice", CONFIG)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["applied"], [])
        self.assertEqual(result["pending"], [source])
        self.assertEqual(result["errors"][0]["stage"], "apply")
        self.assertIn("必须声明 memory_type=A 或先拆分", result["errors"][0]["message"])
        self.assertIsNotNone(store.get_entry("one_month", source))
        self.assertEqual(store.load_tier("half_year"), [])

    def test_split_promotion_rejects_oversized_typed_child_atomically(self) -> None:
        now = datetime(2026, 9, 25, tzinfo=timezone.utc)
        source = self._seed(
            "one_month", "typed-rollback", content="source", weight=10, expires_at=now
        )
        store = MemoryStore(self.root, "alice", CONFIG)
        before = store.get_entry("one_month", source)
        location = store.locate_in_tier("one_month", source)

        for memory_type, length, expected in (
            ("B", 151, "B 类内容超过 150 字"),
            ("A", 1001, "A 类内容超过 1000 字"),
        ):
            with self.subTest(memory_type=memory_type), self.assertRaisesRegex(
                Exception, expected
            ):
                store._split_promote_location(
                    location,
                    "half_year",
                    now,
                    [
                        {"filename": "valid-child", "content": "valid"},
                        {"filename": "oversized-child", "content": "乙" * length},
                    ],
                    memory_type=memory_type,
                )

            self.assertEqual(store.get_entry("one_month", source), before)
            self.assertEqual(store.load_tier("half_year"), [])

    def test_merged_promotion_rejects_content_above_declared_type_limit(self) -> None:
        now = datetime(2026, 9, 25, tzinfo=timezone.utc)
        source = self._seed(
            "seven_days", "merge-source-limit", content="source", weight=3, expires_at=now
        )
        target = self._seed(
            "one_month",
            "merge-target-limit",
            content="target",
            weight=2,
            expires_at=now + timedelta(days=1),
        )
        store = MemoryStore(self.root, "alice", CONFIG)
        source_before = store.get_entry("seven_days", source)
        target_before = store.get_entry("one_month", target)
        location = store.locate_in_tier("seven_days", source)

        with self.assertRaisesRegex(Exception, "B 类内容超过 150 字"):
            store._promote_location(
                location,
                "one_month",
                now,
                merged_content="丙" * 151,
                target_filename=target,
                memory_type="B",
            )

        self.assertEqual(store.get_entry("seven_days", source), source_before)
        self.assertEqual(store.get_entry("one_month", target), target_before)

    def test_promotion_system_task_registration_is_idempotent(self) -> None:
        first = ensure_memory_promotion_task(self.root)
        second = ensure_memory_promotion_task(self.root)
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(first, second)
        self.assertEqual(second["type"], "recurring")
        self.assertEqual(second["interval_seconds"], 30)
        self.assertEqual(second["task_id"], MEMORY_PROMOTION_SYSTEM_KEY)
        self.assertEqual(second["exec_mode"], "system")
        self.assertEqual(second["action"], "memory_promotion")
        self.assertEqual(second["prompt"], "")

    def test_self_improve_executor_validates_trigger_specific_output(self) -> None:
        class Context:
            def __init__(self, result):
                self.result = result

            def run_model(self, input_data):
                return self.result

        execute_self_improve(
            Context(_result(candidates=[])),
            {"trigger": "context_compression", "rounds": []},
        )
        with self.assertRaisesRegex(AgentOutputError, "promotions"):
            execute_self_improve(
                Context(_result(candidates=[])),
                {"trigger": "memory_promotion", "promotions": []},
            )

    def test_context_extraction_rejects_non_durable_or_unverifiable_candidates(self) -> None:
        class Context:
            @staticmethod
            def run_model(input_data):
                return _result(
                    candidates=[
                        {
                            "action": "upsert",
                            "filename": "系统配置",
                            "content": "当前模型配置",
                        },
                        {
                            "action": "upsert",
                            "filename": "缺少证据",
                            "content": "用户长期偏好",
                            "durable": True,
                        },
                        {
                            "action": "upsert",
                            "filename": "偏好一",
                            "content": "用户偏好简洁回答",
                            "durable": True,
                            "evidence": "请回答简洁一些",
                        },
                        {
                            "action": "upsert",
                            "filename": "偏好二",
                            "content": "用户偏好中文",
                            "durable": True,
                            "evidence": "以后请使用中文",
                        },
                        {
                            "action": "upsert",
                            "filename": "偏好三",
                            "content": "用户偏好表格",
                            "durable": True,
                            "evidence": "我喜欢表格",
                        },
                        {"action": "forget", "filename": "旧偏好"},
                    ]
                )

        result = execute_self_improve(
            Context(),
            {
                "trigger": "context_compression",
                "rounds": [
                    {
                        "round": 1,
                        "messages": [
                            {
                                "role": "user",
                                "content": "请回答简洁一些，以后请使用中文。",
                            },
                            {
                                "role": "assistant",
                                "content": "我喜欢表格。",
                            },
                        ],
                    }
                ],
            },
        )

        self.assertEqual(
            [item["filename"] for item in result.data["candidates"]],
            ["偏好一", "偏好二"],
        )
        self.assertEqual(result.metadata["candidate_filter"]["accepted"], 2)
        self.assertEqual(result.metadata["candidate_filter"]["rejected"], 4)

    def test_context_extraction_model_receives_only_user_messages(self) -> None:
        captured = {}

        class Context:
            @staticmethod
            def run_model(input_data):
                captured.update(input_data)
                return _result(candidates=[])

        execute_self_improve(
            Context(),
            {
                "trigger": "context_compression",
                "rounds": [
                    {
                        "round": 7,
                        "messages": [
                            {"role": "user", "content": "用户明确原话"},
                            {"role": "assistant", "content": "来自临时重要记忆的复述"},
                        ],
                        "think": {"content": "不可信推理"},
                        "tools": [{"result": "不可信工具结果"}],
                    }
                ],
            },
        )

        self.assertEqual(
            captured["rounds"],
            [
                {
                    "round": 7,
                    "messages": [{"role": "user", "content": "用户明确原话"}],
                }
            ],
        )
        rendered = json.dumps(captured, ensure_ascii=False)
        self.assertNotIn("临时重要记忆", rendered)
        self.assertNotIn("不可信", rendered)

    def test_context_extraction_uses_configured_batch_candidate_cap(self) -> None:
        class Context:
            runner = SimpleNamespace(
                config={"memory": {"extraction_max_candidates_per_batch": 6}}
            )

            @staticmethod
            def run_model(input_data):
                return _result(
                    candidates=[
                        {
                            "action": "upsert",
                            "filename": f"偏好{index}",
                            "content": f"用户长期偏好 {index}",
                            "durable": True,
                            "evidence": f"用户证据 {index}",
                        }
                        for index in range(8)
                    ]
                )

        result = execute_self_improve(
            Context(),
            {
                "trigger": "context_compression",
                "rounds": [
                    {
                        "round": 1,
                        "messages": [
                            {
                                "role": "user",
                                "content": " ".join(
                                    f"用户证据 {value}" for value in range(8)
                                ),
                            }
                        ],
                    }
                ],
            },
        )

        self.assertEqual(len(result.data["candidates"]), 6)
        self.assertEqual(result.metadata["candidate_filter"]["limit"], 6)
        self.assertEqual(result.metadata["candidate_filter"]["rejected"], 2)

    def test_context_extraction_keeps_many_independent_facts_from_one_round(self) -> None:
        fact_count = 12

        class Context:
            runner = SimpleNamespace(
                config={"memory": {"extraction_max_candidates_per_batch": 30}}
            )

            @staticmethod
            def run_model(input_data):
                return _result(
                    candidates=[
                        {
                            "action": "upsert",
                            "filename": f"独立事实{index}",
                            "content": f"用户长期事实 {index}",
                            "durable": True,
                            "evidence": f"长期证据 {index}",
                        }
                        for index in range(fact_count)
                    ]
                )

        result = execute_self_improve(
            Context(),
            {
                "trigger": "context_compression",
                "rounds": [
                    {
                        "round": 1,
                        "messages": [
                            {
                                "role": "user",
                                "content": " ".join(
                                    f"长期证据 {index}" for index in range(fact_count)
                                ),
                            }
                        ],
                    }
                ],
            },
        )

        self.assertEqual(len(result.data["candidates"]), fact_count)
        self.assertEqual(result.metadata["candidate_filter"]["accepted"], fact_count)
        self.assertEqual(result.metadata["candidate_filter"]["rejected"], 0)
        self.assertEqual(result.metadata["candidate_filter"]["limit"], 30)

    def test_manual_review_persists_candidates_for_main_agent_call(self) -> None:
        class Context:
            runner = SimpleNamespace(root=self.root, user="alice", config=CONFIG)

            @staticmethod
            def run_model(input_data):
                return _result(
                    candidates=[
                        {
                            "action": "upsert",
                            "filename": "manual-review",
                            "content": "用户偏好简洁的技术说明。",
                            "explicit": False,
                        }
                    ]
                )

        result = execute_self_improve(
            Context(),
            {"trigger": "manual_review", "request": "整理用户表达偏好"},
        )
        location = MemoryStore(self.root, "alice", CONFIG).locate("manual-review")
        self.assertIsNotNone(location)
        self.assertEqual(location.tier, "seven_days")
        self.assertEqual(
            result.metadata["memory_update"]["created"], ["manual-review.md"]
        )

        with self.assertRaisesRegex(AgentOutputError, "request"):
            execute_self_improve(Context(), {"trigger": "manual_review"})


class SkillCreaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "users" / "alice").mkdir(parents=True)
        self.context = {"root": str(self.root), "user": "alice"}

    def test_create_update_delete_and_scope_layout(self) -> None:
        created = run_skill_creater(
            "create",
            "agent_create",
            "deploy-check",
            "# Deploy check\n\nRun validation.",
            context=self.context,
        )
        path = Path(created["path"]) / "SKILL.md"
        self.assertTrue(path.is_file())
        run_skill_creater(
            "update",
            "agent_create",
            "deploy-check",
            "# Deploy check\n\nRun tests first.",
            context=self.context,
        )
        self.assertIn("Run tests first", path.read_text("utf-8"))
        deleted = run_skill_creater(
            "delete",
            "agent_create",
            "deploy-check",
            context=self.context,
        )
        self.assertTrue(deleted["deleted"])
        self.assertFalse(path.parent.exists())

    def test_rejects_sensitive_traversal_and_self_improve_scope_escape(self) -> None:
        with self.assertRaisesRegex(ValueError, "技能名称无效"):
            run_skill_creater(
                "create", "agent_create", "../escape", "# x", context=self.context
            )
        with self.assertRaisesRegex(ValueError, "敏感凭据"):
            run_skill_creater(
                "create",
                "agent_create",
                "secret",
                "# Secret\n\napi_key=abcd1234",
                context=self.context,
            )
        with self.assertRaises(PermissionError):
            run_skill_creater(
                "create",
                "shared",
                "forbidden",
                "# forbidden",
                context={**self.context, "agent": "self_improve"},
            )


if __name__ == "__main__":
    unittest.main()
