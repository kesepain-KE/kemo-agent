from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from run.infra import LogStore


class LogStoreTests(unittest.TestCase):
    def test_existing_message_database_upgrades_without_losing_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "runtime" / "logs.sqlite3"
            path.parent.mkdir()
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript("""
                    CREATE TABLE message_route_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_key TEXT NOT NULL UNIQUE,
                        occurred_at TEXT NOT NULL, occurred_at_ms INTEGER NOT NULL,
                        user TEXT NOT NULL, machine_id TEXT NOT NULL, platform TEXT NOT NULL,
                        chat_type TEXT NOT NULL, chat_id TEXT NOT NULL, direction TEXT NOT NULL,
                        kind TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
                        file_path TEXT, mime TEXT, size INTEGER,
                        success INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL,
                        created_at_ms INTEGER NOT NULL
                    );
                    INSERT INTO message_route_logs
                        (event_key, occurred_at, occurred_at_ms, user, machine_id, platform,
                         chat_type, chat_id, direction, kind, content, source, created_at_ms)
                    VALUES ('old', '2026-10-01T12:00:00+08:00', 1790827200000, 'alice',
                            'bridge', 'test', 'private', 'chat', 'receive', 'text', 'old body', 'sqlite', 0);
                    PRAGMA user_version = 2;
                """)
            store = LogStore(root, retention_days=0)
            self.assertEqual(store.list_messages("bridge")[0]["content"], "old body")
            self.assertEqual(store.runtime_log_records("alice")[0]["inputs"][0]["content"], "old body")
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(connection.execute("SELECT exchange_id FROM message_route_logs").fetchone()[0], "")
            # Distinct IDs at the same time must not collapse into a single
            # idempotency record when the message bodies happen to be equal.
            common = {
                "user": "alice", "machine_id": "bridge", "platform": "test",
                "chat_type": "private", "chat_id": "chat",
                "occurred_at": "2026-10-02T12:00:00+08:00",
                "direction": "receive", "kind": "text", "content": "same body",
            }
            entries = [{**common, "exchange_id": key} for key in ("one", "two")]
            store.append_message_entries(entries)
            store.append_message_entries(entries)
            self.assertEqual(len(store.list_messages("bridge")), 3)
            self.assertEqual(len(store.runtime_log_records("alice")), 3)

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

    def test_task_specific_cron_query_filters_before_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LogStore(Path(directory))
            records = [
                {
                    "executed_at": "2026-10-01T08:00:00+08:00",
                    "user": "alice",
                    "task_id": "cron_target",
                    "status": "success",
                    "duration_ms": 1,
                    "result": {"order": 1},
                },
                {
                    "executed_at": "2026-10-02T08:00:00+08:00",
                    "user": "alice",
                    "task_id": "cron_target",
                    "status": "success",
                    "duration_ms": 1,
                    "result": {"order": 2},
                },
            ]
            records.extend(
                {
                    "executed_at": f"2026-11-{1 + index // 86400:02d}T00:{(index // 60) % 60:02d}:{index % 60:02d}+08:00",
                    "user": "alice",
                    "task_id": "cron_unrelated",
                    "status": "success",
                    "duration_ms": 1,
                    "result": {"index": index},
                }
                for index in range(5000)
            )
            store.append_cron_records(records)

            self.assertNotIn("cron_target", {row["task_id"] for row in store.list_cron("alice", limit=5000)})
            target_rows = store.list_cron_for_task("alice", "cron_target", limit=2)
            self.assertEqual([row["result"]["order"] for row in target_rows], [2, 1])

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
