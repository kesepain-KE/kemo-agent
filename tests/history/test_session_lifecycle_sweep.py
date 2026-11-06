from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from run.cards import CardStore, IntegrationStore, session_key
from run.history import (
    close_session,
    find_record,
    reserve_session,
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
        (self.root / "config" / "global_config.yaml").write_text(
            json.dumps({"memory": {"extraction_mode": "compression_only"}}),
            encoding="utf-8",
        )
        user_dir = self.root / "users" / "alice"
        user_dir.mkdir(parents=True)
        (user_dir / "user_config.yaml").write_text("{}", encoding="utf-8")
        self.now = time.time()

    def seed(self, session_id: str, *, source: str = "web", rounds: int = 1) -> None:
        reserve_session(self.root, "alice", source, session_id)
        store = CardStore(self.root, "alice")
        for index in range(rounds):
            integration_id = f"i_{session_id}_{index}"
            store.append(source, session_id, integration_id, "user_text",
                         {"text": "old request"}, level="initial")
            store.append(source, session_id, integration_id, "agent_text",
                         {"text": "old response"}, level="final")
        old = datetime.fromtimestamp(self.now - 25 * 3600, timezone.utc).isoformat()
        with connection(self.root, "alice", write=True) as db:
            db.execute("UPDATE card_sessions SET updated_at=? WHERE session_key=?",
                       (old, session_key(source, session_id)))

    def test_idle_session_is_queued_and_closed(self) -> None:
        self.seed("stale")
        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(result["closed"], 1)
        record = find_record(self.root, "alice", "web", "stale")
        self.assertEqual(record["lifecycle"], "closed")
        self.assertEqual(record["memory_status"], "queued")
        self.assertEqual(record["memory_queue_reason"], "idle_session_sweep")
        self.assertEqual(record["summary_status"], "queued")
        self.assertEqual(record["summary_target_seq"], 2)

    def test_recent_session_is_not_closed(self) -> None:
        self.seed("recent")
        fresh = datetime.fromtimestamp(self.now - 3600, timezone.utc).isoformat()
        with connection(self.root, "alice", write=True) as db:
            db.execute("UPDATE card_sessions SET updated_at=? WHERE session_key=?",
                       (fresh, session_key("web", "recent")))
        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(result["closed"], 0)
        self.assertEqual(find_record(self.root, "alice", "web", "recent")["lifecycle"], "open")

    def test_closed_session_with_unqueued_rounds_is_recovered(self) -> None:
        self.seed("closed")
        close_session(self.root, "alice", "web", "closed")
        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(result["closed"], 0)
        self.assertEqual(result["requeued_closed"], 1)
        self.assertEqual(find_record(self.root, "alice", "web", "closed")["memory_status"], "queued")
        self.assertEqual(find_record(self.root, "alice", "web", "closed")["summary_status"], "queued")

    def test_closed_legacy_session_with_first_message_title_is_requeued(self) -> None:
        self.seed("legacy-title")
        CardStore(self.root, "alice").update_record(
            "web", "legacy-title", {"title": "旧版直接复制的用户首句"}
        )
        close_session(self.root, "alice", "web", "legacy-title")

        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)

        record = find_record(self.root, "alice", "web", "legacy-title")
        self.assertEqual(result["requeued_closed"], 1)
        self.assertEqual(record["summary_status"], "queued")
        self.assertEqual(record["summary_target_seq"], 2)

    def test_running_session_is_skipped(self) -> None:
        self.seed("running")
        IntegrationStore(CardStore(self.root, "alice"), "web", "running", "i_running").start()
        result = sweep_idle_sessions(self.root, "alice", now=self.now, idle_seconds=86400)
        self.assertEqual(result["closed"], 0)
        self.assertGreaterEqual(result["skipped_active"], 1)

    def test_online_web_lease_is_skipped(self) -> None:
        self.seed("leased")
        from run.history.lease_presence import update_presence
        update_presence(self.root, "alice", "web", "leased", "browser", 1, now=self.now)
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
