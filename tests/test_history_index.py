from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from run.history import commit_window, empty_window
from run.history_index import (
    claim_pending_memory,
    close_session,
    find_record,
    finish_memory_claim,
    get_active,
    index_path,
    load_index,
    new_conversation_id,
    remove_session,
    reserve_session,
    update_run_state,
)


class HistoryIndexTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "users" / "alice" / "history").mkdir(parents=True)
        return temporary, root

    def commit_archive(
        self,
        root: Path,
        *,
        source: str,
        session_id: str,
        directory_name: str,
        memory_status: str | None = None,
    ) -> Path:
        directory = root / "users" / "alice" / "history" / directory_name
        window = empty_window("alice", source, session_id)
        window["text"]["messages"] = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        window["think"]["rounds"] = [{"round": 1, "content": "reason"}]
        window["tool"]["rounds"] = [{"round": 1, "calls": []}]
        window["data"]["rounds"] = 1
        if memory_status is not None:
            window["data"]["memory_processed_round"] = 0
            window["data"]["memory_status"] = memory_status
        commit_window(directory, window)
        return directory

    def test_empty_or_corrupt_index_rebuilds_legacy_archive(self) -> None:
        _, root = self.make_root()
        self.commit_archive(
            root,
            source="web",
            session_id="legacy-session",
            directory_name="2026-07-21-12-30",
        )
        index_path(root, "alice").write_text("{broken", "utf-8")

        rebuilt = load_index(root, "alice")

        record = find_record(root, "alice", "web", "legacy-session")
        self.assertEqual(rebuilt["schema_version"], 2)
        self.assertIsNotNone(record)
        self.assertTrue(str(record["conversation_id"]).startswith("legacy_"))
        self.assertEqual(record["archive_window"], "2026-07-21-12-30")
        self.assertEqual(record["memory_processed_round"], 1)
        self.assertEqual(record["memory_status"], "completed")

    def test_reserved_active_session_can_close_and_remove_without_archive(self) -> None:
        _, root = self.make_root()
        session_id = new_conversation_id()
        reserve_session(
            root,
            "alice",
            "web",
            session_id,
            active_key="interactive:alice",
        )

        self.assertEqual(
            get_active(root, "alice", "interactive:alice")["session_id"],
            session_id,
        )
        self.assertEqual(
            close_session(root, "alice", "web", session_id)["lifecycle"],
            "closed",
        )
        self.assertIsNone(get_active(root, "alice", "interactive:alice"))
        remove_session(root, "alice", "web", session_id)
        self.assertIsNone(find_record(root, "alice", "web", session_id))

    def test_active_references_do_not_collide_across_sources(self) -> None:
        _, root = self.make_root()
        session_id = "same-session-id"
        reserve_session(
            root,
            "alice",
            "web",
            session_id,
            active_key="interactive:alice",
        )
        reserve_session(
            root,
            "alice",
            "message:telegram",
            session_id,
            active_key="message:telegram:private:42",
        )

        self.assertEqual(
            get_active(root, "alice", "interactive:alice")["source"], "web"
        )
        self.assertEqual(
            get_active(root, "alice", "message:telegram:private:42")["source"],
            "message:telegram",
        )
        close_session(root, "alice", "web", session_id)
        self.assertIsNone(get_active(root, "alice", "interactive:alice"))
        self.assertEqual(
            get_active(root, "alice", "message:telegram:private:42")["source"],
            "message:telegram",
        )

    def test_memory_claim_is_exclusive_and_advances_cursor(self) -> None:
        _, root = self.make_root()
        self.commit_archive(
            root,
            source="web",
            session_id="memory-session",
            directory_name="conv_memory",
            memory_status="pending",
        )

        claim = claim_pending_memory(root, "alice")

        self.assertIsNotNone(claim)
        self.assertEqual(claim["memory_claim_round"], 1)
        self.assertIsNone(claim_pending_memory(root, "alice"))
        finished = finish_memory_claim(
            root,
            "alice",
            "web",
            "memory-session",
            claim_id=claim["memory_claim_id"],
            processed_round=1,
        )
        self.assertEqual(finished["memory_processed_round"], 1)
        self.assertEqual(finished["memory_status"], "completed")
        self.assertNotIn("memory_claim_id", finished)

    def test_pending_memory_is_not_claimed_while_session_is_running(self) -> None:
        _, root = self.make_root()
        archive = self.commit_archive(
            root,
            source="web",
            session_id="running-session",
            directory_name="conv_running",
            memory_status="pending",
        )
        update_run_state(
            root,
            "alice",
            "web",
            "running-session",
            run_state="running",
            directory=archive,
        )

        self.assertIsNone(claim_pending_memory(root, "alice"))
        update_run_state(
            root,
            "alice",
            "web",
            "running-session",
            run_state="idle",
        )
        self.assertIsNotNone(claim_pending_memory(root, "alice"))


if __name__ == "__main__":
    unittest.main()
