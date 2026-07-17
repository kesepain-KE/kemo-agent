from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.schema import ChatResponse, Usage
from run.engine import handle_request, iter_request_events


TIERS = {
    "seven_days": {"days": 7, "upgrade_threshold": 3, "next": "one_month"},
    "one_month": {"days": 30, "upgrade_threshold": 10, "next": "half_year"},
    "half_year": {"days": 180, "upgrade_threshold": 60, "next": "permanent"},
    "permanent": {"days": None, "upgrade_threshold": None, "next": None},
}


class Provider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def chat(self, request):
        if self.fail:
            raise RuntimeError("provider failed")
        return ChatResponse(text="完成", usage=Usage(1, 1, 2, source="mock"), model=request.model)


class MemoryEngineTests(unittest.TestCase):
    def root(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "provider": {
                        "type": "kemo", "base_url": "http://127.0.0.1:1/v1",
                        "api_key_env": "TEST_MEMORY_KEY", "model": "mock",
                    },
                    "tools": {"enabled": False},
                    "memory": {
                        "tiers": TIERS,
                        "extraction_enabled": True,
                        "injection_enabled": True,
                    },
                }
            ),
            "utf-8",
        )
        return root

    def request(self):
        return {"user": "alice", "source": "cli", "session_id": "s", "prompt": "记住我喜欢川菜"}

    def test_only_committed_round_submits_extraction(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False), patch(
            "run.engine.submit_memory_extraction", return_value="memory-task"
        ) as submit:
            result = handle_request(self.request(), root=root, provider_factory=lambda _: Provider())
        self.assertTrue(result["committed"])
        self.assertEqual(result["memory"]["extraction_task_id"], "memory-task")
        submit.assert_called_once()
        call = submit.call_args.kwargs
        self.assertEqual(call["user_text"], "记住我喜欢川菜")
        self.assertEqual(call["assistant_text"], "完成")
        self.assertEqual(call["source"]["round"], 1)

    def test_provider_failure_and_cancel_do_not_submit(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False), patch(
            "run.engine.submit_memory_extraction"
        ) as submit:
            with self.assertRaises(RuntimeError):
                handle_request(self.request(), root=root, provider_factory=lambda _: Provider(fail=True))
            submit.assert_not_called()

        stopped = threading.Event()
        iterator = iter_request_events(
            self.request(), root=root, provider_factory=lambda _: Provider(), cancel_event=stopped
        )
        stopped.set()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False), patch(
            "run.engine.submit_memory_extraction"
        ) as submit:
            self.assertEqual(list(iterator), [])
            submit.assert_not_called()

    def test_queue_submission_failure_does_not_rollback_history(self) -> None:
        root = self.root()
        with patch.dict(os.environ, {"TEST_MEMORY_KEY": "x"}, clear=False), patch(
            "run.engine.submit_memory_extraction", side_effect=RuntimeError("queue unavailable")
        ):
            result = handle_request(self.request(), root=root, provider_factory=lambda _: Provider())
        self.assertTrue(result["committed"])
        self.assertEqual(result["memory"]["extraction_error"]["message"], "queue unavailable")
        windows = list((root / "users" / "alice" / "history").iterdir())
        self.assertEqual(len(windows), 1)


if __name__ == "__main__":
    unittest.main()
