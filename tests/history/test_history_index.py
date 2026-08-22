from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from run.history import (
    commit_window,
    empty_window,
    find_window,
    list_sessions,
    load_window,
)
from run.history import (
    claim_pending_memory,
    claim_pending_summary,
    close_session,
    find_record,
    finish_memory_claim,
    finish_summary_claim,
    get_active,
    load_index,
    new_conversation_id,
    remove_session,
    queue_summary,
    reserve_session,
    retry_summary,
    session_key,
    update_run_state,
    update_title,
)
from run.history import (
    connection,
    database_path,
    delete_window,
    query_session_records,
    read_registry,
    session_page_cursor,
    write_registry,
    window_exists,
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

    def test_history_is_stored_in_sqlite_without_archive_json_directories(self) -> None:
        _, root = self.make_root()
        directory = self.commit_archive(
            root,
            source="web",
            session_id="sqlite-session",
            directory_name="conv_sqlite",
        )

        self.assertTrue(database_path(root, "alice").is_file())
        self.assertFalse(directory.exists())
        self.assertTrue(window_exists(directory))
        self.assertEqual(load_window(directory)["data"]["rounds"], 1)

    def test_repeated_history_reads_use_query_only_connections(self) -> None:
        _, root = self.make_root()
        self.commit_archive(
            root,
            source="web",
            session_id="query-only-session",
            directory_name="conv_query_only",
        )
        with connection(root, "alice") as database:
            before = database.execute(
                "SELECT rowid FROM history_meta WHERE key='schema_version'"
            ).fetchone()[0]
            self.assertEqual(database.execute("PRAGMA query_only").fetchone()[0], 1)

        query_session_records(root, "alice", source="web")

        with connection(root, "alice") as database:
            after = database.execute(
                "SELECT rowid FROM history_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(after, before)

    def test_session_cursor_does_not_skip_equal_timestamps(self) -> None:
        _, root = self.make_root()
        timestamp = "2026-08-02T00:00:00+00:00"
        sessions = {
            session_key("web", f"same-{index}"): {
                "conversation_id": f"same-{index}",
                "session_id": f"same-{index}",
                "source": "web",
                "title": f"same {index}",
                "lifecycle": "open",
                "run_state": "idle",
                "updated_at": timestamp,
            }
            for index in range(5)
        }
        write_registry(root, "alice", sessions, {})

        collected: list[str] = []
        cursor = ""
        while True:
            page, has_more = query_session_records(
                root,
                "alice",
                source="web",
                limit=2,
                before_updated_at=cursor,
            )
            collected.extend(str(record["session_id"]) for record in page)
            if not has_more:
                break
            cursor = session_page_cursor(page[-1])

        self.assertEqual(
            collected,
            ["same-4", "same-3", "same-2", "same-1", "same-0"],
        )

    def test_cross_source_cursor_does_not_skip_same_session_and_timestamp(self) -> None:
        _, root = self.make_root()
        timestamp = "2026-08-08T00:00:00+00:00"
        sessions = {
            session_key(source, "same-session"): {
                "conversation_id": f"{source}-conversation",
                "session_id": "same-session",
                "source": source,
                "title": source,
                "lifecycle": "closed",
                "run_state": "idle",
                "updated_at": timestamp,
            }
            for source in ("web", "cli", "message:telegram")
        }
        write_registry(root, "alice", sessions, {})

        collected: list[tuple[str, str]] = []
        cursor = ""
        while True:
            page, has_more = query_session_records(
                root,
                "alice",
                source=None,
                limit=1,
                before_updated_at=cursor,
            )
            collected.extend(
                (str(record["source"]), str(record["session_id"]))
                for record in page
            )
            if not has_more:
                break
            cursor = session_page_cursor(page[-1])

        self.assertEqual(
            collected,
            [
                ("web", "same-session"),
                ("message:telegram", "same-session"),
                ("cli", "same-session"),
            ],
        )

    def test_missing_registry_rebuilds_from_sqlite_windows(self) -> None:
        _, root = self.make_root()
        self.commit_archive(
            root,
            source="web",
            session_id="rebuild-session",
            directory_name="conv_rebuild",
        )
        with connection(root, "alice", write=True) as database:
            database.execute("DELETE FROM history_sessions")

        rebuilt = load_index(root, "alice")

        record = rebuilt["sessions"][session_key("web", "rebuild-session")]
        self.assertEqual(rebuilt["schema_version"], 3)
        self.assertEqual(record["archive_window"], "conv_rebuild")

    def test_find_window_uses_sqlite_registry_without_directory_scan(self) -> None:
        _, root = self.make_root()
        expected = self.commit_archive(
            root,
            source="web",
            session_id="target-session",
            directory_name="conv_target",
        )
        self.commit_archive(
            root,
            source="web",
            session_id="other-session",
            directory_name="conv_other",
        )

        self.assertEqual(find_window(root, "alice", "web", "target-session"), expected)

    def test_deleting_a_window_is_a_database_operation(self) -> None:
        _, root = self.make_root()
        archive = self.commit_archive(
            root,
            source="web",
            session_id="removed-session",
            directory_name="conv_removed",
        )
        self.assertTrue(delete_window(archive))
        self.assertFalse(window_exists(archive))

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

    def test_memory_claim_can_lease_a_contiguous_round_range(self) -> None:
        _, root = self.make_root()
        archive = self.commit_archive(
            root,
            source="web",
            session_id="memory-batch",
            directory_name="conv_memory_batch",
            memory_status="pending",
        )
        window = load_window(archive)
        window["text"]["messages"] = [
            item
            for round_number in range(1, 6)
            for item in (
                {"role": "user", "content": f"问题 {round_number}"},
                {"role": "assistant", "content": f"回答 {round_number}"},
            )
        ]
        window["data"]["rounds"] = 5
        window["data"]["memory_processed_round"] = 0
        commit_window(archive, window)

        claim = claim_pending_memory(root, "alice", max_rounds=5)

        self.assertEqual(claim["memory_claim_round"], 1)
        self.assertEqual(claim["memory_claim_start_round"], 1)
        self.assertEqual(claim["memory_claim_end_round"], 5)
        finished = finish_memory_claim(
            root,
            "alice",
            "web",
            "memory-batch",
            claim_id=claim["memory_claim_id"],
            processed_round=5,
        )
        self.assertEqual(finished["memory_processed_round"], 5)
        self.assertEqual(finished["memory_status"], "completed")

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

        self.assertEqual(failed["summary_status"], "retry_wait")
        self.assertEqual(failed["summary_retry_count"], 1)
        self.assertEqual(failed["summary_attempt_count"], 1)
        self.assertEqual(failed["summary_consecutive_failures"], 1)
        self.assertEqual(failed["summary_max_attempts"], 5)
        self.assertEqual(failed["summary_last_error"]["message"], "摘要格式错误")
        self.assertTrue(failed["summary_retry_at"])
        listed = list_sessions(root, "alice", "web")[0]
        self.assertEqual(listed["summary_retry_count"], 1)
        self.assertEqual(listed["summary_retry_at"], failed["summary_retry_at"])

    def test_summary_exhaustion_manual_retry_and_recovery(self) -> None:
        _, root = self.make_root()
        self.commit_archive(
            root,
            source="web",
            session_id="exhausted-summary",
            directory_name="conv_exhausted_summary",
        )
        close_session(root, "alice", "web", "exhausted-summary")
        queue_summary(root, "alice", "web", "exhausted-summary")

        for attempt in range(1, 4):
            claim = claim_pending_summary(root, "alice")
            self.assertIsNotNone(claim)
            failed = finish_summary_claim(
                root,
                "alice",
                "web",
                "exhausted-summary",
                claim_id=claim["summary_claim_id"],
                error={"message": f"失败 {attempt}"},
                max_attempts=3,
                retry_delays=(1,),
            )
            if attempt < 3:
                sessions, active = read_registry(root, "alice")
                record = sessions[session_key("web", "exhausted-summary")]
                record["summary_retry_at"] = "2000-01-01T00:00:00+00:00"
                write_registry(root, "alice", sessions, active)

        self.assertEqual(failed["summary_status"], "exhausted")
        self.assertNotIn("summary_retry_at", failed)
        requeued = retry_summary(root, "alice", "web", "exhausted-summary")
        self.assertEqual(requeued["summary_status"], "queued")
        self.assertEqual(requeued["summary_retry_count"], 0)

        claim = claim_pending_summary(root, "alice")
        recovered = finish_summary_claim(
            root,
            "alice",
            "web",
            "exhausted-summary",
            claim_id=claim["summary_claim_id"],
            title="历史摘要重试恢复成功",
            summary="自动重试耗尽后，用户可以手动重新排队并成功生成历史摘要内容。",
            completed_round=1,
        )
        self.assertEqual(recovered["summary_status"], "completed")
        self.assertTrue(recovered["summary_recovered_at"])
        self.assertEqual(recovered["summary_consecutive_failures"], 0)


if __name__ == "__main__":
    unittest.main()
