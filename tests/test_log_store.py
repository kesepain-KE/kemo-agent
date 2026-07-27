from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from run.log_store import LogStore


class LogStoreTests(unittest.TestCase):
    def test_cron_records_are_idempotent_and_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LogStore(Path(directory))
            record = {
                "executed_at": "2026-07-27T12:00:00+08:00",
                "user": "alice",
                "task_id": "memory_promotion",
                "status": "success",
                "duration_ms": 12,
                "result": {"status": "completed"},
            }
            store.append_cron(record)
            store.append_cron(record)

            rows = store.list_cron("alice")

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["task_id"], "memory_promotion")
            self.assertEqual(rows[0]["result"], {"status": "completed"})

    def test_cron_legacy_jsonl_migration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_dir = root / "cron" / "task_cron_system" / "log"
            log_dir.mkdir(parents=True)
            path = log_dir / "2026-07-27.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "executed_at": "2026-07-27T12:00:00+08:00",
                        "user": "alice",
                        "task_id": "expand_update",
                        "status": "success",
                        "duration_ms": 4,
                        "result": {},
                    }
                )
                + "\n",
                "utf-8",
            )
            store = LogStore(root)

            store.migrate_cron_logs(log_dir)
            store.migrate_cron_logs(log_dir)

            self.assertEqual(len(store.list_cron("alice")), 1)

    def test_message_legacy_markdown_migration_preserves_four_log_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_dir = root / "message" / "out" / "bridge" / "log"
            log_dir.mkdir(parents=True)
            (log_dir / "2026-07-27.md").write_text(
                "## 2026-07-27 12:49:08 | private | chat-1\n\n"
                "**入站**：请查看会议纪要\n"
                "  - 附件：meeting_notes.docx (application/docx, 8 bytes)\n\n"
                "**出站**：会议纪要已收到。\n"
                "  - 出站附件：summary.pdf (users/alice/download/summary.pdf)\n\n---\n",
                "utf-8",
            )
            store = LogStore(root)

            store.migrate_message_logs(
                log_dir,
                machine_id="bridge",
                user="alice",
                platform="telegram",
                files_root="message/out/bridge/files",
            )
            store.migrate_message_logs(
                log_dir,
                machine_id="bridge",
                user="alice",
                platform="telegram",
                files_root="message/out/bridge/files",
            )

            rows = store.list_messages("bridge")
            self.assertEqual(len(rows), 4)
            self.assertEqual(store.count_messages("bridge", date_prefix="2026-07-27"), 4)
            self.assertEqual(
                {(row["direction"], row["kind"]) for row in rows},
                {("receive", "text"), ("receive", "file"), ("send", "text"), ("send", "file")},
            )

