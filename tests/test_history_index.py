from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from run.history import commit_window, empty_window, list_sessions
from run.history_index import (
    claim_pending_memory,
    claim_pending_summary,
    close_session,
    find_record,
    finish_memory_claim,
    finish_summary_claim,
    get_active,
    index_path,
    load_index,
    new_conversation_id,
    remove_session,
    queue_summary,
    reserve_session,
    update_run_state,
    update_title,
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

    def test_closed_session_summary_claim_is_idempotent_and_exclusive(self) -> None:
        _, root = self.make_root()
        self.commit_archive(
            root,
            source="web",
            session_id="summary-session",
            directory_name="conv_summary",
        )
        self.assertEqual(
            queue_summary(root, "alice", "web", "summary-session")["reason"],
            "session_not_closed",
        )
        close_session(root, "alice", "web", "summary-session")
        queued = queue_summary(root, "alice", "web", "summary-session")
        self.assertEqual(queued["status"], "queued")
        claim = claim_pending_summary(root, "alice")
        self.assertIsNotNone(claim)
        self.assertIsNone(claim_pending_summary(root, "alice"))
        finished = finish_summary_claim(
            root,
            "alice",
            "web",
            "summary-session",
            claim_id=claim["summary_claim_id"],
            title="历史会话后台摘要功能",
            summary="关闭会话后由后台线程生成标题和摘要，并原子写回历史索引。",
            completed_round=1,
        )
        self.assertEqual(finished["summary_status"], "completed")
        self.assertEqual(finished["summary_completed_round"], 1)
        self.assertEqual(
            queue_summary(root, "alice", "web", "summary-session")["reason"],
            "already_current",
        )

    def test_manual_title_is_not_overwritten_by_automatic_summary(self) -> None:
        _, root = self.make_root()
        self.commit_archive(
            root,
            source="web",
            session_id="manual-title",
            directory_name="conv_manual_title",
        )
        close_session(root, "alice", "web", "manual-title")
        update_title(root, "alice", "web", "manual-title", "用户手动命名")
        queue_summary(root, "alice", "web", "manual-title")
        claim = claim_pending_summary(root, "alice")
        finished = finish_summary_claim(
            root,
            "alice",
            "web",
            "manual-title",
            claim_id=claim["summary_claim_id"],
            title="模型自动生成标题内容",
            summary="后台可以更新摘要内容，但不得覆盖用户已经手动设置的标题。",
            completed_round=1,
        )
        self.assertEqual(finished["title"], "用户手动命名")
        self.assertEqual(finished["title_source"], "manual")
        self.assertIn("不得覆盖", finished["summary"])

    def test_failed_summary_retry_metadata_is_exposed_to_session_list(self) -> None:
        _, root = self.make_root()
        self.commit_archive(
            root,
            source="web",
            session_id="retry-summary",
            directory_name="conv_retry_summary",
        )
        close_session(root, "alice", "web", "retry-summary")
        queue_summary(root, "alice", "web", "retry-summary")
        claim = claim_pending_summary(root, "alice")
        failed = finish_summary_claim(
            root,
            "alice",
            "web",
            "retry-summary",
            claim_id=claim["summary_claim_id"],
            error={"message": "摘要格式错误", "exception_type": "AgentOutputError"},
        )

        self.assertEqual(failed["summary_status"], "failed")
        self.assertEqual(failed["summary_retry_count"], 1)
        self.assertTrue(failed["summary_retry_at"])
        listed = list_sessions(root, "alice", "web")[0]
        self.assertEqual(listed["summary_retry_count"], 1)
        self.assertEqual(listed["summary_retry_at"], failed["summary_retry_at"])


if __name__ == "__main__":
    unittest.main()
