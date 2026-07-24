from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.adapters.compat import chat_response_to_kemo, kemo_request_to_chat
from provider.schema import ChatResponse, Usage
from run.engine import (
    _analyze_memory_batch_resilient,
    _memory_batch_operation_id,
    handle_request,
    iter_request_events,
)
from run.history import find_window, load_window
from run.history_index import find_record as find_history_record
from run.memory import MemoryStore


TIERS = {
    "seven_days": {"days": 7, "upgrade_threshold": 3, "next": "one_month"},
    "one_month": {"days": 30, "upgrade_threshold": 10, "next": "half_year"},
    "half_year": {"days": 180, "upgrade_threshold": 60, "next": None},
}


class Provider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def chat(self, request):
        if self.fail:
            raise RuntimeError("provider failed")
        return ChatResponse(text="完成", usage=Usage(1, 1, 2, source="mock"), model=request.model)

    def create(self, request):
        return chat_response_to_kemo(self.chat(kemo_request_to_chat(request)), request)


class MemoryEngineTests(unittest.TestCase):
    def test_memory_batch_operation_id_changes_when_round_content_changes(self) -> None:
        first = _memory_batch_operation_id(
            "alice",
            "web",
            "session",
            1,
            2,
            [{"round": 1, "messages": [{"content": "before"}]}],
        )
        repeated = _memory_batch_operation_id(
            "alice",
            "web",
            "session",
            1,
            2,
            [{"round": 1, "messages": [{"content": "before"}]}],
        )
        rewritten = _memory_batch_operation_id(
            "alice",
            "web",
            "session",
            1,
            2,
            [{"round": 1, "messages": [{"content": "after"}]}],
        )

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, rewritten)

    def test_batch_analysis_retries_then_splits_malformed_output(self) -> None:
        rounds = [
            {"round": number, "messages": []}
            for number in range(1, 5)
        ]
        failed = {
            "status": "failed",
            "candidate_count": 0,
            "error": {
                "message": "输出缺少 candidates",
                "exception_type": "AgentOutputError",
            },
        }

        def analyze(**kwargs):
            current = kwargs["rounds"]
            if analyze.calls < 2:
                analyze.calls += 1
                return dict(failed)
            analyze.calls += 1
            numbers = [item["round"] for item in current]
            return {
                "status": "completed",
                "candidate_count": 1,
                "candidates": [
                    {
                        "filename": f"批次-{numbers[0]}",
                        "content": "批次内容",
                    }
                ],
                "round_start": min(numbers),
                "round_end": max(numbers),
                "rounds": numbers,
                "source": {},
                "agent": "self_improve",
                "usage": {},
                "error": None,
            }

        analyze.calls = 0
        with patch("run.engine._analyze_memory_batch", side_effect=analyze) as mocked:
            result = _analyze_memory_batch_resilient(
                rounds=rounds,
                agent_runner=object(),
                cancel_event=None,
                source={"source": "round_commit"},
            )

        self.assertEqual(mocked.call_count, 4)
        self.assertTrue(result["fallback_split"])
        self.assertEqual(result["rounds"], [1, 2, 3, 4])
        self.assertEqual(result["candidate_count"], 2)

    def root(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice" / "history").mkdir(parents=True)
        provider = {
            "type": "kemo",
            "base_url": "http://127.0.0.1:1/v1",
            "api_key_env": "TEST_MEMORY_KEY",
            "model": "mock",
            "stream": False,
        }
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1, "provider": provider}),
            "utf-8",
        )
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "tools": {"enabled": False},
                    "memory": {
                        "tiers": TIERS,
                    },
                }
            ),
            "utf-8",
        )
        return root

    def request(self):
        return {"user": "alice", "source": "cli", "session_id": "s", "prompt": "记住我喜欢川菜"}

    def test_empty_tier_file_is_treated_as_empty_memory(self) -> None:
        root = self.root()
        config = {
            "memory": {
                "tiers": TIERS,
            }
        }
        store = MemoryStore(root, "alice", config)
        store.tier_dir("permanent").mkdir(parents=True, exist_ok=True)
        self.assertEqual(store.load_tier("permanent"), [])

    def test_committed_round_defers_compression_only_extraction(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False):
            result = handle_request(self.request(), root=root, provider_factory=lambda _: Provider())
        self.assertTrue(result["committed"])
        self.assertIsNone(result["memory"]["extraction_task_id"])
        self.assertIsNone(result["memory"]["extraction_error"])
        self.assertEqual(result["memory"]["extraction_mode"], "compression_only")
        self.assertEqual(result["memory"]["round_extraction"]["status"], "skipped")
        self.assertEqual(
            result["memory"]["round_extraction"]["reason"],
            "deferred_until_compression",
        )
        archive = load_window(find_window(root, "alice", "cli", "s"))
        self.assertEqual(archive["data"]["memory_processed_round"], 0)
        self.assertEqual(archive["data"]["memory_status"], "deferred")
        self.assertEqual(
            MemoryStore(root, "alice", {"memory": {"tiers": TIERS}}).list_items(),
            [],
        )

    def test_provider_failure_does_not_submit_but_cancel_commits_terminal_round(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False):
            with self.assertRaises(RuntimeError):
                handle_request(self.request(), root=root, provider_factory=lambda _: Provider(fail=True))
        self.assertIsNone(find_window(root, "alice", "cli", "s"))

        stopped = threading.Event()
        iterator = iter_request_events(
            self.request(), root=root, provider_factory=lambda _: Provider(), cancel_event=stopped
        )
        stopped.set()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False):
            events = list(iterator)
        self.assertEqual([event.type for event in events], ["done"])
        self.assertEqual(events[0].metadata["status"], "cancelled")
        archive = load_window(find_window(root, "alice", "cli", "s"))
        self.assertEqual(archive["data"]["rounds"], 1)
        self.assertEqual(archive["data"]["round_metrics"][0]["status"], "cancelled")

    def test_committed_round_persists_history_without_extraction_queue(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False):
            result = handle_request(self.request(), root=root, provider_factory=lambda _: Provider())
        self.assertTrue(result["committed"])
        history = root / "users" / "alice" / "history"
        windows = [
            item for item in history.iterdir() if item.is_dir() and item.name != "temp"
        ]
        self.assertEqual(len(windows), 1)
        self.assertTrue((history / "temp" / windows[0].name / "data.json").is_file())

    def test_index_memory_error_does_not_fail_commit_or_leave_run_running(self) -> None:
        root = self.root()
        request = self.request()
        request["session_id"] = "index-error"
        with (
            patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False),
            patch("run.engine.update_memory_state", side_effect=RuntimeError("index failed")),
        ):
            result = handle_request(request, root=root, provider_factory=lambda _: Provider())

        self.assertTrue(result["committed"])
        self.assertEqual(result["history_index_error"]["message"], "index failed")
        indexed = find_history_record(root, "alice", "cli", "index-error")
        self.assertEqual(indexed["run_state"], "idle")


if __name__ == "__main__":
    unittest.main()
