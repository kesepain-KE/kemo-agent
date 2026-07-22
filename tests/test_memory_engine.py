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
from run.engine import handle_request, iter_request_events
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

    def test_committed_round_records_disabled_commit_extraction(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False):
            result = handle_request(self.request(), root=root, provider_factory=lambda _: Provider())
        self.assertTrue(result["committed"])
        self.assertIsNone(result["memory"]["extraction_task_id"])
        self.assertIsNone(result["memory"]["extraction_error"])
        self.assertEqual(result["memory"]["extraction_mode"], "round_commit")
        self.assertEqual(result["memory"]["round_extraction"]["status"], "skipped")
        self.assertEqual(
            MemoryStore(root, "alice", {"memory": {"tiers": TIERS}}).list_items(),
            [],
        )

    def test_provider_failure_and_cancel_do_not_submit(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False):
            with self.assertRaises(RuntimeError):
                handle_request(self.request(), root=root, provider_factory=lambda _: Provider(fail=True))

        stopped = threading.Event()
        iterator = iter_request_events(
            self.request(), root=root, provider_factory=lambda _: Provider(), cancel_event=stopped
        )
        stopped.set()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False):
            self.assertEqual(list(iterator), [])

    def test_committed_round_persists_history_without_extraction_queue(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False):
            result = handle_request(self.request(), root=root, provider_factory=lambda _: Provider())
        self.assertTrue(result["committed"])
        history = root / "users" / "alice" / "history"
        windows = [item for item in history.iterdir() if item.name != "temp"]
        self.assertEqual(len(windows), 1)
        self.assertTrue((history / "temp" / windows[0].name / "data.json").is_file())


if __name__ == "__main__":
    unittest.main()
