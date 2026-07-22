from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from run.maintenance import MaintenanceScheduler
from run.memory import MemoryStore, normalize_memory_filename


class MaintenanceSchedulerTests(unittest.TestCase):
    def test_force_scan_promotes_expired_half_year_memory_to_permanent(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice").mkdir(parents=True)
        config = {
            "schema_version": 1,
            "memory": {
                "tiers": {
                    "seven_days": {
                        "days": 7,
                        "upgrade_threshold": 3,
                        "next": "one_month",
                    },
                    "one_month": {
                        "days": 30,
                        "upgrade_threshold": 10,
                        "next": "half_year",
                    },
                    "half_year": {
                        "days": 180,
                        "upgrade_threshold": 60,
                        "next": None,
                    },
                }
            },
            "agents": {
                "important_memory_review_hours": 3,
                "daily_memory_review_time": "02:00",
            },
        }
        (root / "config" / "global_config.json").write_text(
            json.dumps(config),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1}),
            "utf-8",
        )
        store = MemoryStore(root, "alice", config)
        filename = normalize_memory_filename("durable preference")
        path = store.fragment_path("half_year", filename)
        path.parent.mkdir(parents=True)
        path.write_text("durable preference", "utf-8")
        now = datetime(2026, 7, 19, 3, tzinfo=timezone.utc)
        store.write_index(
            "half_year",
            {
                filename: {
                    "weight": 60,
                    "updated_at": (now - timedelta(days=181)).isoformat(),
                    "last_weight_date": None,
                    "expires_at": (now - timedelta(seconds=1)).isoformat(),
                }
            },
        )

        result = MaintenanceScheduler(root).scan_once(now=now, force=True)

        self.assertNotIn("_perception", result)
        self.assertNotIn("memory_lifecycle", result["alice"])
        self.assertFalse(store.fragment_path("permanent", filename).is_file())
        self.assertIn(filename, store.load_index("half_year"))
        self.assertNotIn("important_memory", result["alice"])
        self.assertNotIn("daily_memory_review", result["alice"])


if __name__ == "__main__":
    unittest.main()
