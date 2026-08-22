from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from run.agents import AgentOutputError
from provider.factory import ProviderCongestionError
from run.history import (
    commit_window,
    empty_window,
    load_window,
    queue_memory_extraction,
)
from run.history import (
    close_session,
    find_record,
    queue_summary,
    session_key,
)
from run.history import read_registry, write_registry
from run.context import estimate_text_tokens
from run.scheduler import (
    HISTORY_SUMMARY_CHUNK_TOKENS,
    MaintenanceScheduler,
    _summary_chunks,
)
from run.memory import MemoryStore, normalize_memory_filename
from tests.memory_db import update_fragment_metadata


class MaintenanceSchedulerTests(unittest.TestCase):
    def test_summary_chunks_hard_split_one_oversized_round_without_data_loss(
        self,
    ) -> None:
        user_text = "用户内容。" * 15_000
        assistant_text = "助手结果。" * 15_000
        chunks = _summary_chunks(
            [{"round": 1, "user": user_text, "assistant": assistant_text}]
        )

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            tokens = estimate_text_tokens(
                json.dumps(chunk, ensure_ascii=False, default=str)
            )
            self.assertLessEqual(tokens, HISTORY_SUMMARY_CHUNK_TOKENS)
        pieces = [item for chunk in chunks for item in chunk]
        self.assertEqual("".join(item["user"] for item in pieces), user_text)
        self.assertEqual(
            "".join(item["assistant"] for item in pieces),
            assistant_text,
        )

    def test_closed_session_summary_is_generated_in_background(self) -> None:
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
            json.dumps({"schema_version": 1}), "utf-8"
        )
        archive = root / "users" / "alice" / "history" / "conv_summary"
        window = empty_window("alice", "web", "conv_summary")
        window["text"]["messages"] = [
            {"role": "user", "content": "给历史对话增加后台摘要"},
            {"role": "assistant", "content": "已设计后台任务和原子索引写回"},
        ]
        window["data"].update(
            {"rounds": 1, "memory_processed_round": 1, "memory_status": "completed"}
        )
        commit_window(archive, window)
        close_session(root, "alice", "web", "conv_summary")
        queue_summary(root, "alice", "web", "conv_summary")

        with patch("run.scheduler.maintenance.AgentRunner") as runner_type:
            runner_type.return_value.run.return_value = SimpleNamespace(
                data={
                    "title": "历史会话后台摘要功能",
                    "summary": "关闭会话后异步生成可读标题和简短摘要，并安全写入历史索引。",
                }
            )
            result = MaintenanceScheduler(root).scan_once()

        summary_result = result["alice"]["history_summary"]
        self.assertEqual(summary_result["claimed"], 1)
        self.assertEqual(summary_result["processed"][0]["status"], "completed")
        record = find_record(root, "alice", "web", "conv_summary")
        self.assertEqual(record["title"], "历史会话后台摘要功能")
        self.assertEqual(record["summary_status"], "completed")
        self.assertIn("异步生成", record["summary"])
        run_kwargs = runner_type.return_value.run.call_args.kwargs
        self.assertEqual(run_kwargs["max_tokens"], 10_000)
        self.assertTrue(run_kwargs["structured_output_tool"])

    def test_failed_summary_preserves_safe_output_preview(self) -> None:
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
            json.dumps({"schema_version": 1}), "utf-8"
        )
        archive = root / "users" / "alice" / "history" / "conv_failed"
        window = empty_window("alice", "web", "conv_failed")
        window["text"]["messages"] = [
            {"role": "user", "content": "总结这段对话"},
            {"role": "assistant", "content": "返回了一段无法解析的普通文本"},
        ]
        window["data"].update(
            {"rounds": 1, "memory_processed_round": 1, "memory_status": "completed"}
        )
        commit_window(archive, window)
        close_session(root, "alice", "web", "conv_failed")
        queue_summary(root, "alice", "web", "conv_failed")

        with patch("run.scheduler.maintenance.AgentRunner") as runner_type:
            runner_type.return_value.run.side_effect = AgentOutputError(
                "子代理响应中没有 JSON 对象",
                raw_text="第一行普通文本\n第二行仍不是 JSON",
            )
            result = MaintenanceScheduler(root).scan_once()

        self.assertEqual(
            result["alice"]["history_summary"]["failed"][0]["error"][
                "raw_output_preview"
            ],
            "第一行普通文本 第二行仍不是 JSON",
        )
        record = find_record(root, "alice", "web", "conv_failed")
        self.assertEqual(record["summary_status"], "retry_wait")
        self.assertEqual(record["summary_retry_count"], 1)
        self.assertTrue(record["summary_retry_at"])
        self.assertEqual(
            record["summary_error"]["raw_output_preview"],
            "第一行普通文本 第二行仍不是 JSON",
        )

    def test_failed_summary_hides_sensitive_output_preview(self) -> None:
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
            json.dumps({"schema_version": 1}), "utf-8"
        )
        archive = root / "users" / "alice" / "history" / "conv_sensitive"
        window = empty_window("alice", "web", "conv_sensitive")
        window["text"]["messages"] = [
            {"role": "user", "content": "总结这段对话"},
            {"role": "assistant", "content": "摘要输出失败"},
        ]
        window["data"].update(
            {"rounds": 1, "memory_processed_round": 1, "memory_status": "completed"}
        )
        commit_window(archive, window)
        close_session(root, "alice", "web", "conv_sensitive")
        queue_summary(root, "alice", "web", "conv_sensitive")

        with patch("run.scheduler.maintenance.AgentRunner") as runner_type:
            runner_type.return_value.run.side_effect = AgentOutputError(
                "子代理响应格式错误",
                raw_text="api_key: test-secret-value",
            )
            MaintenanceScheduler(root).scan_once()

        record = find_record(root, "alice", "web", "conv_sensitive")
        self.assertEqual(
            record["summary_error"]["raw_output_preview"],
            "[输出包含疑似敏感内容，已隐藏]",
        )

    def test_summary_retry_resumes_from_persisted_chunk_checkpoint(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "config" / "global_config.json").write_text("{}", "utf-8")
        (root / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
        archive = root / "users" / "alice" / "history" / "conv_chunked"
        window = empty_window("alice", "web", "conv_chunked")
        window["text"]["messages"] = [
            {"role": "user", "content": "第一轮" + "甲" * 26_000},
            {"role": "assistant", "content": "第一答"},
        ]
        window["data"].update(
            {"rounds": 1, "memory_processed_round": 1, "memory_status": "completed"}
        )
        commit_window(archive, window)
        close_session(root, "alice", "web", "conv_chunked")
        queue_summary(root, "alice", "web", "conv_chunked")
        scheduler = MaintenanceScheduler(root, summary_retry_delays=(1,))

        with patch("run.scheduler.maintenance.AgentRunner") as runner_type:
            runner_type.return_value.run.side_effect = [
                SimpleNamespace(
                    data={
                        "title": "分块摘要第一阶段结果",
                        "summary": "第一块历史内容已经完成摘要并写入持久断点，后续失败时无需重新处理。",
                    }
                ),
                AgentOutputError("第二块暂时失败"),
            ]
            first = scheduler.process_next_summary("alice")
        self.assertEqual(first["failed"][0]["round"], 1)
        checkpointed = find_record(root, "alice", "web", "conv_chunked")
        self.assertEqual(checkpointed["summary_checkpoint_next_chunk"], 1)
        self.assertEqual(checkpointed["summary_checkpoint_total_chunks"], 2)

        sessions, active = read_registry(root, "alice")
        sessions[session_key("web", "conv_chunked")]["summary_retry_at"] = (
            "2000-01-01T00:00:00+00:00"
        )
        write_registry(root, "alice", sessions, active)
        with patch("run.scheduler.maintenance.AgentRunner") as runner_type:
            runner_type.return_value.run.return_value = SimpleNamespace(
                data={
                    "title": "分块摘要断点恢复完成",
                    "summary": "第二块从持久断点继续处理，最终摘要成功写回且没有重复调用第一块内容。",
                }
            )
            second = scheduler.process_next_summary("alice")

        self.assertEqual(second["processed"][0]["resumed_from_chunk"], 1)
        self.assertEqual(runner_type.return_value.run.call_count, 1)
        payload = runner_type.return_value.run.call_args.args[1]
        self.assertEqual(payload["previous_summary"]["title"], "分块摘要第一阶段结果")
        recovered = find_record(root, "alice", "web", "conv_chunked")
        self.assertEqual(recovered["summary_status"], "completed")
        self.assertTrue(recovered["summary_recovered_at"])

    def test_provider_congestion_defers_without_consuming_retry(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "config" / "global_config.json").write_text("{}", "utf-8")
        (root / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
        archive = root / "users" / "alice" / "history" / "conv_congested"
        window = empty_window("alice", "web", "conv_congested")
        window["text"]["messages"] = [
            {"role": "user", "content": "生成摘要"},
            {"role": "assistant", "content": "等待可用模型槽位"},
        ]
        window["data"].update({"rounds": 1, "memory_processed_round": 1})
        commit_window(archive, window)
        close_session(root, "alice", "web", "conv_congested")
        queue_summary(root, "alice", "web", "conv_congested")

        with patch("run.scheduler.maintenance.AgentRunner") as runner_type:
            runner_type.return_value.run.side_effect = ProviderCongestionError(
                "Provider 繁忙"
            )
            result = MaintenanceScheduler(root).process_next_summary("alice")

        self.assertTrue(result["failed"][0]["deferred"])
        record = find_record(root, "alice", "web", "conv_congested")
        self.assertEqual(record["summary_status"], "retry_wait")
        self.assertEqual(record["summary_attempt_count"], 0)
        self.assertEqual(record.get("summary_retry_count", 0), 0)

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

        def analyze(**kwargs):
            observed.update(kwargs)
            return {
                "status": "completed",
                "candidate_count": 1,
                "candidates": [],
                "source": {"source": "round_commit", "round": 1},
                "error": None,
            }

        with (
            patch("run.scheduler.maintenance.analyze_round_memory", side_effect=analyze),
            patch(
                "run.scheduler.maintenance.persist_round_memory_analysis",
                return_value={
                    "status": "completed",
                    "candidate_count": 1,
                    "error": None,
                },
            ),
        ):
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

        with patch("run.scheduler.maintenance.analyze_round_memory") as extract:
            result = MaintenanceScheduler(root).scan_once()

        recovery = result["alice"]["memory_recovery"]
        self.assertEqual(recovery["mode"], "compression_only")
        self.assertEqual(recovery["claimed"], 0)
        extract.assert_not_called()
        self.assertEqual(
            find_record(root, "alice", "web", "conv_deferred")["memory_status"],
            "pending",
        )

    def test_compression_only_claims_closed_session_explicitly_queued_for_save(
        self,
    ) -> None:
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

        with (
            patch(
                "run.scheduler.maintenance.analyze_round_memory",
                return_value={
                    "status": "completed",
                    "candidate_count": 1,
                    "candidates": [],
                    "source": {"source": "round_commit", "round": 1},
                    "error": None,
                },
            ) as extract,
            patch(
                "run.scheduler.maintenance.persist_round_memory_analysis",
                return_value={
                    "status": "completed",
                    "candidate_count": 1,
                    "error": None,
                },
            ),
        ):
            result = MaintenanceScheduler(root).scan_once()

        self.assertEqual(queued["status"], "queued")
        self.assertEqual(result["alice"]["memory_recovery"]["claimed"], 1)
        extract.assert_called_once()
        self.assertEqual(load_window(archive)["data"]["memory_status"], "completed")
        self.assertEqual(
            find_record(root, "alice", "web", "conv_saved")["memory_status"],
            "completed",
        )

    def test_manual_compression_queue_drains_open_session_to_bounded_target(
        self,
    ) -> None:
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
        archive = root / "users" / "alice" / "history" / "conv_manual"
        window = empty_window("alice", "web", "conv_manual")
        window["text"]["messages"] = [
            item
            for round_number in range(1, 4)
            for item in (
                {"role": "user", "content": f"问题 {round_number}"},
                {"role": "assistant", "content": f"回答 {round_number}"},
            )
        ]
        window["data"].update(
            {
                "rounds": 3,
                "memory_processed_round": 0,
                "memory_status": "deferred",
            }
        )
        commit_window(archive, window)
        queued = queue_memory_extraction(
            root,
            "alice",
            "web",
            "conv_manual",
            target_round=3,
            reason="manual_compression",
        )

        analysis = {
            "status": "completed",
            "candidate_count": 0,
            "candidates": [],
            "source": {"source": "round_commit"},
            "error": None,
        }
        persisted = {"status": "completed", "candidate_count": 0, "error": None}
        with (
            patch(
                "run.scheduler.maintenance.analyze_memory_batch_resilient", return_value=analysis
            ) as analyze_batch,
            patch(
                "run.scheduler.maintenance.analyze_round_memory", return_value=analysis
            ) as analyze_round,
            patch(
                "run.scheduler.maintenance.persist_round_memory_analysis",
                return_value=persisted,
            ),
        ):
            first = MaintenanceScheduler(root).scan_once()
            after_first = load_window(archive)["data"]
            second = MaintenanceScheduler(root).scan_once()

        self.assertEqual(queued["pending_rounds"], 3)
        self.assertEqual(first["alice"]["memory_recovery"]["claimed"], 2)
        self.assertEqual(first["alice"]["memory_recovery"]["batches"], 1)
        self.assertEqual(analyze_batch.call_count, 1)
        self.assertEqual(analyze_round.call_count, 1)
        self.assertEqual(after_first["memory_processed_round"], 2)
        self.assertEqual(after_first["memory_status"], "queued")
        self.assertEqual(after_first["memory_target_round"], 3)
        self.assertEqual(second["alice"]["memory_recovery"]["claimed"], 1)
        completed = load_window(archive)["data"]
        self.assertEqual(completed["memory_processed_round"], 3)
        self.assertEqual(completed["memory_status"], "completed")
        self.assertNotIn("memory_target_round", completed)

    def test_manual_compression_queue_stops_at_target_when_new_round_arrives(
        self,
    ) -> None:
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
                        "recovery_max_rounds_per_scan": 10,
                    }
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1}),
            "utf-8",
        )
        archive = root / "users" / "alice" / "history" / "conv_bounded"
        window = empty_window("alice", "web", "conv_bounded")
        window["text"]["messages"] = [
            item
            for round_number in range(1, 4)
            for item in (
                {"role": "user", "content": f"问题 {round_number}"},
                {"role": "assistant", "content": f"回答 {round_number}"},
            )
        ]
        window["data"].update(
            {"rounds": 3, "memory_processed_round": 0, "memory_status": "deferred"}
        )
        commit_window(archive, window)
        queue_memory_extraction(
            root,
            "alice",
            "web",
            "conv_bounded",
            target_round=3,
            reason="manual_compression",
        )
        window = load_window(archive)
        window["text"]["messages"].extend(
            [
                {"role": "user", "content": "后来新增的问题"},
                {"role": "assistant", "content": "后来新增的回答"},
            ]
        )
        window["data"]["rounds"] = 4
        commit_window(archive, window)

        analysis = {
            "status": "completed",
            "candidate_count": 0,
            "candidates": [],
            "source": {"source": "round_commit"},
            "error": None,
        }
        persisted = {"status": "completed", "candidate_count": 0, "error": None}
        with (
            patch(
                "run.scheduler.maintenance.analyze_memory_batch_resilient",
                return_value=analysis,
            ) as analyze,
            patch(
                "run.scheduler.maintenance.persist_round_memory_analysis",
                return_value=persisted,
            ),
        ):
            result = MaintenanceScheduler(root).scan_once()

        recovery = result["alice"]["memory_recovery"]
        self.assertEqual(recovery["claimed"], 3)
        self.assertEqual(recovery["batches"], 1)
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(len(analyze.call_args.kwargs["rounds"]), 3)
        completed = load_window(archive)["data"]
        self.assertEqual(completed["rounds"], 4)
        self.assertEqual(completed["memory_processed_round"], 3)
        self.assertEqual(completed["memory_status"], "deferred")
        self.assertNotIn("memory_target_round", completed)
        record = find_record(root, "alice", "web", "conv_bounded")
        self.assertEqual(record["memory_processed_round"], 3)
        self.assertEqual(record["memory_status"], "deferred")

    def test_failed_manual_memory_retry_preserves_last_error(self) -> None:
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
                        "recovery_max_rounds_per_scan": 1,
                    }
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1}),
            "utf-8",
        )
        archive = root / "users" / "alice" / "history" / "conv_retry"
        window = empty_window("alice", "web", "conv_retry")
        window["text"]["messages"] = [
            {"role": "user", "content": "需要提取的事实"},
            {"role": "assistant", "content": "事实回答"},
        ]
        window["data"].update(
            {"rounds": 1, "memory_processed_round": 0, "memory_status": "deferred"}
        )
        commit_window(archive, window)
        queue_memory_extraction(
            root,
            "alice",
            "web",
            "conv_retry",
            target_round=1,
            reason="manual_compression",
        )
        failure = {
            "status": "failed",
            "candidate_count": 0,
            "error": {
                "message": "self_improve 输出缺少 candidates 数组",
                "exception_type": "AgentOutputError",
            },
        }
        with patch("run.scheduler.maintenance.analyze_round_memory", return_value=failure):
            first = MaintenanceScheduler(root).scan_once()

        self.assertEqual(first["alice"]["memory_recovery"]["claimed"], 1)
        failed_archive = load_window(archive)["data"]
        failed_record = find_record(root, "alice", "web", "conv_retry")
        for state in (failed_archive, failed_record):
            self.assertEqual(state["memory_processed_round"], 0)
            self.assertEqual(state["memory_status"], "failed")
            self.assertEqual(state["memory_error"]["round"], 1)
            self.assertEqual(
                state["memory_last_error"]["exception_type"], "AgentOutputError"
            )
            self.assertEqual(state["memory_last_error"]["retry_count"], 1)
            self.assertTrue(state["memory_last_error"]["occurred_at"])

        sessions, active = read_registry(root, "alice")
        record_key = session_key("web", "conv_retry")
        sessions[record_key]["memory_state_updated_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=2)
        ).isoformat()
        write_registry(root, "alice", sessions, active)
        success_analysis = {
            "status": "completed",
            "candidate_count": 0,
            "candidates": [],
            "source": {"source": "round_commit"},
            "error": None,
        }
        success_persisted = {
            "status": "completed",
            "candidate_count": 0,
            "error": None,
        }
        with (
            patch(
                "run.scheduler.maintenance.analyze_round_memory",
                return_value=success_analysis,
            ),
            patch(
                "run.scheduler.maintenance.persist_round_memory_analysis",
                return_value=success_persisted,
            ),
        ):
            second = MaintenanceScheduler(root).scan_once()

        self.assertEqual(second["alice"]["memory_recovery"]["claimed"], 1)
        recovered_archive = load_window(archive)["data"]
        recovered_record = find_record(root, "alice", "web", "conv_retry")
        for state in (recovered_archive, recovered_record):
            self.assertEqual(state["memory_processed_round"], 1)
            self.assertEqual(state["memory_status"], "completed")
            self.assertNotIn("memory_error", state)
            self.assertEqual(state["memory_last_error"]["round"], 1)
            self.assertEqual(state["memory_last_error"]["retry_count"], 1)

    def test_stale_memory_analysis_is_discarded_without_persisting_or_marking_failed(
        self,
    ) -> None:
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
        archive = root / "users" / "alice" / "history" / "conv_stale"
        window = empty_window("alice", "web", "conv_stale")
        window["text"]["messages"] = [
            {"role": "user", "content": "陈旧 claim 测试"},
            {"role": "assistant", "content": "不会写入"},
        ]
        window["data"].update(
            {"rounds": 1, "memory_processed_round": 0, "memory_status": "deferred"}
        )
        commit_window(archive, window)
        queue_memory_extraction(
            root,
            "alice",
            "web",
            "conv_stale",
            target_round=1,
            reason="manual_compression",
        )
        analysis = {
            "status": "completed",
            "candidate_count": 1,
            "candidates": [{"filename": "不应写入"}],
            "source": {"source": "round_commit"},
            "error": None,
        }
        with (
            patch("run.scheduler.maintenance.analyze_round_memory", return_value=analysis),
            patch(
                "run.scheduler.maintenance.find_record",
                return_value={
                    "memory_claim_id": "newer-worker",
                    "memory_claim_round": 1,
                },
            ),
            patch("run.scheduler.maintenance.persist_round_memory_analysis") as persist,
        ):
            result = MaintenanceScheduler(root).scan_once()

        recovery = result["alice"]["memory_recovery"]
        self.assertEqual(recovery["claimed"], 1)
        self.assertTrue(recovery["failed"][0]["stale"])
        persist.assert_not_called()
        archived = load_window(archive)["data"]
        self.assertEqual(archived["memory_status"], "queued")
        self.assertNotIn("memory_error", archived)

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
        now = datetime(2026, 7, 19, 3, tzinfo=timezone.utc)
        store.create_fragment(
            "half_year", filename, "durable preference", now=now - timedelta(days=181)
        )
        update_fragment_metadata(
            store,
            "half_year",
            filename,
            weight=60,
            expires_at=now - timedelta(seconds=1),
        )

        result = MaintenanceScheduler(root).scan_once(now=now, force=True)

        self.assertNotIn("_perception", result)
        self.assertNotIn("memory_lifecycle", result["alice"])
        self.assertIsNone(store.get_entry("permanent", filename))
        self.assertIsNotNone(store.get_entry("half_year", filename))
        self.assertNotIn("important_memory", result["alice"])
        self.assertNotIn("daily_memory_review", result["alice"])


if __name__ == "__main__":
    unittest.main()
