from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from run.history import commit_window, empty_window, load_window, queue_memory_extraction
from run.history_index import close_session, find_record
from run.maintenance import MaintenanceScheduler
from run.memory import MemoryStore, normalize_memory_filename


class MaintenanceSchedulerTests(unittest.TestCase):
    def test_pending_committed_round_is_recovered_once(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "memory": {
                        "extraction_mode": "background",
                        "recovery_max_rounds_per_scan": 2,
                    }
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1}),
            "utf-8",
        )
        archive = root / "users" / "alice" / "history" / "conv_pending"
        window = empty_window("alice", "web", "conv_pending")
        window["text"]["messages"] = [
            {"role": "user", "content": "请记住设备名"},
            {"role": "assistant", "content": "设备名是 J1900"},
        ]
        window["think"]["rounds"] = [{"round": 1, "content": "提取设备名"}]
        window["tool"]["rounds"] = [{"round": 1, "calls": []}]
        window["data"].update(
            {
                "rounds": 1,
                "memory_processed_round": 0,
                "memory_status": "pending",
            }
        )
        commit_window(archive, window)
        observed: dict[str, object] = {}

        def extract(**kwargs):
            observed.update(kwargs)
            return {"status": "completed", "candidate_count": 1, "error": None}

        with patch("run.maintenance._extract_round_memory", side_effect=extract):
            result = MaintenanceScheduler(root).scan_once()

        self.assertEqual(result["alice"]["memory_recovery"]["claimed"], 1)
        self.assertEqual(observed["round_number"], 1)
        self.assertEqual(observed["prompt"], "请记住设备名")
        self.assertEqual(load_window(archive)["data"]["memory_processed_round"], 1)
        record = find_record(root, "alice", "web", "conv_pending")
        self.assertEqual(record["memory_processed_round"], 1)
        self.assertEqual(record["memory_status"], "completed")

    def test_compression_only_does_not_claim_untouched_pending_round(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "config" / "global_config.json").write_text(
            json.dumps({"memory": {"extraction_mode": "compression_only"}}),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1}),
            "utf-8",
        )
        archive = root / "users" / "alice" / "history" / "conv_deferred"
        window = empty_window("alice", "web", "conv_deferred")
        window["text"]["messages"] = [
            {"role": "user", "content": "普通问题"},
            {"role": "assistant", "content": "普通回答"},
        ]
        window["data"].update(
            {
                "rounds": 1,
                "memory_processed_round": 0,
                # Also protect legacy pending records created before the mode
                # migration; the policy, not only the status, controls claims.
                "memory_status": "pending",
            }
        )
        commit_window(archive, window)

        with patch("run.maintenance._extract_round_memory") as extract:
            result = MaintenanceScheduler(root).scan_once()

        recovery = result["alice"]["memory_recovery"]
        self.assertEqual(recovery["mode"], "compression_only")
        self.assertEqual(recovery["claimed"], 0)
        extract.assert_not_called()
        self.assertEqual(
            find_record(root, "alice", "web", "conv_deferred")["memory_status"],
            "pending",
        )

    def test_compression_only_claims_closed_session_explicitly_queued_for_save(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "memory": {
                        "extraction_mode": "compression_only",
                        "recovery_max_rounds_per_scan": 2,
                    }
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1}),
            "utf-8",
        )
        archive = root / "users" / "alice" / "history" / "conv_saved"
        window = empty_window("alice", "web", "conv_saved")
        window["text"]["messages"] = [
            {"role": "user", "content": "需要后台提取"},
            {"role": "assistant", "content": "已保存对话"},
        ]
        window["data"].update(
            {
                "rounds": 1,
                "memory_processed_round": 0,
                "memory_status": "deferred",
            }
        )
        commit_window(archive, window)
        queued = queue_memory_extraction(root, "alice", "web", "conv_saved")
        close_session(root, "alice", "web", "conv_saved")

        with patch(
            "run.maintenance._extract_round_memory",
            return_value={"status": "completed", "candidate_count": 1, "error": None},
        ) as extract:
            result = MaintenanceScheduler(root).scan_once()

        self.assertEqual(queued["status"], "queued")
        self.assertEqual(result["alice"]["memory_recovery"]["claimed"], 1)
        extract.assert_called_once()
        self.assertEqual(load_window(archive)["data"]["memory_status"], "completed")
        self.assertEqual(
            find_record(root, "alice", "web", "conv_saved")["memory_status"],
            "completed",
        )

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
