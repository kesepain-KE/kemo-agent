from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from run.infra import LogStore
from run.scheduler import CronStore, normalize_task
from run.tasks import PlanStore, normalize_plan
from web.app import create_app
from web.errors import InvalidRequestError
from web.service import WebRunService


class TaskCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="kemo-task-cleanup-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for user in ("alice", "bob"):
            (self.root / "users" / user).mkdir(parents=True)
        self.service = WebRunService(self.root)
        self.app = create_app(service=self.service)
        self.now = datetime.now(timezone.utc)

    def plan(self, *, status="completed", user="alice", source="web", session="s1",
             step_status="completed"):
        return PlanStore(self.root, user).create(normalize_plan(
            title=f"Plan {status}", description="Fixture only", user=user,
            source=source, session_id=session, status=status,
            steps=[{"step_id": "step_1", "title": "Fixture step",
                    "description": "Fixture only", "status": step_status,
                    "finished_at": self.now.isoformat() if step_status in {"completed", "failed"} else "",
                    "result": {"ok": True}}],
        ))

    def cron(self, *, status="completed", user="alice"):
        return CronStore(self.root, user).create(normalize_task(
            title=f"Cron {status}", prompt="Fixture only", user=user, type="once",
            status=status, next_run_at="" if status in {"completed", "cancelled"}
            else "2030-01-01T09:00:00+08:00",
        ))

    def logs(self, count=1, *, status="completed", user="alice", task_id="cron_12345678",
             offset=0):
        LogStore(self.root, retention_days=0).append_cron_records([
            {"user": user, "task_id": task_id, "status": status,
             "executed_at": (self.now + timedelta(seconds=offset + i)).isoformat(),
             "duration_ms": 10, "result": {"index": offset + i}}
            for i in range(count)
        ])

    def preview(self, category, *, user="alice", session="s1"):
        return self.service.preview_task_cleanup(user, category, session_id=session)

    def cleanup(self, preview, *, user="alice", session="s1"):
        return self.service.cleanup_completed_tasks(user, {
            "category": preview["category"], "source": "web", "session_id": session,
            "items": [{"id": item["id"], "version": item["version"]}
                      for item in preview["items"]],
        })

    def request(self, method, path, payload=None):
        async def invoke():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                return await client.request(method, path, json=payload)
        return asyncio.run(invoke())

    def test_plan_preview_is_read_only_and_cleanup_covers_all_pages(self):
        completed = [self.plan() for _ in range(8)]
        preserved = [self.plan(status=status) for status in
                     ("pending", "approved", "running", "paused", "failed", "cancelled")]
        cron = self.cron()
        preview = self.preview("plans")
        self.assertEqual(preview["count"], 8)
        self.assertEqual(len(PlanStore(self.root, "alice").list_plans()), 14)
        self.assertEqual(set(preview["items"][0]), {"id", "title", "version"})
        result = self.cleanup(preview)
        self.assertEqual(result["removed_count"], len(completed))
        self.assertEqual({p["plan_id"] for p in PlanStore(self.root, "alice").list_plans()},
                         {p["plan_id"] for p in preserved})
        self.assertEqual(CronStore(self.root, "alice").read(cron["task_id"]), cron)
        self.assertEqual(self.cleanup(preview)["removed_count"], 0)

    def test_plan_source_session_and_user_boundaries(self):
        own = self.plan()
        self.plan(user="bob")
        other_session = self.plan(session="s2")
        app = self.plan(source="app")
        cli = self.plan(source="cli")
        preview = self.preview("plans")
        self.assertEqual([item["id"] for item in preview["items"]], [own["plan_id"]])
        self.assertEqual(self.cleanup(preview, user="bob")["removed_count"], 0)
        self.assertEqual(self.cleanup(preview)["removed_count"], 1)
        self.assertEqual({p["plan_id"] for p in PlanStore(self.root, "alice").list_plans()},
                         {p["plan_id"] for p in (other_session, app, cli)})
        self.assertEqual(len(PlanStore(self.root, "bob").list_plans()), 1)

    def test_cron_cleanup_only_completed_and_never_new_completions(self):
        other_user = self.cron(user="bob")
        for _ in range(8):
            self.cron()
        preserved = [self.cron(status=status) for status in
                     ("enabled", "running", "paused", "failed", "cancelled")]
        preview = self.preview("crons")
        new = self.cron()
        self.assertEqual(preview["count"], 8)
        self.assertEqual(self.cleanup(preview)["removed_count"], 8)
        self.assertEqual({c["task_id"] for c in CronStore(self.root, "alice").list_tasks()},
                         {c["task_id"] for c in [*preserved, new]})
        self.assertEqual(CronStore(self.root, "bob").read(other_user["task_id"]), other_user)

    def test_changed_cron_and_plan_snapshots_are_skipped(self):
        plan = self.plan()
        plan_preview = self.preview("plans")
        PlanStore(self.root, "alice").update(plan["plan_id"], lambda p: {**p, "reminder": "changed"})
        self.assertEqual(self.cleanup(plan_preview)["skipped"], [plan["plan_id"]])
        cron = self.cron()
        cron_preview = self.preview("crons")
        CronStore(self.root, "alice").update(cron["task_id"], lambda c: {**c, "title": "Changed"})
        self.assertEqual(self.cleanup(cron_preview)["skipped"], [cron["task_id"]])

    def test_plan_store_rechecks_revision_at_delete_boundary(self):
        plan = self.plan()
        preview = self.preview("plans")
        original = PlanStore.delete

        def race(store, plan_id, **kwargs):
            store.update(plan_id, lambda p: {**p, "reminder": "raced"})
            return original(store, plan_id, **kwargs)

        with patch.object(PlanStore, "delete", race):
            result = self.cleanup(preview)
        self.assertEqual(result["removed_count"], 0)
        self.assertEqual(result["skipped"], [plan["plan_id"]])
        self.assertEqual(PlanStore(self.root, "alice").read(plan["plan_id"])["reminder"], "raced")

    def test_cron_store_rechecks_status_at_delete_boundary(self):
        cron = self.cron()
        preview = self.preview("crons")
        original = CronStore.delete

        def race(store, task_id, **kwargs):
            store.update(task_id, lambda c: {**c, "status": "enabled",
                                           "next_run_at": "2030-01-01T09:00:00+08:00"})
            return original(store, task_id, **kwargs)

        with patch.object(CronStore, "delete", race):
            result = self.cleanup(preview)
        self.assertEqual(result["removed_count"], 0)
        self.assertEqual(result["skipped"], [cron["task_id"]])
        self.assertEqual(CronStore(self.root, "alice").read(cron["task_id"])["status"], "enabled")

    def test_execution_cleanup_is_durable_and_preserves_sources_and_new_runs(self):
        plan = self.plan()
        self.plan(status="failed", step_status="failed")
        self.logs(105)
        # Older failures must reappear after filtering BEFORE the 100-row limit.
        self.logs(status="failed", offset=-200)
        self.logs(user="bob")
        preview = self.preview("executions")
        self.assertEqual(preview["count"], 106)
        self.assertEqual(self.cleanup(preview)["removed_count"], 106)
        service = WebRunService(self.root)
        remaining = service.tasks("alice", session_id="s1")["executions"]
        self.assertEqual(len(remaining), 2)
        self.assertTrue(all(r["status"] == "failed" for r in remaining))
        self.assertEqual(len(LogStore(self.root).list_cron("alice")), 106)
        self.assertEqual(PlanStore(self.root, "alice").read(plan["plan_id"])["steps"][0]["result"],
                         {"ok": True})
        self.assertEqual(len(service.tasks("bob", session_id="s1")["executions"]), 1)
        self.assertEqual(service.preview_task_cleanup("alice", "executions", session_id="s1")["count"], 0)
        self.logs(offset=201)
        self.assertEqual(len(service.tasks("alice", session_id="s1")["executions"]), 3)
        self.assertEqual(self.cleanup(preview)["removed_count"], 0)

    def test_execution_preview_respects_plan_source_session_and_user(self):
        plan = self.plan()
        self.plan(session="s2")
        self.plan(source="app")
        self.plan(user="bob")
        preview = self.preview("executions")
        self.assertEqual([r["id"] for r in preview["items"]],
                         [f"plan_step:{plan['plan_id']}:step_1"])
        self.assertEqual(self.cleanup(preview)["removed_count"], 1)
        self.assertEqual(len(self.service.tasks("alice", session_id="s2")["executions"]), 1)
        self.assertEqual(len(self.service.tasks("alice", source="app", session_id="s1")["executions"]), 1)
        self.assertEqual(len(self.service.tasks("bob", session_id="s1")["executions"]), 1)

    def test_execution_cleanup_keeps_cron_definitions(self):
        cron = self.cron()
        self.logs(task_id=cron["task_id"])
        self.assertEqual(self.cleanup(self.preview("executions"))["removed_count"], 1)
        self.assertEqual(CronStore(self.root, "alice").read(cron["task_id"]), cron)

    def test_cron_definition_cleanup_preserves_diagnostic_execution_logs(self):
        cron = self.cron()
        self.logs(task_id=cron["task_id"])
        self.assertEqual(self.cleanup(self.preview("crons"))["removed_count"], 1)
        self.assertEqual(len(LogStore(self.root).list_cron("alice")), 1)
        self.assertEqual(len(self.service.tasks("alice", session_id="s1")["executions"]), 1)

    def test_system_tasks_and_per_user_system_records_are_excluded(self):
        task = CronStore(self.root, "__system__", system=True).create(normalize_task(
            task_id="memory_promotion", title="System fixture", prompt="", user="",
            type="recurring", interval_seconds=30, exec_mode="system", action="memory_promotion",
        ))
        self.logs(task_id=task["task_id"])
        self.logs(user="__system__", task_id=task["task_id"])
        self.assertEqual(self.preview("crons")["count"], 0)
        self.assertEqual(self.preview("executions")["count"], 0)
        self.assertEqual(self.service.tasks("alice")["executions"], [])
        self.assertEqual(len(LogStore(self.root).list_cron("alice")), 1)
        self.assertEqual(CronStore(self.root, "__system__", system=True).read(task["task_id"]), task)

    def test_partial_failure_continues_with_remaining_items(self):
        first, second = self.cron(), self.cron()
        preview = self.preview("crons")
        original = CronStore.delete

        def fail_one(store, task_id, **kwargs):
            if task_id == first["task_id"]:
                raise OSError("fixture write failed")
            return original(store, task_id, **kwargs)

        with patch.object(CronStore, "delete", fail_one):
            result = self.cleanup(preview)
        self.assertEqual(result["removed"], [second["task_id"]])
        self.assertEqual(result["errors"], [{"id": first["task_id"], "message": "fixture write failed"}])

    def test_http_contract_and_malformed_input(self):
        self.cron()
        response = self.request("GET", "/api/users/alice/tasks/cleanup?category=crons")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["count"], 1)
        base = {"category": "crons", "items": []}
        invalid = [
            {}, {**base, "typo": True}, {**base, "category": []},
            {**base, "category": "all"}, {**base, "source": []},
            {**base, "source": {}}, {**base, "source": "cli"},
            {**base, "session_id": []}, {**base, "session_id": False},
            {**base, "session_id": "\n"}, {**base, "items": None},
            {**base, "items": ["cron_12345678"]},
            {**base, "items": [{"id": "x"}]},
            {**base, "items": [{"id": [], "version": "x"}]},
            {**base, "items": [{"id": "x", "version": {}, "title": "unexpected"}]},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                result = self.request("POST", "/api/users/alice/tasks/cleanup", payload)
                self.assertEqual(result.status_code, 400, result.text)
        self.assertEqual(self.request("POST", "/api/users/alice/tasks/cleanup", base).status_code, 200)
        self.assertEqual(self.request("GET", "/api/users/alice/tasks/cleanup?category=all").status_code, 400)
        self.assertEqual(self.request("GET", "/api/users/missing/tasks/cleanup?category=crons").status_code, 404)
        self.assertEqual(len(CronStore(self.root, "alice").list_tasks()), 1)
        with self.assertRaises(InvalidRequestError):
            self.service.cleanup_completed_tasks("alice", [])
