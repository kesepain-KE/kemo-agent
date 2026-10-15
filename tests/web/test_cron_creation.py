from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from cron.scheduler import CronScheduler
from run.scheduler import CronStore, normalize_task
from web.app import create_app
from web.service import WebRunService


class CronCreationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="kemo-cron-create-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for user in ("alice", "bob"):
            (self.root / "users" / user).mkdir(parents=True)
        self.app = create_app(service=WebRunService(self.root))
        self.base = {"title": "Local test", "prompt": "Fixture only", "type": "daily", "time": "03:30"}

    def request(self, method: str, path: str, payload=None):
        async def invoke():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                return await client.request(method, path, json=payload)
        return asyncio.run(invoke())

    def create(self, payload):
        return self.request("POST", "/api/users/alice/tasks/crons", payload)

    def test_all_schedule_types_persist_and_are_user_scoped(self) -> None:
        for schedule in (
            {"type": "once", "next_run_at": "2030-01-01T09:00:00+08:00"},
            {"type": "daily", "time": "03:30"},
            {"type": "weekly", "times": ["09:00", "20:00"], "weekdays": [5, 1],
             "start_date": "2030-01-01", "end_date": "2030-12-31", "max_runs": 4},
            {"type": "monthly", "time": "09:00", "month_days": [1, 1]},
            {"type": "recurring", "interval_seconds": 60},
        ):
            with self.subTest(schedule=schedule):
                response = self.create({"title": "Local test", "prompt": "Fixture only", **schedule})
                self.assertEqual(response.status_code, 200, response.text)
                task_id = response.json()["cron_task"]["task_id"]
                stored = CronStore(self.root, "alice").read(task_id)
                self.assertEqual(stored["user"], "alice")
                self.assertEqual(stored["status"], "enabled")
                self.assertEqual(stored["exec_mode"], "agent")
                self.assertEqual(datetime.fromisoformat(stored["next_run_at"]).utcoffset().total_seconds(), 28800)
                if schedule["type"] == "weekly":
                    self.assertEqual(stored["weekdays"], [1, 5])
                    self.assertEqual(stored["times"], ["09:00", "20:00"])
                if schedule["type"] == "monthly":
                    self.assertEqual(stored["month_days"], [1])
                detail = self.request("GET", f"/api/users/alice/tasks/crons/{task_id}")
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(detail.json()["cron_task"]["prompt"], "Fixture only")
                self.assertEqual(self.request("GET", f"/api/users/bob/tasks/crons/{task_id}").status_code, 404)

    def test_invalid_input_returns_400_without_creating_tasks(self) -> None:
        invalid = [
            {"type": {"value": "daily"}}, {"type": ["daily"]}, {"type": None},
            {"status": {"value": "enabled"}}, {"status": ["enabled"]}, {"status": True},
            *({"status": status} for status in ("running", "completed", "failed", "cancelled")),
            {"prompt": ""}, {"prompt": "  "}, {"prompt": {}}, {"title": []},
            {"time": "03:30:00"}, {"time": "25:00"}, {"time": {}},
            {"times": "09:00"}, {"times": []}, {"times": [["09:00"]]},
            {"times": ["09:00"]},  # simultaneous time and times
            {"weekdays": [1]}, {"month_days": [1]}, {"interval_seconds": 60},
            {"start_date": []}, {"end_date": True}, {"max_runs": True},
            {"max_runs": 0}, {"max_runs": 1.5},
            {"user": "bob"}, {"exec_mode": "system"}, {"task_id": "cron_12345678"},
            {"schedule": {"type": "daily"}}, {"typo": True},
        ]
        for change in invalid:
            with self.subTest(change=change):
                response = self.create({**self.base, **change})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertTrue(response.json()["error"]["message"])
        for schedule in (
            {"type": "weekly", "time": "09:00", "weekdays": []},
            {"type": "weekly", "time": "09:00", "weekdays": [True]},
            {"type": "weekly", "time": "09:00", "weekdays": [[1]]},
            {"type": "weekly", "time": "09:00", "weekdays": "1"},
            {"type": "monthly", "time": "09:00", "month_days": [32]},
            {"type": "monthly", "time": "09:00", "month_days": [{"day": 1}]},
            {"type": "daily", "times": [True]},
            {"type": "daily", "times": ["09:00"] * 25},
            {"type": "recurring", "interval_seconds": 59},
            {"type": "recurring", "interval_seconds": True},
        ):
            response = self.create({"title": "Local test", "prompt": "Fixture only", **schedule})
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(CronStore(self.root, "alice").list_tasks(), [])

    def test_once_requires_timezone_and_normalizes_explicit_offsets(self) -> None:
        once = {"title": "Once", "prompt": "Fixture only", "type": "once"}
        for raw in ("2030-01-01T09:00:00", "2030-01-01", "invalid", "", None, {}):
            with self.subTest(raw=raw):
                response = self.create({**once, "next_run_at": raw})
                self.assertEqual(response.status_code, 400, response.text)
        for raw, expected in (
            ("2030-01-01T09:00:00+08:00", "2030-01-01T09:00:00+08:00"),
            ("2030-01-01T01:00:00Z", "2030-01-01T09:00:00+08:00"),
            ("2030-01-01T09:00:00-05:00", "2030-01-01T22:00:00+08:00"),
        ):
            response = self.create({**once, "next_run_at": raw})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["cron_task"]["next_run_at"], expected)

    def test_updates_preserve_terminal_state_and_allow_pause_resume(self) -> None:
        created = self.create({**self.base, "status": "paused"})
        self.assertEqual(created.status_code, 200, created.text)
        task_id = created.json()["cron_task"]["task_id"]
        endpoint = f"/api/users/alice/tasks/crons/{task_id}"
        for status in ("enabled", "paused"):
            response = self.request("PUT", endpoint, {"status": status})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["cron_task"]["status"], status)
        for change in ({"status": "completed"}, {"status": ["enabled"]}, {"type": ["once"]}):
            self.assertEqual(self.request("PUT", endpoint, change).status_code, 400)
        response = self.request("PUT", endpoint, {"type": "once", "next_run_at": "2030-01-01T09:00:00"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(CronStore(self.root, "alice").read(task_id)["type"], "daily")
        response = self.request("PUT", endpoint, {"type": "weekly", "weekdays": [1, 5], "times": ["12:30", "20:00"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("time", CronStore(self.root, "alice").read(task_id))
        completed = CronStore(self.root, "alice").create(normalize_task(
            title="Completed", prompt="Fixture only", user="alice",
            type="once", status="completed", next_run_at="",
        ))
        endpoint = f"/api/users/alice/tasks/crons/{completed['task_id']}"
        response = self.request("PUT", endpoint, {"title": "Edited", "status": "completed"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cron_task"]["status"], "completed")
        self.assertEqual(response.json()["cron_task"]["next_run_at"], "")

    def test_api_created_due_task_reaches_scheduler_without_provider_calls(self) -> None:
        response = self.create({
            "title": "Due fixture", "prompt": "Fixture only", "type": "once",
            "next_run_at": "2020-01-01T09:00:00+08:00",
        })
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()["cron_task"]["task_id"]
        with patch("cron.scheduler.execute_cron_task", return_value={"status": "completed"}) as execute:
            self.assertEqual(CronScheduler(self.root).scan_once(), 1)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.kwargs["task_id"], task_id)
        self.assertEqual(execute.call_args.kwargs["user"], "alice")
