from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.adapters.compat import chat_response_to_kemo, kemo_request_to_chat
from provider.schema import ChatResponse, Usage
from run.engine import handle_request


class MockProvider:
    def __init__(self, seen: list[list[dict]]) -> None:
        self.seen = seen

    def chat(self, request):
        self.seen.append(request.messages)
        return ChatResponse(
            text="ok",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model="mock",
        )

    def chat_stream(self, request):
        raise AssertionError("stream path not requested")

    def create(self, request):
        return chat_response_to_kemo(self.chat(kemo_request_to_chat(request)), request)


class PromptKnowledgeTests(unittest.TestCase):
    def test_engine_does_not_inject_knowledge_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                "config",
                "knowledge/global",
                "knowledge/share",
                "users/alice/knowledge",
                "users/alice/history",
                "users/alice/improve",
            ):
                (root / relative).mkdir(parents=True, exist_ok=True)
            (root / "config/global_soul.md").write_text("GLOBAL", "utf-8")
            (root / "users/alice/user_soul.md").write_text("USER", "utf-8")
            (root / "agents.md").write_text("AGENTS", "utf-8")
            (root / "users/alice/memory_temporary_important.md").write_text(
                "HOT", "utf-8"
            )
            (root / "config/global_config.yaml").write_text("{}", "utf-8")
            (root / "users/alice/user_config.yaml").write_text(
                json.dumps({
                    "schema_version": 1,
                    "provider": {
                        "type": "kemo",
                        "base_url": "http://127.0.0.1:1/v1",
                        "api_key_env": "KEMO_API_KEY",
                        "model": "mock",
                        "stream": False,
                    },
                }),
                "utf-8",
            )
            for relative, marker in (
                ("users/alice/knowledge/index.md", "USER_KNOWLEDGE_FILE"),
                ("knowledge/share/shared-notes.md", "SHARED_KNOWLEDGE_FILE"),
                ("knowledge/global/索引.md", "GLOBAL_KNOWLEDGE_FILE"),
            ):
                (root / relative).write_text(marker, "utf-8")

            seen: list[list[dict]] = []
            with patch.dict(os.environ, {"KEMO_API_KEY": "secret"}, clear=False):
                result = handle_request(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "knowledge",
                        "prompt": "review knowledge paths",
                    },
                    root=root,
                    provider_factory=lambda _: MockProvider(seen),
                )

        system_prompt = seen[0][0]["content"]
        for marker in (
            "USER_KNOWLEDGE_FILE",
            "SHARED_KNOWLEDGE_FILE",
            "GLOBAL_KNOWLEDGE_FILE",
        ):
            self.assertNotIn(marker, system_prompt)
        self.assertEqual(result["knowledge"]["documents"], [])


if __name__ == "__main__":
    unittest.main()
