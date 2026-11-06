from __future__ import annotations

from tests.card_history_support import session_history

from tests.type_assertions import require_present

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.adapters.compat import chat_response_to_kemo, kemo_request_to_chat
from provider.schema import ChatResponse, Usage
from run.engine import handle_request, iter_request_events

from run.memory import (
    analyze_memory_batch_resilient,
    memory_batch_operation_id,
)
from run.history import find_record as find_history_record
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
        return ChatResponse(
            text="完成", usage=Usage(1, 1, 2, source="mock"), model=request.model
        )

    def create(self, request):
        return chat_response_to_kemo(self.chat(kemo_request_to_chat(request)), request)


class MemoryEngineTests(unittest.TestCase):
    def test_memory_batch_operation_id_changes_when_round_content_changes(self) -> None:
        first = memory_batch_operation_id(
            "alice",
            "web",
            "session",
            1,
            2,
            [{"round": 1, "messages": [{"content": "before"}]}],
        )
        repeated = memory_batch_operation_id(
            "alice",
            "web",
            "session",
            1,
            2,
            [{"round": 1, "messages": [{"content": "before"}]}],
        )
        rewritten = memory_batch_operation_id(
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
        rounds = [{"round": number, "messages": []} for number in range(1, 5)]
        failed = {
            "status": "failed",
            "candidate_count": 0,
            "error": {
                "message": "输出缺少 candidates",
                "exception_type": "AgentOutputError",
            },
        }

        analysis_calls = 0

        def analyze(**kwargs):
            nonlocal analysis_calls
            current = kwargs["rounds"]
            if analysis_calls < 2:
                analysis_calls += 1
                return dict(failed)
            analysis_calls += 1
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

        analysis_calls = 0
        with patch(
            "run.memory.analysis.analyze_memory_batch", side_effect=analyze
        ) as mocked:
            result = analyze_memory_batch_resilient(
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
            "api_key_env": "KEMO_API_KEY",
            "model": "mock",
            "stream": False,
        }
        (root / "users" / "alice" / "user_config.yaml").write_text(
            json.dumps({"schema_version": 1, "provider": provider}),
            "utf-8",
        )
        (root / "config" / "global_config.yaml").write_text(
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
        return {
            "user": "alice",
            "source": "cli",
            "session_id": "s",
            "prompt": "记住我喜欢川菜",
        }

    def test_empty_database_tier_is_treated_as_empty_memory(self) -> None:
        root = self.root()
        config = {
            "memory": {
                "tiers": TIERS,
            }
        }
        store = MemoryStore(root, "alice", config)
        self.assertEqual(store.load_tier("permanent"), [])

    def test_committed_history_defers_compression_only_extraction(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"KEMO_API_KEY": "x"}, clear=False):
            result = handle_request(
                self.request(), root=root, provider_factory=lambda _: Provider()
            )
        self.assertTrue(result["committed"])
        self.assertEqual(result["memory"]["extraction_mode"], "compression_only")
        history = require_present(session_history(root, "alice", "cli", "s")).refresh()
        self.assertEqual([card.raw_kind for card in history.cards],
                         ["user_text", "agent_text"])
        self.assertEqual(history.integrations[0]["status"], "completed")
        record = require_present(find_history_record(root, "alice", "cli", "s"))
        self.assertEqual(record["memory_processed_seq"], 0)
        self.assertNotIn("memory_target_seq", record)
        self.assertNotEqual(record.get("memory_status"), "queued")
        self.assertEqual(
            MemoryStore(root, "alice", {"memory": {"tiers": TIERS}}).list_items(),
            [],
        )

    def test_provider_failure_and_cancel_commit_without_extracting_memory(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"KEMO_API_KEY": "x"}, clear=False):
            with self.assertRaises(RuntimeError):
                handle_request(
                    self.request(),
                    root=root,
                    provider_factory=lambda _: Provider(fail=True),
                )
        archive = require_present(session_history(root, 'alice', 'cli', 's')).refresh()
        self.assertEqual(len(archive.integrations), 1)
        self.assertEqual(archive.integrations[0]['status'], 'failed')
        self.assertEqual(
            MemoryStore(root, "alice", {"memory": {"tiers": TIERS}}).list_items(),
            [],
        )

        stopped = threading.Event()
        iterator = iter_request_events(
            self.request(),
            root=root,
            provider_factory=lambda _: Provider(),
            cancel_event=stopped,
        )
        stopped.set()
        with patch.dict(os.environ, {"KEMO_API_KEY": "x"}, clear=False):
            events = list(iterator)
        self.assertEqual([event.type for event in events], ["done"])
        self.assertEqual(events[0].metadata["status"], "cancelled")
        archive = require_present(session_history(root, 'alice', 'cli', 's')).refresh()
        self.assertEqual(len(archive.integrations), 2)
        self.assertEqual([item['status'] for item in archive.integrations], ['failed', 'cancelled'])




if __name__ == "__main__":
    unittest.main()
