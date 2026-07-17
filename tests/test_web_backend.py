from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

import httpx
from unittest.mock import patch

from events import RunEvent
from run.history import commit_window, empty_window
from web.app import create_app
from web.service import WebRunService


class FakeService:
    def __init__(self, *, events: list[RunEvent] | None = None, failure: Exception | None = None) -> None:
        self.events = events or []
        self.failure = failure
        self.cancel_event: threading.Event | None = None
        self.seen: dict[str, Any] = {}

    def health(self):
        if self.failure:
            raise self.failure
        return {"status": "ok", "service": "kemo-agent-web", "version": 1}

    def users(self):
        return [{"name": "alice"}]

    def sessions(self, user, *, source="web"):
        return {"user": user, "source": source, "sessions": []}

    def history(self, user, session_id, *, source="web"):
        return {"user": user, "source": source, "session_id": session_id, "messages": []}

    def stream_chat(self, user, session_id, prompt, *, cancel_event):
        self.cancel_event = cancel_event
        self.seen = {"user": user, "session_id": session_id, "prompt": prompt}
        return iter(self.events)


class WebBackendTests(unittest.TestCase):
    def request(self, app, method: str, url: str, **kwargs):
        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, url, **kwargs)
        return asyncio.run(invoke())

    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "users" / "bob" / "history").mkdir(parents=True)
        return temporary, root

    def parse_sse(self, text: str) -> list[tuple[str, dict[str, Any]]]:
        parsed = []
        for block in text.strip().split("\n\n"):
            lines = block.splitlines()
            event = lines[0].removeprefix("event: ")
            data = json.loads(lines[1].removeprefix("data: "))
            parsed.append((event, data))
        return parsed

    def test_health_does_not_touch_run_provider(self) -> None:
        fake = FakeService()
        response = self.request(create_app(service=fake), "GET", "/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIsNone(fake.cancel_event)

    def test_users_sessions_and_history_use_real_service(self) -> None:
        _, root = self.make_root()
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        window["data"]["rounds"] = 1
        commit_window(root / "users" / "alice" / "history" / "window-1", window)
        app = create_app(service=WebRunService(root))

        users = self.request(app, "GET", "/api/users")
        self.assertEqual([item["name"] for item in users.json()["users"]], ["alice", "bob"])
        sessions = self.request(app, "GET", "/api/users/alice/sessions")
        self.assertEqual(sessions.json()["sessions"][0]["session_id"], "s1")
        history = self.request(app, "GET", "/api/users/alice/sessions/s1/history")
        self.assertEqual(len(history.json()["messages"]), 2)

    def test_not_found_invalid_source_and_cross_user_session(self) -> None:
        _, root = self.make_root()
        window = empty_window("alice", "web", "private")
        window["text"]["messages"] = [{"role": "user", "content": "secret"}]
        commit_window(root / "users" / "alice" / "history" / "window-1", window)
        app = create_app(service=WebRunService(root))

        missing_user = self.request(app, "GET", "/api/users/mallory/sessions")
        self.assertEqual(missing_user.status_code, 404)
        self.assertEqual(missing_user.json()["error"]["code"], "not_found")
        invalid_source = self.request(app, "GET", "/api/users/alice/sessions?source=cli")
        self.assertEqual(invalid_source.status_code, 400)
        cross_user = self.request(app, "GET", "/api/users/bob/sessions/private/history")
        self.assertEqual(cross_user.status_code, 404)
        self.assertNotIn("secret", cross_user.text)

    def test_validation_and_internal_error_are_sanitized(self) -> None:
        invalid = self.request(create_app(service=FakeService()), "POST", "/api/chat", json={})
        self.assertEqual(invalid.status_code, 400)
        self.assertNotIn("input", invalid.text)
        secret = "API_KEY=super-secret"
        failed = self.request(
            create_app(service=FakeService(failure=RuntimeError(secret))),
            "GET",
            "/api/health",
        )
        self.assertEqual(failed.status_code, 500)
        self.assertNotIn(secret, failed.text)
        self.assertEqual(failed.json()["error"]["message"], "Web 服务处理请求失败")

    def test_web_run_generator_keeps_thread_affinity(self) -> None:
        _, root = self.make_root()
        lock = threading.RLock()
        thread_ids: list[int] = []

        def source(*_args, **_kwargs):
            with lock:
                thread_ids.append(threading.get_ident())
                try:
                    yield RunEvent(type="text_delta", content="ok")
                    yield RunEvent(type="done")
                finally:
                    thread_ids.append(threading.get_ident())

        service = WebRunService(root, event_source=source)
        events = list(
            service.stream_chat(
                "alice", "thread-affinity", "hello", cancel_event=threading.Event()
            )
        )
        self.assertEqual([event.type for event in events], ["text_delta", "done"])
        self.assertEqual(len(set(thread_ids)), 1)
        self.assertNotEqual(thread_ids[0], threading.get_ident())

    def test_sse_order_and_payload_are_preserved(self) -> None:
        events = [
            RunEvent(type="reasoning_delta", content="think"),
            RunEvent(type="text_delta", content="hello"),
            RunEvent(type="tool_call_start", tool_call_id="c1", tool_name="clock", arguments={"x": 1}),
            RunEvent(type="tool_call_result", tool_call_id="c1", tool_name="clock", result={"ok": True}),
            RunEvent(type="usage", usage={"total_tokens": 3}),
            RunEvent(type="done", usage={"total_tokens": 3}, metadata={"committed": True}),
        ]
        fake = FakeService(events=events)
        response = self.request(
            create_app(service=fake),
            "POST",
            "/api/chat",
            json={"user": "alice", "session_id": "s1", "prompt": "hello"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        parsed = self.parse_sse(response.text)
        self.assertEqual([item[0] for item in parsed], [event.type for event in events])
        self.assertEqual(parsed[2][1]["arguments"], {"x": 1})
        self.assertEqual(parsed[3][1]["result"], {"ok": True})
        self.assertEqual(fake.seen["session_id"], "s1")
        self.assertTrue(fake.cancel_event.is_set())

    def test_startup_config_defaults_and_overrides_without_provider(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"web": {"host": "127.0.0.1", "port": 1478, "log_level": "info"}}),
            "utf-8",
        )
        from start_web import main, resolve_web_config
        import argparse

        resolved = resolve_web_config(
            root,
            argparse.Namespace(host="0.0.0.0", port=19000, log_level="debug"),
        )
        self.assertEqual(resolved, {"host": "0.0.0.0", "port": 19000, "log_level": "debug"})
        with patch("uvicorn.run") as run:
            self.assertEqual(main([], root=root), 0)
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(run.call_args.kwargs["port"], 1478)

    def test_missing_terminal_and_invalid_event_become_sse_error(self) -> None:
        missing = self.request(
            create_app(service=FakeService(events=[RunEvent(type="text_delta", content="partial")])),
            "POST",
            "/api/chat",
            json={"user": "alice", "session_id": "s1", "prompt": "hello"},
        )
        parsed = self.parse_sse(missing.text)
        self.assertEqual([item[0] for item in parsed], ["text_delta", "error"])
        self.assertEqual(parsed[-1][1]["error"]["exception_type"], "MissingTerminalEvent")

        invalid = FakeService(events=["bad"])  # type: ignore[list-item]
        response = self.request(
            create_app(service=invalid),
            "POST",
            "/api/chat",
            json={"user": "alice", "session_id": "s1", "prompt": "hello"},
        )
        parsed = self.parse_sse(response.text)
        self.assertEqual(parsed[-1][0], "error")
        self.assertEqual(parsed[-1][1]["error"]["exception_type"], "InvalidRunEvent")


if __name__ == "__main__":
    unittest.main()
