from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.self_improve.executor import execute as execute_self_improve
from cron.executor import execute_cron_task
from cron.review_due import scan_and_promote
from cron.scheduler import (
    MEMORY_PROMOTION_SYSTEM_KEY,
    ensure_memory_promotion_task,
)
from plugins.skill_creater.tool import run as run_skill_creater
from run.agent_runner import AgentOutputError, AgentRunResult
from run.cron_store import CronStore, normalize_task
from run.memory import MemoryStore, normalize_memory_filename
from run.memory_pipeline import extract_compressed_round_memory


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
        path = store.fragment_path(tier, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, "utf-8")
        index = store.load_index(tier)
        index[filename] = {
            "weight": weight,
            "updated_at": expires_at.isoformat(),
            "last_weight_date": None,
            "expires_at": expires_at.isoformat(),
        }
        store.write_index(tier, index)
        return filename

    def test_context_compression_passes_complete_rounds_and_persists_candidates(self) -> None:
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
                return _result(candidates=[{
                    "action": "upsert",
                    "filename": "batch fact",
                    "content": "A fact from complete rounds.",
                    "explicit": False,
                }])

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
            MemoryStore(self.root, "alice", CONFIG).load_tier("seven_days")[0]["content"],
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
                return _result(promotions=[{
                    "from_tier": "seven_days",
                    "to_tier": "one_month",
                    "filename": high,
                    "merged_with": None,
                    "skill_created": False,
                }])

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
        self.assertNotIn(high, store.load_index("seven_days"))
        self.assertNotIn(low, store.load_index("seven_days"))
        self.assertIn(high, store.load_index("one_month"))

    def test_merged_promotion_overwrites_target_and_resets_lifecycle(self) -> None:
        now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        filename = self._seed(
            "seven_days", "same", content="source", weight=3, expires_at=now
        )
        store = MemoryStore(self.root, "alice", CONFIG)
        location = next(
            item
            for item in store._locations(filename)
            if item.tier == "seven_days"
        )
        store._promote_location(
            location,
            "one_month",
            now,
            merged_content="merged content",
        )

        self.assertNotIn(filename, store.load_index("seven_days"))
        target = store.load_index("one_month")[filename]
        self.assertEqual(target["weight"], 0)
        self.assertEqual(
            target["expires_at"],
            (now + timedelta(days=30)).isoformat(),
        )
        self.assertEqual(
            store.fragment_path("one_month", filename).read_text("utf-8").strip(),
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
                return _result(promotions=[{
                    "from_tier": "seven_days",
                    "to_tier": "one_month",
                    "filename": source,
                    "merged_with": target,
                    "content": "merged semantic fact",
                    "skill_created": False,
                }])

        with patch("cron.review_due.AgentRunner", Runner):
            result = scan_and_promote(
                root=self.root,
                user="alice",
                config=CONFIG,
                now=now,
            )

        store = MemoryStore(self.root, "alice", CONFIG)
        self.assertEqual(result["applied"], [source])
        self.assertNotIn(source, store.load_index("seven_days"))
        self.assertEqual(store.load_index("one_month")[target]["weight"], 0)
        self.assertEqual(
            store.fragment_path("one_month", target).read_text("utf-8").strip(),
            "merged semantic fact",
        )

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

    def test_context_extraction_rejects_non_durable_and_caps_candidates(self) -> None:
        class Context:
            @staticmethod
            def run_model(input_data):
                return _result(candidates=[
                    {"action": "upsert", "filename": "系统配置", "content": "当前模型配置"},
                    {"action": "upsert", "filename": "缺少证据", "content": "用户长期偏好", "durable": True},
                    {"action": "upsert", "filename": "偏好一", "content": "用户偏好简洁回答", "durable": True, "evidence": "请回答简洁一些"},
                    {"action": "upsert", "filename": "偏好二", "content": "用户偏好中文", "durable": True, "evidence": "以后请使用中文"},
                    {"action": "upsert", "filename": "偏好三", "content": "用户偏好表格", "durable": True, "evidence": "我喜欢表格"},
                    {"action": "forget", "filename": "旧偏好"},
                ])

        result = execute_self_improve(
            Context(),
            {"trigger": "context_compression", "rounds": [{"round": 1}]},
        )

        self.assertEqual(
            [item["filename"] for item in result.data["candidates"]],
            ["偏好一", "偏好二"],
        )
        self.assertEqual(result.metadata["candidate_filter"]["accepted"], 2)
        self.assertEqual(result.metadata["candidate_filter"]["rejected"], 4)

    def test_manual_review_persists_candidates_for_main_agent_call(self) -> None:
        class Context:
            runner = SimpleNamespace(root=self.root, user="alice", config=CONFIG)

            @staticmethod
            def run_model(input_data):
                return _result(candidates=[{
                    "action": "upsert",
                    "filename": "manual-review",
                    "content": "用户偏好简洁的技术说明。",
                    "explicit": False,
                }])

        result = execute_self_improve(
            Context(),
            {"trigger": "manual_review", "request": "整理用户表达偏好"},
        )
        location = MemoryStore(self.root, "alice", CONFIG).locate("manual-review")
        self.assertIsNotNone(location)
        self.assertEqual(location.tier, "seven_days")
        self.assertEqual(result.metadata["memory_update"]["created"], ["manual-review.md"])

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
