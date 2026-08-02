from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from run.log_store import LogStore


class LogStoreTests(unittest.TestCase):
    def test_message_route_state_round_trip_preserves_extension_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LogStore(Path(directory))
            state = {
                "schema_version": 1,
                "health": "healthy",
                "last_check": "2026-08-02T12:00:00+08:00",
                "last_message_at": None,
                "error": None,
                "latency_ms": 18,
                "messages_received_today": 4,
                "messages_sent_today": 3,
                "input_status": "running",
                "input_restart_count": 1,
                "input_last_restart_at": None,
                "input_error": None,
                "_bot_info": {"username": "demo"},
            }
            store.write_message_route_state(
                "telegram-demo", user="alice", platform="telegram", state=state
            )

            self.assertEqual(
                store.read_message_route_state("telegram-demo"), state
            )

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

    def test_message_records_are_idempotent_and_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LogStore(Path(directory))
            common = {
                "occurred_at": "2026-07-27 12:49:08",
                "user": "alice",
                "machine_id": "bridge",
                "platform": "telegram",
                "chat_type": "private",
                "chat_id": "chat-1",
                "source": "runtime/logs.sqlite3",
                "success": True,
            }
            entries = [
                {**common, "direction": "receive", "kind": "text", "content": "请查看会议纪要"},
                {**common, "direction": "receive", "kind": "file", "content": "meeting_notes.docx", "file_path": "message/out/bridge/files/meeting_notes.docx", "mime": "application/docx", "size": 8},
                {**common, "direction": "send", "kind": "text", "content": "会议纪要已收到。"},
                {**common, "direction": "send", "kind": "file", "content": "summary.pdf", "file_path": "users/alice/download/summary.pdf"},
            ]
            store.append_message_entries(entries)
            store.append_message_entries(entries)

            rows = store.list_messages("bridge")
            self.assertEqual(len(rows), 4)
            self.assertEqual(store.count_messages("bridge", date_prefix="2026-07-27"), 4)
            self.assertEqual(
                {(row["direction"], row["kind"]) for row in rows},
                {("receive", "text"), ("receive", "file"), ("send", "text"), ("send", "file")},
            )
