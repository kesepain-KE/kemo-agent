from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from provider.factory import create_provider
from provider.protocol.enums import MessageRole, StreamEventType
from provider.protocol.models import KemoRequest, MessageItem, ToolCallItem, text_from_content
from provider.schema import ProviderAuthError, ProviderError
from run.config import (
    ConfigError,
    deep_merge,
    load_config,
    provider_runtime_config,
    resolve_capability_model,
)
from run.history import HistoryError, commit_window, get_or_create_window, load_window


class MockChatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list[dict] = []

    def log_message(self, *_: object) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        type(self).requests.append(
            {"path": self.path, "body": body, "authorization": self.headers.get("Authorization")}
        )
        if self.headers.get("Authorization") == "Bearer bad-key":
            self._json(
                401,
                {"error": {"message": "invalid key", "type": "auth_error", "code": 401}},
            )
            return
        if body.get("model") == "broken-model":
            self._json(
                502,
                {"detail": {"error": {"message": "upstream failed", "type": "provider_error"}}},
            )
            return
        if body.get("stream"):
            if body.get("model") == "tool-stream":
                lines = [
                    {"id": "stream-tool", "choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "history_", "arguments": "{\"query\":\"hel"}}]}}]},
                    {"id": "stream-tool", "choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "search", "arguments": "lo\",\"limit\":2}"}}]}, "finish_reason": "tool_calls"}]},
                    {"id": "stream-tool", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}},
                ]
            else:
                lines = [
                    {"id": "stream-1", "choices": [{"delta": {"content": "你"}}]},
                    {"id": "stream-1", "choices": [{"delta": {"content": "好"}}]},
                    {"id": "stream-1", "choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
                ]
            payload = "".join(f"data: {json.dumps(line, ensure_ascii=False)}\n\n" for line in lines)
            payload += "data: [DONE]\n\n"
            raw = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        response = {
            "id": "chat-1",
            "model": body.get("model"),
            "choices": [{"message": {"content": "mock reply"}, "finish_reason": "stop"}],
        }
        if body.get("model") != "no-usage":
            response["usage"] = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
        self._json(200, response)

    def _json(self, status: int, value: object) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ServerMixin:
    server: ThreadingHTTPServer
    thread: threading.Thread

    @classmethod
    def setUpClass(cls) -> None:
        MockChatHandler.requests = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockChatHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)


class ConfigAndHistoryTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "users" / "bob" / "history").mkdir(parents=True)
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "provider": {"type": "kemo", "model": "global-model"},
                    "nested": {"a": 1, "b": 2},
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "provider": {"type": "chat", "model": "user-model"},
                    "nested": {"b": 9},
                }
            ),
            "utf-8",
        )
        (root / "users" / "bob" / "user_config.json").write_text("{}", "utf-8")
        return temporary, root

    def test_deep_user_override(self) -> None:
        _, root = self.make_root()
        config = load_config("alice", root)
        self.assertEqual(
            config["provider"],
            {"type": "chat", "model": "user-model"},
        )
        self.assertEqual(config["nested"], {"a": 1, "b": 9})

    def test_global_provider_is_not_a_user_fallback(self) -> None:
        _, root = self.make_root()
        config = load_config("bob", root)
        self.assertNotIn("provider", config)
        with self.assertRaises(ConfigError):
            provider_runtime_config(config)

    def test_dotenv_is_loaded_without_overriding_process_environment(self) -> None:
        _, root = self.make_root()
        (root / ".env").write_text(
            "# comment\nTEST_DOTENV_VALUE=from-file\nTEST_DOTENV_KEEP=from-file\n",
            "utf-8",
        )
        with patch.dict(os.environ, {"TEST_DOTENV_KEEP": "from-process"}, clear=False):
            load_config("alice", root)
            self.assertEqual(os.environ["TEST_DOTENV_VALUE"], "from-file")
            self.assertEqual(os.environ["TEST_DOTENV_KEEP"], "from-process")
        os.environ.pop("TEST_DOTENV_VALUE", None)

    def test_runtime_secret_from_environment(self) -> None:
        config = {"provider": {"type": "chat", "model": "test", "api_key_env": "UNIT_KEY"}}
        with patch.dict(os.environ, {"UNIT_KEY": "runtime-secret"}, clear=False):
            provider = provider_runtime_config(config)
        self.assertEqual(provider["api_key"], "runtime-secret")
        self.assertEqual(provider["base_url"], "https://api.openai.com/v1")
        self.assertTrue(provider["stream"])
        self.assertEqual(provider["timeout"], 120.0)

    def test_reasoning_effort_is_normalized_and_cannot_be_disabled(self) -> None:
        for configured, expected in (
            ("minimal", "minimal"),
            ("high", "high"),
            ("max", "max"),
            ("none", "medium"),
            ("xhigh", "medium"),
            ("invalid", "medium"),
            (None, "medium"),
        ):
            provider = provider_runtime_config(
                {
                    "provider": {
                        "type": "chat",
                        "model": "test",
                        "api_key": "key",
                        "reasoning_effort": configured,
                    }
                }
            )
            self.assertEqual(provider["reasoning_effort"], expected)

    def test_kemo_runtime_preserves_xhigh_logical_effort(self) -> None:
        provider = provider_runtime_config(
            {
                "provider": {
                    "type": "kemo",
                    "model": "test",
                    "api_key": "key",
                    "reasoning_effort": "xhigh",
                }
            }
        )
        self.assertEqual(provider["reasoning_effort"], "xhigh")

    def test_only_chat_and_kemo_provider_types_are_accepted(self) -> None:
        with self.assertRaisesRegex(ConfigError, "chat.*kemo"):
            provider_runtime_config(
                {"provider": {"type": "openai", "model": "test", "api_key": "key"}}
            )

    def test_inline_api_key_and_multimodal_model_precedence(self) -> None:
        config = {
            "provider": {
                "type": "kemo",
                "model": "chat-model",
                "api_key": "inline-key",
                "api_key_env": "UNIT_KEY",
            },
            "multimodal_models": {
                "vision": "vision-model",
                "image_generation": "",
            },
        }
        with patch.dict(os.environ, {"UNIT_KEY": "environment-key"}, clear=False):
            provider = provider_runtime_config(config)
        self.assertEqual(provider["api_key"], "inline-key")
        self.assertEqual(resolve_capability_model(config, "vision"), "vision-model")
        self.assertEqual(
            resolve_capability_model(config, "image_generation"),
            "chat-model",
        )

    def test_provider_base_url_environment_fallback_and_explicit_precedence(self) -> None:
        env = {
            "KEMO_BASE_URL": "http://kemo-env.test/gateway/",
            "OPENAI_BASE_URL": "https://openai-env.test/v1/",
        }
        with patch.dict(os.environ, env, clear=False):
            kemo = provider_runtime_config(
                {"provider": {"type": "kemo", "model": "test", "api_key": "key"}}
            )
            chat = provider_runtime_config(
                {"provider": {"type": "chat", "model": "test", "api_key": "key"}}
            )
            explicit = provider_runtime_config(
                {
                    "provider": {
                        "type": "kemo",
                        "base_url": "http://explicit.test/v1/",
                        "model": "test",
                        "api_key": "key",
                    }
                }
            )
        self.assertEqual(kemo["base_url"], "http://kemo-env.test/gateway")
        self.assertEqual(chat["base_url"], "https://openai-env.test/v1")
        self.assertEqual(explicit["base_url"], "http://explicit.test/v1")

    def test_history_isolates_users_sources_and_sessions(self) -> None:
        _, root = self.make_root()
        alice_cli_path, alice_cli = get_or_create_window(root, "alice", "cli", "default")
        alice_qq_path, _ = get_or_create_window(root, "alice", "qq", "default")
        bob_cli_path, _ = get_or_create_window(root, "bob", "cli", "default")
        self.assertNotEqual(alice_cli_path, alice_qq_path)
        self.assertNotEqual(alice_cli_path, bob_cli_path)
        alice_cli["text"]["messages"].append({"role": "user", "content": "private"})
        commit_window(alice_cli_path, alice_cli)
        self.assertEqual(load_window(alice_cli_path)["text"]["messages"][0]["content"], "private")
        self.assertEqual(load_window(alice_qq_path)["text"]["messages"], [])

    def test_incomplete_window_is_rejected(self) -> None:
        _, root = self.make_root()
        path, _ = get_or_create_window(root, "alice", "cli", "broken")
        (path / "tool.json").unlink()
        with self.assertRaises(HistoryError):
            load_window(path)


class ProviderTests(ServerMixin, unittest.TestCase):
    def config(self, mode: str, model: str = "mock-model", key: str = "test-key") -> dict:
        return {
            "type": mode,
            "base_url": self.base_url,
            "api_key": key,
            "model": model,
            "timeout": 5,
        }

    @staticmethod
    def request(model: str = "mock-model", *, stream: bool = False) -> KemoRequest:
        return KemoRequest(
            model=model,
            stream=stream,
            system_prompt="",
            input=[MessageItem.text(MessageRole.USER, "hello")],
        )

    def test_chat_bridge_request_and_exact_usage(self) -> None:
        provider = create_provider(self.config("chat"))
        response = provider.create(self.request())
        message = next(item for item in response.output if isinstance(item, MessageItem))
        self.assertEqual(text_from_content(message.content), "mock reply")
        self.assertTrue(response.usage.measurement.exact)
        self.assertEqual(response.usage.total_tokens, 5)
        request = MockChatHandler.requests[-1]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["authorization"], "Bearer test-key")

    def test_chat_missing_usage_is_marked_estimated(self) -> None:
        provider = create_provider(self.config("chat", model="no-usage"))
        response = provider.create(self.request("no-usage"))
        self.assertFalse(response.usage.measurement.exact)
        self.assertGreater(response.usage.total_tokens or 0, 0)

    def test_stream_parsing_and_usage(self) -> None:
        provider = create_provider(self.config("chat"))
        events = list(provider.stream(self.request(stream=True)))
        self.assertEqual(
            "".join(
                event.delta or ""
                for event in events
                if event.type == StreamEventType.OUTPUT_TEXT_DELTA
            ),
            "你好",
        )
        usage = [event.usage for event in events if event.type == StreamEventType.USAGE_UPDATED][-1]
        self.assertIsNotNone(usage)
        self.assertEqual(usage.total_tokens, 6)
        self.assertEqual(events[-1].type, StreamEventType.RESPONSE_COMPLETED)

    def test_stream_tool_arguments_are_joined(self) -> None:
        provider = create_provider(self.config("chat", model="tool-stream"))
        events = list(provider.stream(self.request("tool-stream", stream=True)))
        calls = [event.item for event in events if event.type == StreamEventType.TOOL_CALL_COMPLETED]
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0], ToolCallItem)
        self.assertEqual(calls[0].call_id, "call-1")
        self.assertEqual(calls[0].name, "history_search")
        self.assertEqual(calls[0].arguments, {"query": "hello", "limit": 2})
        self.assertEqual(events[-1].type, StreamEventType.RESPONSE_COMPLETED)

    def test_auth_error_mapping(self) -> None:
        provider = create_provider(self.config("chat", key="bad-key"))
        with self.assertRaises(ProviderAuthError) as caught:
            provider.create(self.request())
        self.assertEqual(caught.exception.status_code, 401)

    def test_gateway_error_body_is_preserved(self) -> None:
        provider = create_provider(self.config("chat", model="broken-model"))
        with self.assertRaises(ProviderError) as caught:
            provider.create(self.request("broken-model"))
        self.assertIn("upstream failed", str(caught.exception))
        self.assertIsNotNone(caught.exception.body)

    def test_kemo_mode_has_no_chat_surface(self) -> None:
        provider = create_provider(
            {
                "type": "kemo",
                "base_url": "http://127.0.0.1:8741",
                "api_key": "test-key",
                "model": "group:chat-default",
            }
        )
        self.assertEqual(provider.mode, "kemo")
        self.assertFalse(hasattr(provider, "chat"))
        self.assertFalse(hasattr(provider, "chat_stream"))


if __name__ == "__main__":
    unittest.main()
