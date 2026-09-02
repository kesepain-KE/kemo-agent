from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from events import RunEvent
from plugins.subagent_dispatch.tool import run as dispatch_subagent
from provider.adapters.compat import chat_response_to_kemo, kemo_request_to_chat
from provider.schema import ChatResponse, Usage
import run.tasks as task_plan_store_module
from run.agents import AgentRunResult
from agents.task_plan.executor import execute as execute_task_plan_agent
from run.tasks import (
    PlanConflictError,
    PlanError,
    PlanNotFoundError,
    PlanStore,
    PlanValidationError,
    normalize_plan,
)
from run.tasks import (
    approve_plan,
    cancel_plan,
    execute_plan,
    get_plan,
    pause_plan,
    resume_plan,
    PlanExecutionError,
)
from run.tasks import (
    PlanGenerationError,
    PlanSkipped,
    edit_plan,
    generate_plan,
    persist_agent_result,
    prepare_task_plan_input,
)
from run.tasks import TaskPlanScheduler
from run.tools import ToolDefinition, ToolRegistry


CONFIG = {
    "provider": {
        "type": "kemo",
        "base_url": "http://127.0.0.1:1/v1",
        "api_key_env": "TEST_PLAN_KEY",
        "model": "mock",
    },
    "tools": {"enabled": True, "timeout": 5},
    "task_plan": {"auto_accept": False, "max_steps": 10},
}


class MockTool:
    """A simple tool that can be discovered by the test registry."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def execute(self, **kwargs: object) -> str:
        self.calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("tool failed")
        return "ok"


class MockProvider:
    """Returns structured plan JSON for task_plan sub-agent."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text

    def chat(self, request):
        return ChatResponse(
            text=self.response_text,
            usage=Usage(1, 1, 2, source="mock"),
            model=request.model,
        )

    def chat_stream(self, request):
        raise AssertionError("stream not expected")

    def create(self, request):
        return chat_response_to_kemo(self.chat(kemo_request_to_chat(request)), request)


def _make_root(users: list[str]) -> tuple[tempfile.TemporaryDirectory, Path]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    (root / "config").mkdir()
    (root / "config" / "global_config.json").write_text(
        json.dumps(CONFIG), "utf-8"
    )
    (root / "config" / "global_soul.md").write_text("SOUL", "utf-8")
    (root / "agents.md").write_text("AGENTS", "utf-8")
    for user in users:
        (root / "users" / user / "task_plan").mkdir(parents=True)
        (root / "users" / user / "user_config.json").write_text("{}", "utf-8")
    return temporary, root


def _make_plan(steps: list[dict], **kwargs) -> dict:
    return normalize_plan(
        title=kwargs.get("title", "Test Plan"),
        description=kwargs.get("description", "Test"),
        user=kwargs.get("user", "alice"),
        steps=steps,
    )


class PlanStoreTests(unittest.TestCase):
    def test_schema_version_remains_one(self) -> None:
        self.assertEqual(task_plan_store_module.SCHEMA_VERSION, 1)

    def test_create_read_list_update_delete(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = _make_plan([
            {"step_id": "step_1", "title": "A", "description": "Step A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        created = store.create(plan)
        self.assertEqual(created["status"], "pending")
        self.assertEqual(created["revision"], 1)
        self.assertTrue(store.path.is_file())
        revisions = store.list_revisions(created["plan_id"])
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["plan_id"], created["plan_id"])
        self.assertEqual(revisions[0]["revision"], 1)
        self.assertEqual(revisions[0]["note"], "创建计划")

        read = store.read(created["plan_id"])
        self.assertEqual(read["plan_id"], created["plan_id"])

        plans = store.list_plans()
        self.assertEqual(len(plans), 1)

        updated = store.update(created["plan_id"], lambda p: {**p, "status": "approved"})
        self.assertEqual(updated["status"], "approved")
        self.assertEqual(updated["revision"], 2)

        self.assertTrue(store.delete(created["plan_id"]))
        with self.assertRaises(PlanNotFoundError):
            store.read(created["plan_id"])

    def test_revision_history_and_rollback_create_new_revision(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        created = store.create(_make_plan([{
            "step_id": "step_1",
            "title": "初始步骤",
            "description": "第一版",
            "tool_name": None,
            "tool_arguments": {},
            "critical": True,
        }], title="第一版计划", description="第一版描述"))
        second = store.update(
            created["plan_id"],
            lambda plan: {**plan, "title": "第二版计划", "status": "paused"},
            note="修改标题并暂停",
        )

        revisions = store.list_revisions(created["plan_id"])
        self.assertEqual([item["revision"] for item in revisions], [2, 1])
        self.assertEqual(revisions[0]["note"], "修改标题并暂停")
        first_snapshot = store.get_revision(created["plan_id"], 1)
        second_snapshot = store.get_revision(created["plan_id"], 2)
        self.assertEqual(first_snapshot["title"], "第一版计划")
        self.assertEqual(second_snapshot["title"], "第二版计划")

        rolled_back = store.rollback(created["plan_id"], 1)
        self.assertEqual(rolled_back["revision"], 3)
        self.assertEqual(rolled_back["title"], "第一版计划")
        self.assertEqual(rolled_back["description"], "第一版描述")
        self.assertEqual(rolled_back["status"], second["status"])
        self.assertEqual(
            [item["revision"] for item in store.list_revisions(created["plan_id"])],
            [3, 2, 1],
        )
        self.assertEqual(store.get_revision(created["plan_id"], 2), second_snapshot)
        self.assertEqual(store.get_revision(created["plan_id"], 3)["title"], "第一版计划")

        self.assertTrue(store.delete(created["plan_id"]))
        database = sqlite3.connect(store.path)
        try:
            remaining = database.execute(
                "SELECT COUNT(*) FROM task_plan_revisions WHERE plan_id=?",
                (created["plan_id"],),
            ).fetchone()[0]
        finally:
            database.close()
        self.assertEqual(remaining, 0)

    def test_rollback_normalizes_running_step_and_preserves_completed_work(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        created = store.create(normalize_plan(
            title="运行中的快照",
            description="验证恢复状态",
            user="alice",
            status="running",
            current_step="step_2",
            steps=[
                {
                    "step_id": "step_1",
                    "title": "第一步",
                    "description": "稍后完成",
                    "status": "pending",
                    "critical": True,
                },
                {
                    "step_id": "step_2",
                    "title": "第二步",
                    "description": "运行中",
                    "status": "running",
                    "result": {"partial": True},
                    "error": {"message": "处理中"},
                    "started_at": "2026-08-22T01:00:00+00:00",
                    "finished_at": "2026-08-22T01:01:00+00:00",
                    "critical": True,
                },
            ],
        ))
        paused = store.update(
            created["plan_id"],
            lambda plan: {
                **plan,
                "status": "paused",
                "current_step": "step_2",
                "steps": [
                    {
                        **plan["steps"][0],
                        "status": "completed",
                        "result": {"durable": True},
                        "started_at": "2026-08-22T01:02:00+00:00",
                        "finished_at": "2026-08-22T01:03:00+00:00",
                    },
                    {**plan["steps"][1], "status": "failed"},
                ],
            },
        )

        rolled_back = store.rollback(
            created["plan_id"],
            created["revision"],
            expected_revision=paused["revision"],
        )

        self.assertEqual(rolled_back["status"], "paused")
        self.assertEqual(rolled_back["current_step"], "step_2")
        self.assertEqual(rolled_back["steps"][0]["status"], "completed")
        self.assertEqual(rolled_back["steps"][0]["result"], {"durable": True})
        restored_running = rolled_back["steps"][1]
        self.assertEqual(restored_running["status"], "pending")
        self.assertIsNone(restored_running["result"])
        self.assertIsNone(restored_running["error"])
        self.assertEqual(restored_running["started_at"], "")
        self.assertEqual(restored_running["finished_at"], "")

    def test_rollback_rejects_snapshot_missing_completed_step(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        created = store.create(_make_plan([{
            "step_id": "step_1",
            "title": "第一步",
            "description": "初始步骤",
            "critical": True,
        }]))
        expanded = store.update(
            created["plan_id"],
            lambda plan: {
                **plan,
                "status": "paused",
                "steps": [
                    *plan["steps"],
                    {
                        "step_id": "step_2",
                        "title": "已完成的新步骤",
                        "description": "不能被历史回滚删除",
                        "status": "completed",
                        "depends_on": ["step_1"],
                        "tool_name": None,
                        "tool_arguments": {},
                        "critical": True,
                        "result": {"ok": True},
                        "error": None,
                        "started_at": "",
                        "finished_at": "2026-08-22T01:00:00+00:00",
                    },
                ],
            },
        )

        with self.assertRaisesRegex(PlanValidationError, "缺少已完成步骤"):
            store.rollback(
                created["plan_id"],
                created["revision"],
                expected_revision=expanded["revision"],
            )
        self.assertEqual(store.read(created["plan_id"])["revision"], expanded["revision"])

    def test_large_revision_values_are_deduplicated_and_transparently_restored(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        created = store.create(normalize_plan(
            title="大结果计划",
            description="验证修订快照不会重复放大",
            user="alice",
            status="paused",
            steps=[{
                "step_id": "step_1",
                "title": "大结果",
                "description": "压缩内容",
                "status": "completed",
                "result": {"output": "A" * 100_000},
                "critical": True,
            }],
        ))

        database = sqlite3.connect(store.path)
        try:
            stored = database.execute(
                "SELECT plan_json FROM task_plan_revisions WHERE plan_id=? AND revision=1",
                (created["plan_id"],),
            ).fetchone()[0]
            blob_count = database.execute(
                "SELECT COUNT(*) FROM task_plan_revision_blobs WHERE plan_id=?",
                (created["plan_id"],),
            ).fetchone()[0]
        finally:
            database.close()

        self.assertLess(len(stored), 5_000)
        self.assertEqual(blob_count, 1)
        self.assertEqual(
            store.get_revision(created["plan_id"], 1)["steps"][0]["result"]["output"],
            "A" * 100_000,
        )

        updated = store.update(
            created["plan_id"],
            lambda plan: {**plan, "title": "仅修改标题"},
        )
        database = sqlite3.connect(store.path)
        try:
            blob_count = database.execute(
                "SELECT COUNT(*) FROM task_plan_revision_blobs WHERE plan_id=?",
                (created["plan_id"],),
            ).fetchone()[0]
        finally:
            database.close()
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(blob_count, 1)

    def test_plan_storage_redacts_embedded_secrets_in_text_and_large_values(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        secret = "Authorization: Bearer abcdefghijklmnop"
        plan = _make_plan([{
            "step_id": "step_1",
            "title": "说明 token 的使用方式",
            "description": "普通说明，不应因为出现 token 一词而被删除",
            "tool_name": None,
            "tool_arguments": {"value": "token: abcdefgh"},
            "result": {"output": secret + "\n" + ("x" * 10_000)},
            "error": {"message": "sk-1234567890abcdef"},
            "critical": True,
        }])
        created = store.create(plan)
        self.assertNotIn("abcdefghijklmnop", json.dumps(created, ensure_ascii=False))
        self.assertNotIn("1234567890abcdef", json.dumps(created, ensure_ascii=False))

        database = sqlite3.connect(store.path)
        try:
            step_row = database.execute(
                "SELECT tool_arguments_json, result_json, error_json "
                "FROM task_plan_steps WHERE plan_id=?",
                (created["plan_id"],),
            ).fetchone()
            revision_row = database.execute(
                "SELECT plan_json, note FROM task_plan_revisions "
                "WHERE plan_id=? AND revision=1",
                (created["plan_id"],),
            ).fetchone()
            blob_rows = database.execute(
                "SELECT payload FROM task_plan_revision_blobs WHERE plan_id=?",
                (created["plan_id"],),
            ).fetchall()
        finally:
            database.close()

        persisted_text = " ".join(str(value) for value in step_row)
        persisted_text += " " + str(revision_row[0]) + " " + " ".join(
            str(row[0]) for row in blob_rows
        )
        self.assertNotIn("abcdefghijklmnop", persisted_text)
        self.assertNotIn("1234567890abcdef", persisted_text)
        self.assertIn("task-plan-secret-redacted", persisted_text)
        self.assertEqual(
            store.read(created["plan_id"])["steps"][0]["title"],
            "说明 token 的使用方式",
        )

    def test_revision_with_text_redaction_cannot_be_rolled_back(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        created = store.create(_make_plan([{
            "step_id": "step_1",
            "title": "安全步骤",
            "description": "普通描述",
            "tool_name": None,
            "tool_arguments": {},
            "critical": True,
        }]))
        updated = store.update(
            created["plan_id"],
            lambda plan: {
                **plan,
                "status": "paused",
                "steps": [{
                    **plan["steps"][0],
                    "result": "api_key=abcdefgh",
                }],
            },
            note="api_key=abcdefgh",
        )
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(
            store.list_revisions(created["plan_id"])[0]["note"],
            "[task-plan-secret-redacted]",
        )
        with self.assertRaisesRegex(PlanValidationError, "脱敏"):
            store.rollback(created["plan_id"], 2, expected_revision=updated["revision"])

    def test_plan_rejects_new_sensitive_tool_arguments(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = _make_plan([{
            "step_id": "step_1",
            "title": "敏感参数",
            "description": "不得进入计划数据库",
            "tool_name": None,
            "tool_arguments": {"api_key": "example-sensitive-value"},
            "critical": True,
        }])

        with self.assertRaisesRegex(PlanValidationError, "不得持久化"):
            store.create(plan)

        safe = store.create(_make_plan([{
            "step_id": "step_1",
            "title": "安全参数",
            "description": "普通参数允许保存",
            "tool_name": None,
            "tool_arguments": {"token_limit": 1000},
            "critical": True,
        }]))
        with self.assertRaisesRegex(PlanValidationError, "不得持久化"):
            store.update(
                safe["plan_id"],
                lambda current: {
                    **current,
                    "steps": [{
                        **current["steps"][0],
                        "tool_arguments": {"session_token": "secret-value"},
                    }],
                },
            )

    def test_plan_update_rejects_sensitive_arguments_after_in_place_mutation(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        created = store.create(_make_plan([{
            "step_id": "step_1",
            "title": "普通参数",
            "description": "原计划",
            "tool_name": None,
            "tool_arguments": {},
            "critical": True,
        }]))

        def mutate(current):
            current["steps"][0]["tool_arguments"]["api_key"] = "secret-value"
            return current

        with self.assertRaisesRegex(PlanValidationError, "不得持久化"):
            store.update(created["plan_id"], mutate)
        persisted = store.read(created["plan_id"])
        self.assertEqual(persisted["revision"], created["revision"])
        self.assertEqual(persisted["steps"][0]["tool_arguments"], {})

    def test_plan_rejects_common_secret_key_variants(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        for key in ("secret_key", "signing_key", "encryption_key", "client_key"):
            plan = _make_plan([{
                "step_id": "step_1",
                "title": "敏感参数",
                "description": "不得进入计划数据库",
                "tool_name": None,
                "tool_arguments": {key: "secret-value"},
                "critical": True,
            }])
            with self.assertRaisesRegex(PlanValidationError, "不得持久化"):
                store.create(plan)

    def test_revision_snapshot_failure_rolls_back_plan_update(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        created = store.create(_make_plan([{
            "step_id": "step_1",
            "title": "原步骤",
            "description": "原内容",
            "tool_name": None,
            "tool_arguments": {},
            "critical": True,
        }]))
        database = sqlite3.connect(store.path)
        try:
            database.execute("PRAGMA foreign_keys=ON")
            database.execute(
                """
                INSERT INTO task_plan_revisions(
                    plan_id, revision, plan_json, note, created_at
                ) VALUES(?, 2, '{}', '冲突占位', 'now')
                """,
                (created["plan_id"],),
            )
            database.commit()
        finally:
            database.close()

        with self.assertRaises(PlanError):
            store.update(
                created["plan_id"],
                lambda plan: {**plan, "title": "不得落盘"},
            )
        current = store.read(created["plan_id"])
        self.assertEqual(current["revision"], 1)
        self.assertEqual(current["title"], "Test Plan")

    def test_existing_database_adds_revision_table_and_backfills_current_plan(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        created = store.create(_make_plan([{
            "step_id": "step_1",
            "title": "旧库步骤",
            "description": "旧库内容",
            "tool_name": None,
            "tool_arguments": {},
            "critical": True,
        }]))
        database = sqlite3.connect(store.path)
        try:
            database.execute("DROP TABLE task_plan_revisions")
            database.commit()
        finally:
            database.close()
        key = str(store.path.resolve()).casefold()
        with task_plan_store_module._READY_DATABASES_GUARD:
            task_plan_store_module._READY_DATABASES.discard(key)

        revisions = PlanStore(root, "alice").list_revisions(created["plan_id"])
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["revision"], created["revision"])
        self.assertEqual(revisions[0]["note"], "迁移现有计划")

    def test_duplicate_plan_id_conflict(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = _make_plan([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        store.create(plan)
        with self.assertRaises(PlanConflictError):
            store.create(plan)

    def test_cycle_dependency_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            _make_plan([
                {"step_id": "step_1", "title": "A", "description": "A",
                 "depends_on": ["step_2"], "tool_name": None,
                 "tool_arguments": {}, "critical": True},
                {"step_id": "step_2", "title": "B", "description": "B",
                 "depends_on": ["step_1"], "tool_name": None,
                 "tool_arguments": {}, "critical": True},
            ])

    def test_blocked_tool_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            _make_plan([
                {"step_id": "step_1", "title": "A", "description": "A",
                 "tool_name": "task_plan_create", "tool_arguments": {},
                 "critical": True},
            ])

    def test_unknown_tool_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            normalize_plan(
                title="T", description="D", user="alice",
                steps=[{"step_id": "step_1", "title": "A", "description": "A",
                        "tool_name": "nonexistent_tool", "tool_arguments": {},
                        "critical": True}],
                tool_names={"get_current_time"},
            )

    def test_multi_user_isolation(self) -> None:
        _, root = _make_root(["alice", "bob"])
        alice_store = PlanStore(root, "alice")
        bob_store = PlanStore(root, "bob")
        alice_plan = _make_plan([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ], user="alice")
        bob_plan = _make_plan([
            {"step_id": "step_1", "title": "B", "description": "B",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ], user="bob")
        alice_store.create(alice_plan)
        bob_store.create(bob_plan)
        self.assertEqual(len(alice_store.list_plans()), 1)
        self.assertEqual(len(bob_store.list_plans()), 1)
        self.assertEqual(alice_store.list_plans()[0]["title"], "Test Plan")
        self.assertEqual(bob_store.list_plans()[0]["title"], "Test Plan")

    def test_plan_conversation_scope_cannot_be_changed_after_creation(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = normalize_plan(
            title="Scoped plan",
            description="scope is immutable",
            user="alice",
            source="web",
            session_id="space-a",
            steps=[
                {
                    "step_id": "step_1",
                    "title": "A",
                    "description": "A",
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                }
            ],
        )
        created = store.create(plan)
        with self.assertRaises(PlanValidationError):
            store.update(
                created["plan_id"],
                lambda current: {
                    **current,
                    "source": "app",
                    "session_id": "space-b",
                },
            )
        unchanged = store.read(created["plan_id"])
        self.assertEqual(unchanged["source"], "web")
        self.assertEqual(unchanged["session_id"], "space-a")

    def test_plan_conversation_scope_cannot_be_changed_by_in_place_mutator(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = normalize_plan(
            title="Scoped plan",
            description="scope is immutable",
            user="alice",
            source="web",
            session_id="space-a",
            steps=[
                {
                    "step_id": "step_1",
                    "title": "A",
                    "description": "A",
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                }
            ],
        )
        created = store.create(plan)

        def mutate_in_place(current: dict[str, object]) -> dict[str, object]:
            current["source"] = "app"
            current["session_id"] = "space-b"
            return current

        with self.assertRaises(PlanValidationError):
            store.update(created["plan_id"], mutate_in_place)
        unchanged = store.read(created["plan_id"])
        self.assertEqual(unchanged["source"], "web")
        self.assertEqual(unchanged["session_id"], "space-a")

    def test_recover_interrupted(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = store.create(_make_plan([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ]))
        # Simulate a crash mid-execution
        store.update(plan["plan_id"], lambda p: {
            **p, "status": "running",
            "steps": [{**p["steps"][0], "status": "running"}],
        })
        recovered = store.recover_interrupted()
        self.assertEqual(len(recovered), 1)
        plan = store.read(plan["plan_id"])
        self.assertEqual(plan["status"], "paused")
        self.assertEqual(plan["steps"][0]["status"], "pending")

    def test_reminder_is_persisted(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = normalize_plan(
            title="Reminder",
            description="Reminder test",
            user="alice",
            reminder="当前任务计划已创建，请让用户点击批准后执行",
            steps=[{
                "step_id": "step_1",
                "title": "A",
                "description": "A",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            }],
        )
        store.create(plan)
        self.assertEqual(
            store.read(plan["plan_id"])["reminder"],
            "当前任务计划已创建，请让用户点击批准后执行",
        )
        updated = store.update(plan["plan_id"], lambda item: item)
        self.assertEqual(
            updated["reminder"],
            "当前任务计划已创建，请让用户点击批准后执行",
        )


class PlanExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.root = _make_root(["alice"])
        self.addCleanup(self._tmp.cleanup)

    def _plan_with_steps(self, steps: list[dict]) -> dict:
        store = PlanStore(self.root, "alice")
        plan = _make_plan(steps)
        return store.create(plan)

    def test_approve_and_execute_simple_plan(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        approve_plan(self.root, "alice", plan["plan_id"])
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG,
        ))
        types = [e.type for e in events]
        self.assertIn("done", types)
        done = next(e for e in events if e.type == "done")
        self.assertEqual(done.metadata["status"], "completed")

    def test_pending_plan_cannot_execute(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG,
        ))
        self.assertTrue(any(e.type == "error" for e in events))

    def test_running_plan_cannot_be_adopted_by_second_executor(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        approve_plan(self.root, "alice", plan["plan_id"])
        first = execute_plan(
            root=self.root,
            user="alice",
            plan_id=plan["plan_id"],
            config=CONFIG,
        )
        first_event = next(first)
        self.assertEqual(first_event.type, "tool_call_start")

        second_events = list(execute_plan(
            root=self.root,
            user="alice",
            plan_id=plan["plan_id"],
            config=CONFIG,
        ))

        self.assertTrue(any(event.type == "error" for event in second_events))
        stored = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(stored["status"], "running")
        self.assertEqual(stored["steps"][0]["status"], "running")

    def test_atomic_transition_does_not_revive_cancelled_plan(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        cancel_plan(self.root, "alice", plan["plan_id"])

        with self.assertRaises(PlanExecutionError):
            approve_plan(self.root, "alice", plan["plan_id"])

        self.assertEqual(
            get_plan(self.root, "alice", plan["plan_id"])["status"],
            "cancelled",
        )

    def test_inflight_step_can_persist_after_plan_is_paused(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": "get_current_time", "tool_arguments": {}, "critical": True},
        ])
        approve_plan(self.root, "alice", plan["plan_id"])

        def event_source(request):
            del request
            pause_plan(self.root, "alice", plan["plan_id"])
            yield RunEvent(
                type="tool_call_result",
                tool_name="get_current_time",
                result={"ok": True, "result": "done"},
                metadata={"status": "completed"},
            )
            yield RunEvent(type="text_delta", content="done")
            yield RunEvent(type="done", metadata={"status": "completed"})

        events = list(execute_plan(
            root=self.root,
            user="alice",
            plan_id=plan["plan_id"],
            config=CONFIG,
            agent_event_source=event_source,
        ))

        self.assertFalse(any(event.type == "error" for event in events))
        stored = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(stored["status"], "paused")
        self.assertEqual(stored["steps"][0]["status"], "completed")

    def test_auto_accept_off_still_creates_pending(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        self.assertEqual(plan["status"], "pending")

    def test_dependency_order(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
            {"step_id": "step_2", "title": "B", "description": "B",
             "depends_on": ["step_1"], "tool_name": None,
             "tool_arguments": {}, "critical": True},
        ])
        approve_plan(self.root, "alice", plan["plan_id"])
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG,
        ))
        done = next(e for e in events if e.type == "done")
        self.assertEqual(done.metadata["status"], "completed")
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["steps"][0]["status"], "completed")
        self.assertEqual(plan["steps"][1]["status"], "completed")

    def test_scheduler_executes_approved_steps_through_main_agent(self) -> None:
        plan = self._plan_with_steps([
            {
                "step_id": "step_1",
                "title": "Collect",
                "description": "Collect data",
                "tool_name": "shell",
                "tool_arguments": {"command": "first"},
                "critical": True,
            },
            {
                "step_id": "step_2",
                "title": "Report",
                "description": "Write report",
                "depends_on": ["step_1"],
                "tool_name": "shell",
                "tool_arguments": {"command": "second"},
                "critical": True,
            },
        ])
        approve_plan(self.root, "alice", plan["plan_id"])
        requests: list[dict] = []

        def event_source(request, **kwargs):
            requests.append(dict(request))
            yield RunEvent(
                type="tool_call_result",
                tool_name="shell",
                result={"ok": True, "result": "done"},
                metadata={"status": "completed"},
            )
            yield RunEvent(type="text_delta", content="done")
            yield RunEvent(type="done", metadata={"committed": True})

        scheduler = TaskPlanScheduler(self.root, event_source=event_source)
        result = scheduler.scan_once()

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["source"], "cli")
        self.assertEqual(result["session_id"], "default")
        self.assertEqual(len(requests), 2)
        self.assertTrue(
            all(
                request["source"] == "cli"
                and request["session_id"] == "default"
                for request in requests
            )
        )
        self.assertIn("只执行当前步骤", requests[0]["prompt"])
        self.assertIn("step_1", requests[0]["prompt"])
        self.assertIn("step_2", requests[1]["prompt"])
        stored = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(
            [step["status"] for step in stored["steps"]],
            ["completed", "completed"],
        )

    def test_scheduler_keeps_each_plan_request_in_its_own_conversation_space(self) -> None:
        store = PlanStore(self.root, "alice")
        plans = {}
        for session_id in ("space-a", "space-b"):
            plan = store.create(
                normalize_plan(
                    title=f"Plan {session_id}",
                    description="scoped scheduler test",
                    user="alice",
                    source="web",
                    session_id=session_id,
                    status="approved",
                    steps=[
                        {
                            "step_id": "step_1",
                            "title": "Run",
                            "description": "run one scoped step",
                            "tool_name": None,
                            "tool_arguments": {},
                            "critical": True,
                        }
                    ],
                )
            )
            plans[plan["plan_id"]] = session_id

        requests: list[dict[str, Any]] = []

        def event_source(request, **_kwargs):
            requests.append(dict(request))
            yield RunEvent(
                type="tool_call_result",
                tool_name="",
                result={"ok": True, "result": "done"},
                metadata={"status": "completed"},
            )
            yield RunEvent(type="done", metadata={"committed": True})

        scheduler = TaskPlanScheduler(self.root, event_source=event_source)
        results = [scheduler.scan_once(), scheduler.scan_once()]

        self.assertTrue(all(result is not None for result in results))
        result_scopes = {
            (result["source"], result["session_id"])
            for result in results
            if result is not None
        }
        self.assertEqual(result_scopes, {("web", "space-a"), ("web", "space-b")})
        self.assertEqual(
            {
                (request["_task_plan_id"], request["source"], request["session_id"])
                for request in requests
            },
            {
                (plan_id, "web", session_id)
                for plan_id, session_id in plans.items()
            },
        )

    def test_executor_events_keep_the_plan_scope_over_agent_metadata(self) -> None:
        plan = self._plan_with_steps([
            {
                "step_id": "step_1",
                "title": "Scoped step",
                "description": "Verify event ownership",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            },
        ])
        approve_plan(self.root, "alice", plan["plan_id"])

        def event_source(_request):
            yield RunEvent(
                type="text_delta",
                content="progress",
                metadata={"source": "other", "session_id": "other-session"},
            )
            yield RunEvent(
                type="done",
                metadata={"status": "completed", "source": "other", "session_id": "other-session"},
            )

        events = list(
            execute_plan(
                root=self.root,
                user="alice",
                plan_id=plan["plan_id"],
                config=CONFIG,
                agent_event_source=event_source,
            )
        )

        self.assertTrue(events)
        for event in events:
            self.assertEqual(event.metadata["plan_id"], plan["plan_id"])
            self.assertEqual(event.metadata["source"], plan["source"])
            self.assertEqual(event.metadata["session_id"], plan["session_id"])

    def test_agent_limited_terminal_preserves_reason_when_plan_pauses(self) -> None:
        plan = self._plan_with_steps([
            {
                "step_id": "step_1",
                "title": "Collect",
                "description": "Collect data",
                "tool_name": "shell",
                "tool_arguments": {"command": "first"},
                "critical": True,
            },
        ])
        approve_plan(self.root, "alice", plan["plan_id"])

        def event_source(request):
            del request
            yield RunEvent(
                type="done",
                metadata={
                    "committed": True,
                    "status": "limited",
                    "stop_reason": "max_tool_iterations",
                },
            )

        events = list(
            execute_plan(
                root=self.root,
                user="alice",
                plan_id=plan["plan_id"],
                config=CONFIG,
                agent_event_source=event_source,
            )
        )

        done = next(event for event in events if event.type == "done")
        self.assertEqual(done.metadata["status"], "paused")
        self.assertEqual(done.metadata["stop_reason"], "max_tool_iterations")
        stored = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(
            stored["steps"][0]["error"]["exception_type"],
            "PlanAgentRunLimited",
        )
        self.assertEqual(
            stored["steps"][0]["error"]["stop_reason"],
            "max_tool_iterations",
        )

    def test_critical_failure_pauses_plan(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": "get_current_time", "tool_arguments": {},
             "critical": True},
        ])
        store = PlanStore(self.root, "alice")
        store.update(plan["plan_id"], lambda p: {**p, "status": "approved"})
        # Use a registry where the tool fails
        from run.tools import ToolDefinition, ToolRegistry
        def fail_tool(**kwargs):
            raise RuntimeError("fail")
        fail_def = ToolDefinition(
            name="get_current_time", description="fail",
            input_schema={"type": "object", "properties": {}},
            version="1", enabled=True, entrypoint="tool.py:fail_tool",
            source="test", directory=self.root / "plugins" / "get_current_time",
            _callable=fail_tool,
        )
        registry = ToolRegistry({"get_current_time": fail_def})
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG, tool_registry=registry,
        ))
        done = next(e for e in events if e.type == "done")
        self.assertEqual(done.metadata["status"], "paused")
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["status"], "paused")
        self.assertEqual(plan["steps"][0]["status"], "failed")

    def test_failed_critical_dependency_requires_fix_instead_of_deadlocking(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "Broken", "description": "B",
             "tool_name": None, "tool_arguments": {}, "critical": True},
            {"step_id": "step_2", "title": "Blocked", "description": "B",
             "depends_on": ["step_1"], "tool_name": None,
             "tool_arguments": {}, "critical": True},
        ])
        store = PlanStore(self.root, "alice")
        store.update(
            plan["plan_id"],
            lambda current: {
                **current,
                "status": "approved",
                "steps": [
                    {**current["steps"][0], "status": "failed", "error": {"message": "fix me"}},
                    current["steps"][1],
                ],
            },
        )

        events = list(execute_plan(
            root=self.root,
            user="alice",
            plan_id=plan["plan_id"],
            config=CONFIG,
        ))

        done = next(event for event in events if event.type == "done")
        self.assertEqual(done.metadata["status"], "paused")
        self.assertEqual(done.metadata["reason"], "failed_step_needs_fix")
        self.assertEqual(done.metadata["failed_step_ids"], ["step_1"])
        self.assertIn("修正失败步骤", done.metadata["message"])
        self.assertEqual(store.read(plan["plan_id"])["status"], "paused")

    def test_failed_critical_leaf_step_requires_fix_after_resume(self) -> None:
        plan = self._plan_with_steps([{
            "step_id": "step_1",
            "title": "Final broken step",
            "description": "No downstream dependency",
            "tool_name": None,
            "tool_arguments": {},
            "critical": True,
        }])
        store = PlanStore(self.root, "alice")
        store.update(
            plan["plan_id"],
            lambda current: {
                **current,
                "status": "approved",
                "steps": [{
                    **current["steps"][0],
                    "status": "failed",
                    "error": {"message": "fix final step"},
                }],
            },
        )

        events = list(execute_plan(
            root=self.root,
            user="alice",
            plan_id=plan["plan_id"],
            config=CONFIG,
        ))

        done = next(event for event in events if event.type == "done")
        self.assertEqual(done.metadata["status"], "paused")
        self.assertEqual(done.metadata["reason"], "failed_step_needs_fix")
        self.assertEqual(done.metadata["failed_step_ids"], ["step_1"])
        self.assertEqual(store.read(plan["plan_id"])["status"], "paused")

    def test_non_critical_failure_continues(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "Fail", "description": "F",
             "tool_name": "get_current_time", "tool_arguments": {},
             "critical": False},
            {"step_id": "step_2", "title": "OK", "description": "O",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        store = PlanStore(self.root, "alice")
        store.update(plan["plan_id"], lambda p: {**p, "status": "approved"})
        from run.tools import ToolDefinition, ToolRegistry
        def fail_tool(**kwargs):
            raise RuntimeError("fail")
        fail_def = ToolDefinition(
            name="get_current_time", description="fail",
            input_schema={"type": "object", "properties": {}},
            version="1", enabled=True, entrypoint="tool.py:fail_tool",
            source="test", directory=self.root / "plugins" / "get_current_time",
            _callable=fail_tool,
        )
        registry = ToolRegistry({"get_current_time": fail_def})
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG, tool_registry=registry,
        ))
        done = next(e for e in events if e.type == "done")
        self.assertEqual(done.metadata["status"], "failed")
        self.assertEqual(done.metadata["reason"], "no_runnable_step")
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["status"], "failed")
        self.assertEqual(plan["steps"][0]["status"], "failed")
        self.assertEqual(plan["steps"][1]["status"], "completed")

    def test_cancel_plan(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
            {"step_id": "step_2", "title": "B", "description": "B",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        cancel_plan(self.root, "alice", plan["plan_id"])
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["status"], "cancelled")
        self.assertEqual(plan["steps"][0]["status"], "cancelled")
        self.assertEqual(plan["steps"][1]["status"], "cancelled")

    def test_pause_and_resume(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        approve_plan(self.root, "alice", plan["plan_id"])
        pause_plan(self.root, "alice", plan["plan_id"])
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["status"], "paused")
        resume_plan(self.root, "alice", plan["plan_id"])
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["status"], "approved")

    def test_completed_step_not_replayed(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
            {"step_id": "step_2", "title": "B", "description": "B",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        store = PlanStore(self.root, "alice")
        store.update(plan["plan_id"], lambda p: {
            **p, "status": "approved",
            "steps": [
                {**p["steps"][0], "status": "completed"},
                p["steps"][1],
            ],
        })
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG,
        ))
        done = next(e for e in events if e.type == "done")
        self.assertEqual(done.metadata["status"], "completed")
        plan = get_plan(self.root, "alice", plan["plan_id"])
        # step_1 was already completed, should not have been re-run
        self.assertEqual(plan["steps"][0]["status"], "completed")
        self.assertEqual(plan["steps"][1]["status"], "completed")


class PlanGenerationTests(unittest.TestCase):
    def _capturing_runner(self, response: dict, calls: list[dict]):
        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, name, input_data, **kwargs):
                calls.append(input_data)
                return AgentRunResult(
                    agent="task_plan",
                    data=response,
                    raw_text="",
                    usage={"total_tokens": 1},
                    model="mock",
                )

        return Runner

    def test_generate_plan_with_mock_provider(self) -> None:
        _, root = _make_root(["alice"])
        (root / "agents").mkdir()
        (root / "agents" / "task_plan").mkdir()
        (root / "agents" / "task_plan" / "agent.json").write_text(
            json.dumps({
                "schema_version": 1, "name": "task_plan", "version": "2.0.0",
                "description": "plan", "enabled": True, "instruction": "任务计划.txt",
                "model_profile": "default", "timeout": 10, "execution": "sync",
                "write_policy": "none",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {"type": "object", "additionalProperties": True},
            }), "utf-8",
        )
        (root / "agents" / "task_plan" / "任务计划.txt").write_text(
            "test instruction", "utf-8",
        )
        plan_json = json.dumps({
            "action": "create",
            "title": "Test Plan",
            "description": "Test goal",
            "steps": [
                {"step_id": "step_1", "title": "A", "description": "A",
                 "tool_name": None, "tool_arguments": {}, "critical": True},
            ],
        })
        with patch.dict(os.environ, {"TEST_PLAN_KEY": "x"}, clear=False):
            plan = generate_plan(
                root=root, user="alice", goal="test goal",
                provider_factory=lambda _: MockProvider(plan_json),
                config=CONFIG,
            )
        self.assertEqual(plan["title"], "Test Plan")
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["status"], "pending")

    def test_subagent_result_is_persisted_for_web_plan_bubble(self) -> None:
        _, root = _make_root(["alice"])
        plan = persist_agent_result(
            root=root,
            user="alice",
            input_data={"action": "create", "goal": "测试任务", "auto_accept": False},
            result_data={
                "action": "create",
                "title": "测试任务计划",
                "description": "测试任务",
                "steps": [{
                    "step_id": "step_1",
                    "title": "检查结果",
                    "description": "确认计划已落盘",
                    "depends_on": [],
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                }],
            },
            source="web",
            session_id="web-session",
            config=CONFIG,
        )
        self.assertIsNotNone(plan)
        stored = PlanStore(root, "alice").read(plan["plan_id"])
        self.assertEqual(stored["session_id"], "web-session")
        self.assertEqual(stored["status"], "pending")
        self.assertEqual(stored["reminder"], "当前任务计划已创建，请让用户点击批准后执行")

    def test_generate_plan_skip(self) -> None:
        _, root = _make_root(["alice"])
        (root / "agents").mkdir()
        (root / "agents" / "task_plan").mkdir()
        (root / "agents" / "task_plan" / "agent.json").write_text(
            json.dumps({
                "schema_version": 1, "name": "task_plan", "version": "2.0.0",
                "description": "plan", "enabled": True, "instruction": "任务计划.txt",
                "model_profile": "default", "timeout": 10, "execution": "sync",
                "write_policy": "none",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {"type": "object", "additionalProperties": True},
            }), "utf-8",
        )
        (root / "agents" / "task_plan" / "任务计划.txt").write_text(
            "test instruction", "utf-8",
        )
        skip_json = json.dumps({"action": "skip", "message": "too simple"})
        with patch.dict(os.environ, {"TEST_PLAN_KEY": "x"}, clear=False):
            with self.assertRaises(PlanSkipped):
                generate_plan(
                    root=root, user="alice", goal="hello",
                    provider_factory=lambda _: MockProvider(skip_json),
                    config=CONFIG,
                )

    def test_generate_plan_invalid_json(self) -> None:
        _, root = _make_root(["alice"])
        (root / "agents").mkdir()
        (root / "agents" / "task_plan").mkdir()
        (root / "agents" / "task_plan" / "agent.json").write_text(
            json.dumps({
                "schema_version": 1, "name": "task_plan", "version": "2.0.0",
                "description": "plan", "enabled": True, "instruction": "任务计划.txt",
                "model_profile": "default", "timeout": 10, "execution": "sync",
                "write_policy": "none",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {"type": "object", "additionalProperties": True},
            }), "utf-8",
        )
        (root / "agents" / "task_plan" / "任务计划.txt").write_text(
            "test instruction", "utf-8",
        )
        with patch.dict(os.environ, {"TEST_PLAN_KEY": "x"}, clear=False):
            with self.assertRaises(PlanGenerationError):
                generate_plan(
                    root=root, user="alice", goal="test",
                    provider_factory=lambda _: MockProvider("not json at all"),
                    config=CONFIG,
                )

    def test_generation_injects_all_skills_and_knowledge_in_fixed_order(self) -> None:
        _, root = _make_root(["alice"])
        files = {
            root / "plugins" / "zeta" / "SKILL.md": "# zeta\n" + "Z" * 12000,
            root / "plugins" / "alpha" / "SKILL.md": "# alpha",
            root / "shared_skills" / "development" / "python" / "SKILL.md": "# shared python",
            root / "users" / "alice" / "user_skills" / "agent_create" / "deploy" / "SKILL.md": "# user deploy",
            root / "global_knowledge" / "data_structure.md": "GLOBAL INDEX",
            root / "shared_knowledge" / "data_structure.md": "SHARED INDEX",
            root / "users" / "alice" / "knowledge" / "data_structure.md": "USER INDEX",
        }
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, "utf-8")
        calls: list[dict] = []
        response = {
            "action": "create",
            "title": "Injected",
            "description": "goal",
            "steps": [{
                "step_id": "step_1",
                "title": "A",
                "description": "A",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            }],
        }
        with patch(
            "run.tasks.service.AgentRunner",
            self._capturing_runner(response, calls),
        ):
            plan = generate_plan(
                root=root,
                user="alice",
                goal="goal",
                config=CONFIG,
                tool_registry=ToolRegistry({}),
            )

        input_data = calls[0]
        ordered = list(input_data)
        fields = [
            "available_tools",
            "plugin_skills",
            "shared_skills_text",
            "user_skills_text",
            "global_knowledge_index",
            "shared_knowledge_index",
            "user_knowledge_index",
        ]
        self.assertEqual(sorted(ordered.index(field) for field in fields), [ordered.index(field) for field in fields])
        self.assertLess(
            input_data["plugin_skills"].index("plugins/alpha/SKILL.md"),
            input_data["plugin_skills"].index("plugins/zeta/SKILL.md"),
        )
        self.assertIn("Z" * 12000, input_data["plugin_skills"])
        self.assertIn("shared python", input_data["shared_skills_text"])
        self.assertIn("user deploy", input_data["user_skills_text"])
        self.assertEqual(input_data["global_knowledge_index"], "GLOBAL INDEX")
        self.assertEqual(input_data["shared_knowledge_index"], "SHARED INDEX")
        self.assertEqual(input_data["user_knowledge_index"], "USER INDEX")
        self.assertFalse(input_data["auto_accept"])
        self.assertEqual(
            plan["reminder"],
            "当前任务计划已创建，请让用户点击批准后执行",
        )

    def test_generation_uses_configured_knowledge_scopes_and_named_indexes(self) -> None:
        _, root = _make_root(["alice"])
        files = {
            root / "global_knowledge" / "index.md": "GLOBAL INDEX",
            root / "shared_knowledge" / "索引.md": "SHARED INDEX",
            root / "users" / "alice" / "knowledge" / "目录.md": "USER INDEX",
        }
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, "utf-8")

        payload = prepare_task_plan_input(
            root=root,
            user="alice",
            input_data={"action": "create", "goal": "goal"},
            config={
                **CONFIG,
                "knowledge": {"use_shared": False, "use_global": False},
            },
            tool_registry=ToolRegistry({}),
        )

        self.assertEqual(payload["global_knowledge_index"], "")
        self.assertEqual(payload["shared_knowledge_index"], "")
        self.assertEqual(payload["user_knowledge_index"], "USER INDEX")

        enabled = prepare_task_plan_input(
            root=root,
            user="alice",
            input_data={"action": "create", "goal": "goal"},
            config={
                **CONFIG,
                "knowledge": {"use_shared": True, "use_global": True},
            },
            tool_registry=ToolRegistry({}),
        )
        self.assertEqual(enabled["global_knowledge_index"], "GLOBAL INDEX")
        self.assertEqual(enabled["shared_knowledge_index"], "SHARED INDEX")
        self.assertEqual(enabled["user_knowledge_index"], "USER INDEX")

    def test_authoritative_input_overrides_forged_tool_list_and_limits(self) -> None:
        _, root = _make_root(["alice"])
        shell = ToolDefinition(
            name="shell",
            description="执行系统命令",
            input_schema={"type": "object", "properties": {}},
            version="1",
            enabled=True,
            entrypoint="tool.py:run",
            source="test",
            directory=root / "plugins" / "shell",
            _callable=lambda **_: None,
        )
        payload = prepare_task_plan_input(
            root=root,
            user="alice",
            input_data={
                "action": "create",
                "goal": "test",
                "available_tools": [{"name": "execute_shell", "description": "forged"}],
                "max_steps": 999,
                "auto_accept": True,
                "context": "keep me",
            },
            config=CONFIG,
            tool_registry=ToolRegistry({"shell": shell}),
        )

        self.assertEqual(payload["available_tools"], [{
            "name": "shell",
            "description": "执行系统命令",
        }])
        self.assertEqual(payload["max_steps"], 10)
        self.assertFalse(payload["auto_accept"])
        self.assertEqual(payload["context"], "keep me")

    def test_dispatch_task_plan_uses_authoritative_payload_and_requires_wait(self) -> None:
        _, root = _make_root(["alice"])
        definition = SimpleNamespace(
            name="task_plan",
            timeout=600.0,
            execution="background_serial",
        )
        enriched = {
            "action": "create",
            "goal": "test",
            "available_tools": [{"name": "shell", "description": "执行系统命令"}],
        }
        result = AgentRunResult(
            agent="task_plan",
            data={"action": "skip", "message": "simple"},
            raw_text="",
            usage={"total_tokens": 1},
            model="mock",
        )
        context = {"root": str(root), "user": "alice", "source": "web", "session_id": "s1"}
        submitted: list[tuple[tuple[object, ...], dict[str, object]]] = []

        class RecordingScheduler:
            def submit(self, *args, **kwargs):
                submitted.append((args, kwargs))
                handler = kwargs.get("result_handler")
                if handler is not None:
                    handler(result)
                return "agent-task-test"

            def wait(self, task_id, timeout=None, **kwargs):
                del task_id, timeout
                del kwargs
                return result

        scheduler = RecordingScheduler()
        with (
            patch("plugins.subagent_dispatch.tool._public", return_value=[definition]),
            patch("plugins.subagent_dispatch.tool.load_config", return_value=CONFIG),
            patch(
                "plugins.subagent_dispatch.tool.prepare_main_agent_invocation",
                return_value=SimpleNamespace(payload=enriched, synchronous_only=True),
            ) as prepare,
            patch(
                "plugins.subagent_dispatch.tool.get_agent_scheduler",
                return_value=scheduler,
            ),
            patch("plugins.subagent_dispatch.tool.persist_main_agent_result", return_value=None) as persist,
        ):
            dispatched = dispatch_subagent(
                "call",
                agent="task_plan",
                input={"action": "create", "goal": "test"},
                context=context,
            )

        prepare.assert_called_once()
        self.assertEqual(submitted[0][0], ("task_plan", enriched))
        self.assertTrue(submitted[0][1]["allow_sync"])
        persist.assert_called_once()
        self.assertEqual(dispatched["data"]["action"], "skip")

        with (
            patch("plugins.subagent_dispatch.tool._public", return_value=[definition]),
            patch("plugins.subagent_dispatch.tool.load_config", return_value=CONFIG),
            patch(
                "plugins.subagent_dispatch.tool.prepare_main_agent_invocation",
                return_value=SimpleNamespace(payload=enriched, synchronous_only=True),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "必须同步调用"):
                dispatch_subagent(
                    "call",
                    agent="task_plan",
                    input={"action": "create", "goal": "test"},
                    wait=False,
                    context=context,
                )

    def test_edit_allows_failed_but_rejects_running_completed_and_cancelled(self) -> None:
        _, root = _make_root(["alice"])
        base = _make_plan([{
            "step_id": "step_1",
            "title": "A",
            "description": "A",
            "tool_name": None,
            "tool_arguments": {},
            "critical": True,
        }])
        for status in ("running", "completed", "cancelled"):
            with self.subTest(status=status):
                plan = {**base, "status": status}
                with self.assertRaisesRegex(PlanGenerationError, "只能编辑"):
                    edit_plan(
                        root=root,
                        user="alice",
                        plan=plan,
                        edit_request="change",
                        config=CONFIG,
                    )

        failed = {**base, "status": "failed"}
        response = {
            "action": "edit",
            "title": "修正后的失败计划",
            "description": "修正执行参数",
            "steps": failed["steps"],
        }
        with patch(
            "run.tasks.service.AgentRunner",
            self._capturing_runner(response, []),
        ):
            edited = edit_plan(
                root=root,
                user="alice",
                plan=failed,
                edit_request="修正失败原因",
                config=CONFIG,
            )
        self.assertEqual(edited["status"], "failed")
        self.assertEqual(edited["title"], "修正后的失败计划")

    def test_edit_preserves_completed_steps_and_passes_protection_summary(self) -> None:
        _, root = _make_root(["alice"])
        existing = normalize_plan(
            title="Existing",
            description="goal",
            user="alice",
            status="paused",
            reminder="old",
            steps=[
                {
                    "step_id": "step_1",
                    "title": "Done",
                    "description": "Finished work",
                    "status": "completed",
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                    "result": {"ok": True},
                    "finished_at": "2026-07-19T00:00:00+00:00",
                },
                {
                    "step_id": "step_2",
                    "title": "Pending",
                    "description": "Old work",
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                },
            ],
        )
        calls: list[dict] = []
        response = {
            "action": "edit",
            "title": "Existing",
            "description": "goal",
            "steps": [
                {"step_id": "step_1"},
                {
                    "step_id": "step_2",
                    "title": "Changed",
                    "description": "New work",
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                },
            ],
        }
        with patch(
            "run.tasks.service.AgentRunner",
            self._capturing_runner(response, calls),
        ):
            edited = edit_plan(
                root=root,
                user="alice",
                plan=existing,
                edit_request="change step two",
                config=CONFIG,
            )

        self.assertEqual(
            calls[0]["completed_steps"],
            [{"step_id": "step_1", "title": "Done"}],
        )
        self.assertEqual(edited["status"], "paused")
        self.assertEqual(edited["steps"][0], existing["steps"][0])
        self.assertEqual(edited["steps"][1]["title"], "Changed")
        self.assertEqual(
            edited["reminder"],
            "当前任务计划已修改，请让用户点击批准后执行",
        )

    def test_edit_rejects_completed_step_mutation(self) -> None:
        _, root = _make_root(["alice"])
        existing = normalize_plan(
            title="Existing",
            description="goal",
            user="alice",
            status="approved",
            steps=[{
                "step_id": "step_1",
                "title": "Done",
                "description": "Finished",
                "status": "completed",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            }],
        )
        response = {
            "action": "edit",
            "title": "Existing",
            "description": "goal",
            "steps": [{
                "step_id": "step_1",
                "title": "Tampered",
                "description": "Finished",
                "status": "completed",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            }],
        }
        with patch(
            "run.tasks.service.AgentRunner",
            self._capturing_runner(response, []),
        ):
            with self.assertRaisesRegex(PlanGenerationError, "不得修改"):
                edit_plan(
                    root=root,
                    user="alice",
                    plan=existing,
                    edit_request="tamper",
                    config=CONFIG,
                )

    def test_auto_accept_true_has_no_reminder(self) -> None:
        _, root = _make_root(["alice"])
        response = {
            "action": "create",
            "title": "Auto",
            "description": "goal",
            "steps": [{
                "step_id": "step_1",
                "title": "A",
                "description": "A",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            }],
        }
        config = {**CONFIG, "task_plan": {"auto_accept": True, "max_steps": 10}}
        with patch(
            "run.tasks.service.AgentRunner",
            self._capturing_runner(response, []),
        ):
            plan = generate_plan(root=root, user="alice", goal="goal", config=config)
        self.assertEqual(plan["reminder"], "")
        self.assertTrue(plan["auto_accept"])
        self.assertEqual(plan["status"], "approved")

    def test_task_plan_executor_normalizes_reminder(self) -> None:
        class Context:
            def run_model(self, input_data):
                return AgentRunResult(
                    agent="task_plan",
                    data={"action": input_data["action"], "steps": []},
                    raw_text="",
                    usage={},
                    model="mock",
                )

        created = execute_task_plan_agent(
            Context(),
            {"action": "create", "auto_accept": False},
        )
        self.assertEqual(
            created.data["reminder"],
            "当前任务计划已创建，请让用户点击批准后执行",
        )
        edited = execute_task_plan_agent(
            Context(),
            {"action": "edit", "auto_accept": True},
        )
        self.assertEqual(edited.data["reminder"], "")


if __name__ == "__main__":
    unittest.main()
