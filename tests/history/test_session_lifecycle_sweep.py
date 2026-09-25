from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from run.history import (
    commit_terminal_windows,
    empty_window,
    find_record,
    reserve_session,
    runtime_window_path,
    update_run_state,
    touch_session_lease,
)
from run.history.store import connection
from run.scheduler.session_sweep import sweep_idle_sessions


class SessionLifecycleSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "global_config.json").write_text(
            json.dumps({"memory": {"extraction_mode": "compression_only"}}),
            encoding="utf-8",
        )
        user_dir = self.root / "users" / "alice"
        user_dir.mkdir(parents=True)
        (user_dir / "user_config.json").write_text("{}", encoding="utf-8")
        self.now = time.time()

    def seed(self, session_id: str, *, source: str = "web", rounds: int = 1) -> None:
        reserve_session(self.root, "alice", source, session_id)
        window = empty_window("alice", source, session_id)
        window["text"]["messages"] = [
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old response"},
        ]
        window["data"]["rounds"] = rounds
        path = self.root / "users" / "alice" / "history" / f"conv_{session_id}"
        commit_terminal_windows(
            path,
            window,
            runtime_window_path(path),
            copy.deepcopy(window),
        )
        old = datetime.fromtimestamp(self.now - 25 * 3600, timezone.utc).isoformat()
        with connection(self.root, "alice", write=True) as db:
            row = db.execute(
                "SELECT record_json FROM history_sessions WHERE source=? AND session_id=?",
                (source, session_id),
            ).fetchone()
            record = json.loads(row["record_json"])
            record["updated_at"] = old
            db.execute(
                "UPDATE history_sessions SET updated_at=?, record_json=? WHERE source=? AND session_id=?",
                (old, json.dumps(record, ensure_ascii=False), source, session_id),
            )

    def test_idle_session_is_queued_and_closed(self) -> None:
        self.seed("stale")
        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(result["closed"], 1)
        record = find_record(self.root, "alice", "web", "stale")
        self.assertEqual(record["lifecycle"], "closed")
        self.assertEqual(record["memory_status"], "queued")
        self.assertEqual(record["memory_queue_reason"], "idle_session_sweep")

    def test_recent_session_is_not_closed(self) -> None:
        self.seed("recent")
        fresh = datetime.fromtimestamp(self.now - 3600, timezone.utc).isoformat()
        with connection(self.root, "alice", write=True) as db:
            row = db.execute(
                "SELECT record_json FROM history_sessions WHERE session_id=?",
                ("recent",),
            ).fetchone()
            record = json.loads(row["record_json"])
            record["updated_at"] = fresh
            db.execute(
                "UPDATE history_sessions SET updated_at=?, record_json=? WHERE session_id=?",
                (fresh, json.dumps(record, ensure_ascii=False), "recent"),
            )
        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(result["closed"], 0)
        self.assertEqual(find_record(self.root, "alice", "web", "recent")["lifecycle"], "open")

    def test_closed_session_with_unqueued_rounds_is_recovered(self) -> None:
        self.seed("closed")
        with connection(self.root, "alice", write=True) as db:
            row = db.execute(
                "SELECT record_json FROM history_sessions WHERE session_id=?",
                ("closed",),
            ).fetchone()
            record = json.loads(row["record_json"])
            record["lifecycle"] = "closed"
            db.execute(
                "UPDATE history_sessions SET lifecycle='closed', record_json=? WHERE session_id=?",
                (json.dumps(record, ensure_ascii=False), "closed"),
            )
        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(result["closed"], 0)
        self.assertEqual(result["requeued_closed"], 1)
        self.assertEqual(find_record(self.root, "alice", "web", "closed")["memory_status"], "queued")

    def test_running_session_is_skipped(self) -> None:
        self.seed("running")
        update_run_state(self.root, "alice", "web", "running", run_state="running")
        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(result["closed"], 0)
        self.assertGreaterEqual(result["skipped_active"], 1)

    def test_online_web_lease_is_skipped(self) -> None:
        self.seed("leased")
        with connection(self.root, "alice", write=True) as db:
            db.execute(
                "INSERT INTO history_web_leases(session_id, client_id, expires_at) VALUES(?, ?, ?)",
                ("leased", "browser", self.now + 3600),
            )
        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(result["closed"], 0)
        self.assertEqual(find_record(self.root, "alice", "web", "leased")["lifecycle"], "open")

    def test_online_app_lease_is_skipped(self) -> None:
        self.seed("app-live", source="app")
        self.assertTrue(touch_session_lease(
            self.root, "alice", "app", "app-live", "app-device", now=self.now,
        ))
        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(result["closed"], 0)
        self.assertEqual(find_record(self.root, "alice", "app", "app-live")["lifecycle"], "open")

    def test_one_failure_does_not_stop_following_sessions(self) -> None:
        self.seed("first")
        self.seed("second")
        original = __import__("run.scheduler.session_sweep", fromlist=["queue_memory_extraction"]).queue_memory_extraction

        def fail_first(root, user, source, session_id, **kwargs):
            if session_id == "first":
                raise RuntimeError("injected")
            return original(root, user, source, session_id, **kwargs)

        with patch("run.scheduler.session_sweep.queue_memory_extraction", side_effect=fail_first):
            result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertTrue(result["errors"])
        self.assertEqual(find_record(self.root, "alice", "web", "first")["lifecycle"], "closed")
        self.assertEqual(find_record(self.root, "alice", "web", "second")["lifecycle"], "closed")
        recovered = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(recovered["requeued_closed"], 1)
        self.assertEqual(find_record(self.root, "alice", "web", "first")["memory_status"], "queued")


if __name__ == "__main__":
    unittest.main()
