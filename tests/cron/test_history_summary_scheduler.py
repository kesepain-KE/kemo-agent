from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from run.history import HistorySummaryScheduler


class HistorySummarySchedulerTests(unittest.TestCase):
    def make_root(self, *users: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for user in users:
            (root / "users" / user).mkdir(parents=True)
        return temporary, root

    def test_scan_is_bounded_and_rotates_between_users(self) -> None:
        _, root = self.make_root("alice", "bob", "carol")
        calls: list[str] = []

        def process(user: str):
            calls.append(user)
            return {"claimed": 1, "processed": [{"session_id": user}], "failed": []}

        scheduler = HistorySummaryScheduler(
            root, processor=process, max_jobs_per_cycle=1
        )
        first = scheduler.scan_once()
        second = scheduler.scan_once()

        self.assertEqual(calls, ["alice", "bob"])
        self.assertEqual(list(first), ["alice"])
        self.assertEqual(list(second), ["bob"])

    def test_wake_runs_worker_without_waiting_for_poll_interval(self) -> None:
        _, root = self.make_root("alice")
        called = threading.Event()
        calls = 0

        def process(_user: str):
            nonlocal calls
            calls += 1
            if calls >= 2:
                called.set()
            return {"claimed": 0, "processed": [], "failed": []}

        scheduler = HistorySummaryScheduler(root, processor=process, poll_interval=60)
        scheduler.start()
        try:
            deadline = time.time() + 1
            while calls < 1 and time.time() < deadline:
                time.sleep(0.01)
            scheduler.wake()
            self.assertTrue(called.wait(1))
            self.assertTrue(scheduler.status()["running"])
        finally:
            scheduler.stop(timeout=1)
        self.assertFalse(scheduler.running)


if __name__ == "__main__":
    unittest.main()
