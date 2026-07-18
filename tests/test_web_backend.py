from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

import httpx
from unittest.mock import patch

from events import RunEvent
from agents._runtime.user_packages import create_user_agent_package
from run.config import load_config
from run.cron_store import CronStore, normalize_task
from run.history import commit_window, empty_window, load_window
from run.task_plan_store import PlanStore, normalize_plan
from web.app import create_app
from web.auth import WebAuthConfig, WebAuthConfigError
from web.service import ActiveRun, WebRunService


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

    def rename_session(self, user, session_id, title, *, source="web"):
        self.seen = {"user": user, "session_id": session_id, "title": title, "source": source}
        return {
            "user": user,
            "source": source,
            "session": {
                "session_id": session_id,
                "window": "window-1",
                "title": title,
                "rounds": 1,
                "updated_at": "now",
            },
        }

    def delete_session(self, user, session_id, *, source="web"):
        self.seen = {"user": user, "session_id": session_id, "source": source}
        return {"user": user, "source": source, "session_id": session_id, "deleted": True}

    def delete_all_sessions(self, user, *, source="web"):
        self.seen = {"user": user, "source": source}
        return {
            "user": user,
            "source": source,
            "deleted": True,
            "deleted_sessions": 0,
            "deleted_windows": 0,
        }

    def settings(self, user):
        return {"user": user, "schema_version": 1}

    def stream_chat(self, user, session_id, prompt, *, cancel_event, run_id=""):
        self.cancel_event = cancel_event
        self.seen = {"user": user, "session_id": session_id, "prompt": prompt, "run_id": run_id}
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

    def test_auth_config_rejects_partial_or_unsigned_configuration(self) -> None:
        with self.assertRaisesRegex(WebAuthConfigError, "必须同时配置"):
            WebAuthConfig(username="alice")
        with self.assertRaisesRegex(WebAuthConfigError, "SESSION_SECRET"):
            WebAuthConfig(access_token="token")
        disabled = WebAuthConfig()
        self.assertFalse(disabled.enabled)
        self.assertEqual(disabled.public_summary()["enabled"], False)

    def test_token_and_password_auth_protect_business_api_and_persist_session(self) -> None:
        fake = FakeService()
        config = WebAuthConfig(
            access_token="token-secret",
            username="alice",
            password="password-secret",
            session_secret="session-secret",
            cookie_name="kemo_test_session",
        )
        app = create_app(service=fake, auth_config=config)

        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                status = await client.get("/api/auth/status")
                health = await client.get("/api/health")
                denied = await client.get("/api/users")
                denied_chat = await client.post(
                    "/api/chat",
                    json={"user": "alice", "session_id": "s1", "prompt": "hello"},
                )
                wrong = await client.post(
                    "/api/auth/bootstrap", json={"token": "wrong"}
                )
                bootstrap = await client.post(
                    "/api/auth/bootstrap", json={"token": "token-secret"}
                )
                allowed = await client.get("/api/users")
                settings = await client.get("/api/users/alice/settings")
                refreshed = await client.get("/api/auth/status")
                logout = await client.post("/api/auth/logout")
                denied_again = await client.get("/api/users")
                login = await client.post(
                    "/api/auth/login",
                    json={"username": "alice", "password": "password-secret"},
                )
                allowed_by_password = await client.get("/api/users")
                return {
                    "status": status,
                    "health": health,
                    "denied": denied,
                    "denied_chat": denied_chat,
                    "wrong": wrong,
                    "bootstrap": bootstrap,
                    "allowed": allowed,
                    "settings": settings,
                    "refreshed": refreshed,
                    "logout": logout,
                    "denied_again": denied_again,
                    "login": login,
                    "allowed_by_password": allowed_by_password,
                }

        result = asyncio.run(invoke())
        self.assertEqual(result["status"].status_code, 200)
        self.assertFalse(result["status"].json()["authenticated"])
        self.assertEqual(result["health"].status_code, 200)
        self.assertEqual(result["denied"].status_code, 401)
        self.assertEqual(result["denied"].json()["error"]["code"], "authentication_required")
        self.assertEqual(result["denied_chat"].status_code, 401)
        self.assertTrue(result["denied_chat"].headers["content-type"].startswith("application/json"))
        self.assertIsNone(fake.cancel_event)
        self.assertEqual(result["wrong"].status_code, 401)
        self.assertEqual(result["bootstrap"].status_code, 200)
        cookie = result["bootstrap"].headers["set-cookie"]
        self.assertIn("kemo_test_session=", cookie)
        self.assertIn("httponly", cookie.lower())
        self.assertIn("samesite=lax", cookie.lower())
        self.assertEqual(result["allowed"].status_code, 200)
        self.assertTrue(result["settings"].json()["authentication"]["enabled"])
        for secret in ("token-secret", "password-secret", "session-secret"):
            self.assertNotIn(secret, result["settings"].text)
        self.assertTrue(result["refreshed"].json()["authenticated"])
        self.assertEqual(result["logout"].status_code, 200)
        self.assertEqual(result["denied_again"].status_code, 401)
        self.assertEqual(result["login"].status_code, 200)
        self.assertEqual(result["allowed_by_password"].status_code, 200)

    def test_authenticated_config_edit_is_redacted_atomic_and_conflict_safe(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "kemo",
                        "base_url": "http://127.0.0.1:8741/v1",
                        "model": "global-model",
                        "timeout": 120,
                        "stream": False,
                    },
                    "tools": {"enabled": True, "max_iterations": 8, "timeout": 60},
                }
            ),
            "utf-8",
        )
        user_config_path = root / "users" / "alice" / "user_config.json"
        user_config_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {"model": "old-model", "api_key": "disk-secret"},
                }
            ),
            "utf-8",
        )
        app = create_app(
            service=WebRunService(root, config_write_enabled=True),
            auth_config=WebAuthConfig(
                access_token="edit-token",
                session_secret="session-secret",
            ),
        )

        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                denied = await client.get("/api/users/alice/config/full")
                await client.post("/api/auth/bootstrap", json={"token": "edit-token"})
                loaded = await client.get("/api/users/alice/config/full")
                payload = loaded.json()
                candidate = payload["config"]
                candidate["provider"]["model"] = "new-model"
                saved = await client.put(
                    "/api/users/alice/config",
                    json={"config": candidate, "etag": payload["etag"]},
                )
                stale = await client.put(
                    "/api/users/alice/config",
                    json={"config": candidate, "etag": payload["etag"]},
                )
                invalid_candidate = saved.json()["config"]
                invalid_candidate["schema_version"] = 2
                invalid = await client.put(
                    "/api/users/alice/config",
                    json={"config": invalid_candidate, "etag": saved.json()["etag"]},
                )
                return denied, loaded, saved, stale, invalid

        denied, loaded, saved, stale, invalid = asyncio.run(invoke())
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(loaded.status_code, 200)
        self.assertTrue(loaded.json()["write_enabled"])
        self.assertEqual(loaded.json()["config"]["provider"]["api_key"], "***")
        self.assertNotIn("disk-secret", loaded.text)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(invalid.status_code, 400)
        stored = json.loads(user_config_path.read_text("utf-8"))
        self.assertEqual(stored["provider"]["api_key"], "disk-secret")
        self.assertEqual(stored["provider"]["model"], "new-model")
        self.assertEqual(load_config("alice", root)["provider"]["model"], "new-model")

    def test_config_write_stays_closed_without_web_authentication(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "kemo",
                        "base_url": "http://127.0.0.1:8741/v1",
                        "model": "model",
                    },
                }
            ),
            "utf-8",
        )
        path = root / "users" / "alice" / "user_config.json"
        path.write_text('{"schema_version":1}', "utf-8")
        app = create_app(service=WebRunService(root, config_write_enabled=True))
        loaded = self.request(app, "GET", "/api/users/alice/config/full")
        blocked = self.request(
            app,
            "PUT",
            "/api/users/alice/config",
            json={"config": loaded.json()["config"], "etag": loaded.json()["etag"]},
        )
        self.assertFalse(loaded.json()["write_enabled"])
        self.assertEqual(blocked.status_code, 403)

    def test_cookie_names_isolate_web_instances(self) -> None:
        first = create_app(
            service=FakeService(),
            auth_config=WebAuthConfig(
                access_token="token",
                session_secret="shared-secret",
                cookie_name="instance_one",
            ),
        )
        second = create_app(
            service=FakeService(),
            auth_config=WebAuthConfig(
                access_token="token",
                session_secret="shared-secret",
                cookie_name="instance_two",
            ),
        )

        async def invoke():
            first_transport = httpx.ASGITransport(app=first, raise_app_exceptions=False)
            second_transport = httpx.ASGITransport(app=second, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=first_transport, base_url="http://test"
            ) as first_client:
                login = await first_client.post(
                    "/api/auth/bootstrap", json={"token": "token"}
                )
                cookie = first_client.cookies.get("instance_one")
            async with httpx.AsyncClient(
                transport=second_transport, base_url="http://test"
            ) as second_client:
                second_client.cookies.set("instance_one", cookie, domain="test.local")
                denied = await second_client.get("/api/users")
            return login, denied

        login, denied = asyncio.run(invoke())
        self.assertEqual(login.status_code, 200)
        self.assertEqual(denied.status_code, 401)

    def test_frontend_dist_and_spa_routes_are_served(self) -> None:
        _, root = self.make_root()
        dist = root / "web" / "frontend" / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            "<!doctype html><html><body><div id='root'>kemo UI</div></body></html>",
            "utf-8",
        )
        (dist / "assets" / "app.js").write_text("window.KEMO = true", "utf-8")
        (dist / "kemo-agent.jpg").write_bytes(b"kemo-image")
        app = create_app(root=root, service=FakeService())

        home = self.request(app, "GET", "/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("text/html", home.headers["content-type"])
        self.assertIn("kemo UI", home.text)

        tasks = self.request(app, "GET", "/tasks?user=alice")
        self.assertEqual(tasks.status_code, 200)
        self.assertIn("kemo UI", tasks.text)

        asset = self.request(app, "GET", "/assets/app.js")
        self.assertEqual(asset.status_code, 200)
        self.assertIn("text/javascript", asset.headers["content-type"])
        self.assertEqual(asset.text, "window.KEMO = true")
        image = self.request(app, "GET", "/kemo-agent.jpg")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.content, b"kemo-image")

        missing_api = self.request(app, "GET", "/api/does-not-exist")
        self.assertEqual(missing_api.status_code, 404)
        self.assertEqual(missing_api.headers["content-type"], "application/json")
        self.assertEqual(missing_api.json()["error"]["code"], "not_found")

    def test_frontend_reports_when_build_is_missing(self) -> None:
        _, root = self.make_root()
        response = self.request(
            create_app(root=root, service=FakeService()),
            "GET",
            "/",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "ui_not_built")

    def test_users_sessions_and_history_use_real_service(self) -> None:
        _, root = self.make_root()
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        window["think"]["rounds"] = [{"round": 1, "content": "inspect first"}]
        window["tool"]["rounds"] = [{
            "round": 1,
            "calls": [{
                "id": "call-1",
                "name": "clock",
                "arguments": {"zone": "local"},
                "result": "x" * 5200,
                "status": "completed",
                "elapsed_ms": 12,
            }],
        }]
        window["data"]["rounds"] = 1
        commit_window(root / "users" / "alice" / "history" / "window-1", window)
        app = create_app(service=WebRunService(root))

        users = self.request(app, "GET", "/api/users")
        self.assertEqual([item["name"] for item in users.json()["users"]], ["alice", "bob"])
        sessions = self.request(app, "GET", "/api/users/alice/sessions")
        self.assertEqual(sessions.json()["sessions"][0]["session_id"], "s1")
        history = self.request(app, "GET", "/api/users/alice/sessions/s1/history")
        self.assertEqual(len(history.json()["messages"]), 2)
        trace = history.json()["round_traces"][0]
        self.assertEqual(trace["reasoning"], "inspect first")
        self.assertEqual(trace["tools"][0]["call_id"], "call-1")
        self.assertEqual(trace["tools"][0]["status"], "success")
        self.assertEqual(len(trace["tools"][0]["result_text"]), 5000)
        self.assertTrue(trace["tools"][0]["result_truncated"])

    def test_session_rename_is_persisted_without_changing_sort_time(self) -> None:
        _, root = self.make_root()
        window_dir = root / "users" / "alice" / "history" / "window-1"
        commit_window(window_dir, empty_window("alice", "web", "s1"))
        app = create_app(service=WebRunService(root))
        before = self.request(app, "GET", "/api/users/alice/sessions").json()["sessions"][0]
        stale_window = load_window(window_dir)

        response = self.request(
            app,
            "PATCH",
            "/api/users/alice/sessions/s1",
            json={"title": "  我的重要对话  "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["session"]["title"], "我的重要对话")
        after = self.request(app, "GET", "/api/users/alice/sessions").json()["sessions"][0]
        self.assertEqual(after["title"], "我的重要对话")
        self.assertEqual(after["updated_at"], before["updated_at"])
        stored = json.loads((window_dir / "data.json").read_text("utf-8"))
        self.assertEqual(stored["title"], "我的重要对话")
        commit_window(window_dir, stale_window)
        stored_after_stale_commit = json.loads((window_dir / "data.json").read_text("utf-8"))
        self.assertEqual(stored_after_stale_commit["title"], "我的重要对话")

    def test_session_rename_validates_title(self) -> None:
        _, root = self.make_root()
        commit_window(
            root / "users" / "alice" / "history" / "window-1",
            empty_window("alice", "web", "s1"),
        )
        app = create_app(service=WebRunService(root))
        for title in ("", "   ", "bad\nname", "x" * 81):
            with self.subTest(title=repr(title)):
                response = self.request(
                    app,
                    "PATCH",
                    "/api/users/alice/sessions/s1",
                    json={"title": title},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_session_delete_removes_all_matching_windows_and_preserves_other_users(self) -> None:
        _, root = self.make_root()
        for name in ("window-1", "window-2"):
            commit_window(
                root / "users" / "alice" / "history" / name,
                empty_window("alice", "web", "s1"),
            )
        bob_window = root / "users" / "bob" / "history" / "window-1"
        commit_window(bob_window, empty_window("bob", "web", "s1"))
        app = create_app(service=WebRunService(root))

        response = self.request(app, "DELETE", "/api/users/alice/sessions/s1")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["deleted"])
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/sessions").json()["sessions"],
            [],
        )
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/sessions/s1/history").status_code,
            404,
        )
        self.assertTrue(bob_window.is_dir())
        self.assertEqual(
            self.request(app, "DELETE", "/api/users/alice/sessions/s1").status_code,
            404,
        )

    def test_delete_all_sessions_is_scoped_and_reports_counts(self) -> None:
        _, root = self.make_root()
        for directory, session_id in (
            ("window-1", "s1"),
            ("window-2", "s1"),
            ("window-3", "s2"),
        ):
            commit_window(
                root / "users" / "alice" / "history" / directory,
                empty_window("alice", "web", session_id),
            )
        bob_window = root / "users" / "bob" / "history" / "window-1"
        commit_window(bob_window, empty_window("bob", "web", "s1"))
        app = create_app(service=WebRunService(root))

        response = self.request(app, "DELETE", "/api/users/alice/sessions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted_sessions"], 2)
        self.assertEqual(response.json()["deleted_windows"], 3)
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/sessions").json()["sessions"],
            [],
        )
        self.assertTrue(bob_window.is_dir())

    def test_active_session_cannot_be_deleted(self) -> None:
        _, root = self.make_root()
        window_dir = root / "users" / "alice" / "history" / "window-1"
        commit_window(window_dir, empty_window("alice", "web", "busy"))
        service = WebRunService(root)
        service._active_runs["run_busy_123"] = ActiveRun("run_busy_123", "alice", "busy")
        app = create_app(service=service)

        response = self.request(app, "DELETE", "/api/users/alice/sessions/busy")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "conflict")
        self.assertTrue(window_dir.is_dir())

        bulk_response = self.request(app, "DELETE", "/api/users/alice/sessions")
        self.assertEqual(bulk_response.status_code, 409)
        self.assertEqual(bulk_response.json()["error"]["code"], "conflict")
        self.assertTrue(window_dir.is_dir())

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

    def test_web_guidance_queue_is_user_scoped_and_removed_after_run(self) -> None:
        _, root = self.make_root()
        seen: list[str] = []

        def source(request, **_kwargs):
            seen.append(request["_guidance_queue"].get(timeout=2))
            yield RunEvent(type="done", metadata={"run_id": request["run_id"]})

        service = WebRunService(root, event_source=source)
        iterator = service.stream_chat(
            "alice",
            "guided-session",
            "start",
            cancel_event=threading.Event(),
            run_id="run_guidance_123",
        )
        captured: list[RunEvent] = []
        worker = threading.Thread(target=lambda: captured.extend(iterator))
        worker.start()
        queued = service.submit_guidance("alice", "run_guidance_123", "adjust target")
        with self.assertRaisesRegex(Exception, "运行不存在"):
            service.submit_guidance("bob", "run_guidance_123", "cross user")
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(seen, ["adjust target"])
        self.assertEqual(captured[-1].metadata["run_id"], "run_guidance_123")
        with self.assertRaisesRegex(Exception, "运行不存在"):
            service.submit_guidance("alice", "run_guidance_123", "too late")

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

    def test_startup_options_without_provider(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"web": {"host": "127.0.0.1", "port": 1478, "log_level": "info"}}),
            "utf-8",
        )
        import start_web

        with (
            patch.object(start_web, "project_root", return_value=root),
            patch.object(start_web, "_check_users", return_value=True),
            patch.object(start_web, "_can_bind", return_value=(True, "")),
            patch("uvicorn.run") as run,
        ):
            self.assertEqual(
                start_web.main(
                    [
                        "--host=0.0.0.0",
                        "--port=19000",
                        "--log-level=debug",
                        "--no-host",
                    ]
                ),
                0,
            )
        self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(run.call_args.kwargs["port"], 19000)
        self.assertEqual(run.call_args.kwargs["log_level"], "debug")

    def test_startup_uses_web_environment_defaults_and_cli_overrides(self) -> None:
        _, root = self.make_root()
        import start_web

        with (
            patch.dict(os.environ, {"WEB_HOST": "0.0.0.0", "WEB_PORT": "19001"}),
            patch.object(start_web, "project_root", return_value=root),
            patch.object(start_web, "_check_users", return_value=True),
            patch.object(start_web, "_can_bind", return_value=(True, "")),
            patch("uvicorn.run") as run,
        ):
            self.assertEqual(start_web.main(["--no-host"]), 0)
        self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(run.call_args.kwargs["port"], 19001)

        with (
            patch.dict(os.environ, {"WEB_HOST": "127.0.0.2", "WEB_PORT": "19002"}),
            patch.object(start_web, "project_root", return_value=root),
            patch.object(start_web, "_check_users", return_value=True),
            patch.object(start_web, "_can_bind", return_value=(True, "")),
            patch("uvicorn.run") as run,
        ):
            self.assertEqual(
                start_web.main(["--host=127.0.0.3", "--port=19003", "--no-host"]),
                0,
            )
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.3")
        self.assertEqual(run.call_args.kwargs["port"], 19003)

    def test_startup_rejects_invalid_web_port_environment(self) -> None:
        _, root = self.make_root()
        import start_web

        with (
            patch.dict(os.environ, {"WEB_PORT": "not-a-port"}),
            patch.object(start_web, "project_root", return_value=root),
            patch.object(start_web, "_check_users") as check_users,
            patch("uvicorn.run") as run,
        ):
            self.assertEqual(start_web.main(["--no-host"]), 1)
        check_users.assert_not_called()
        run.assert_not_called()

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

    def test_observer_endpoints_return_real_sanitized_state(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "openai",
                        "base_url": "https://example.test/v1",
                        "model": "test-model",
                        "api_key": "super-secret",
                        "timeout": 30,
                    },
                    "tools": {"enabled": True, "max_iterations": 4, "timeout": 10},
                    "knowledge": {
                        "enabled": True,
                        "use_shared": False,
                        "use_global": True,
                        "max_items": 3,
                        "max_chars": 2000,
                    },
                    "skills": {
                        "shared_whitelist": ["observer"],
                        "user_whitelist": [],
                    },
                    "expand": {
                        "global_whitelist": [],
                        "shared_whitelist": [],
                    },
                    "perception": {"global_whitelist": ["runtime"]},
                    "kemo_graph": {"enabled": True},
                    "memory": {"extraction_enabled": True, "injection_enabled": True},
                    "task_plan": {"auto_accept": False, "max_steps": 8},
                    "cron": {"enabled": True, "auto_start": False},
                    "agents": {"n4_token_limit": 100000},
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "knowledge").mkdir()
        (root / "users" / "alice" / "knowledge" / "notes.md").write_text("# Alice Notes\nprivate index", "utf-8")
        (root / "shared_knowledge").mkdir()
        (root / "shared_knowledge" / "team.md").write_text("# Team Shared", "utf-8")
        (root / "global_knowledge").mkdir()
        (root / "global_knowledge" / "shared.md").write_text("# Shared", "utf-8")
        (root / "global_sense").mkdir()
        (root / "global_sense" / "README.md").write_text("observer core", "utf-8")
        for module_name in ("runtime", "network"):
            module = root / "global_sense" / module_name
            module.mkdir()
            (module / "status.md").write_text(module_name, "utf-8")
        (root / "global_sense" / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    registry.add_perception(Path(__file__).resolve().parent)\n",
            "utf-8",
        )
        shared_skills = root / "shared_skills"
        shared_skills.mkdir()
        (shared_skills / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    registry.add_skills('shared', Path(__file__).resolve().parent)\n",
            "utf-8",
        )
        for skill_name in ("observer", "filtered"):
            skill = shared_skills / skill_name
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                f"# {skill_name}\n{skill_name} description", "utf-8"
            )
        plugin = root / "plugins" / "clock"
        plugin.mkdir(parents=True)
        clock_manifest = {
            "name": "clock",
            "description": "read time",
            "input_schema": {"type": "object", "properties": {}},
            "version": "1",
            "enabled": True,
            "entrypoint": "tool.py:run",
        }
        (plugin / "SKILL.md").write_text(
            "# clock\nread time\n\n## Tool\n\n```json\n"
            + json.dumps(clock_manifest)
            + "\n```\n",
            "utf-8",
        )
        PlanStore(root, "alice").create(
            normalize_plan(
                title="Observer plan",
                description="safe metadata",
                user="alice",
                steps=[
                    {
                        "step_id": "step_1",
                        "title": "Inspect",
                        "description": "read only",
                        "critical": True,
                    }
                ],
            )
        )
        CronStore(root, "alice").create(
            normalize_task(
                title="Daily check",
                prompt="do not expose this prompt",
                user="alice",
                schedule={"type": "daily", "time": "09:00", "timezone": "Asia/Shanghai"},
            )
        )
        window = empty_window("alice", "web", "observer-session")
        window["data"]["rounds"] = 1
        window["data"]["token_usage"] = {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
            "estimated": False,
        }
        commit_window(root / "users" / "alice" / "history" / "observer-window", window)
        (root / "users" / "alice" / "history" / "observer-window" / "context_summary.json").write_text(
            json.dumps(
                {
                    "source_hash": "hash",
                    "covered_rounds": [1],
                    "created_at": "2026-07-18T00:00:00+00:00",
                    "summary": {"narrative": "must not be exposed"},
                }
            ),
            "utf-8",
        )
        memory_dir = root / "users" / "alice" / "improve" / "seven_days"
        memory_dir.mkdir(parents=True)
        (memory_dir / "data.json").write_text(
            json.dumps(
                [
                    {
                        "id": "memory-1",
                        "content": "safe memory preview",
                        "tier_weight": 2,
                        "created_at": "2026-07-18T00:00:00+00:00",
                    }
                ]
            ),
            "utf-8",
        )
        create_user_agent_package(
            root,
            "alice",
            {
                "name": "observer_agent",
                "description": "user hot-plug agent",
                "instruction": "Return JSON.",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
        )
        app = create_app(
            service=WebRunService(
                root,
                runtime_status_provider=lambda: {
                    "state": "running",
                    "components": {"cron": {"name": "cron", "state": "running"}},
                },
            )
        )

        overview = self.request(
            app,
            "GET",
            "/api/users/alice/overview?session_id=observer-session",
        )
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["counts"]["knowledge_documents"], 3)
        self.assertEqual(overview.json()["counts"]["enabled_tools"], 1)
        self.assertEqual(overview.json()["context"]["usage"]["total_tokens"], 1500)
        self.assertEqual(overview.json()["context"]["rounds"], 1)
        self.assertEqual(overview.json()["context"]["round_limit"], 30)
        self.assertEqual(overview.json()["agents"][0]["name"], "observer_agent")
        self.assertEqual(overview.json()["agents"][0]["source"], "user")
        self.assertEqual(overview.json()["summary_cache"]["covered_rounds"], [1])
        self.assertNotIn("must not be exposed", overview.text)
        self.assertEqual(overview.json()["runtime_host"]["state"], "running")

        tasks = self.request(app, "GET", "/api/users/alice/tasks")
        self.assertEqual(len(tasks.json()["plans"]), 1)
        self.assertEqual(len(tasks.json()["cron_tasks"]), 1)
        self.assertNotIn("do not expose", tasks.text)

        knowledge = self.request(app, "GET", "/api/users/alice/knowledge")
        self.assertEqual(knowledge.json()["summary"]["user_documents"], 1)
        self.assertEqual(knowledge.json()["summary"]["shared_documents"], 1)
        self.assertEqual(knowledge.json()["summary"]["global_documents"], 1)
        self.assertEqual(
            [item["scope"] for item in knowledge.json()["documents"]],
            ["user", "shared", "global"],
        )
        self.assertEqual(
            [item["active_for_main_agent"] for item in knowledge.json()["documents"]],
            [True, False, True],
        )
        self.assertEqual(
            knowledge.json()["source_policy"]["knowledge"]["effective_scopes"],
            ["user", "global"],
        )
        self.assertEqual(knowledge.json()["extensions"]["kemo_graph"], "not_connected")
        self.assertNotIn("private index", knowledge.text)

        skills = self.request(app, "GET", "/api/users/alice/skills")
        self.assertEqual(skills.json()["tools"][0]["name"], "clock")
        self.assertEqual(skills.json()["prompt_summary"]["registered"], 2)
        self.assertEqual(skills.json()["prompt_summary"]["active"], 1)
        self.assertNotIn("project", skills.text)
        sense = self.request(app, "GET", "/api/users/alice/sense")
        self.assertTrue(sense.json()["core_available"])
        self.assertEqual(
            [item["layer"] for item in sense.json()["sources"]],
            ["global", "global"],
        )
        self.assertEqual(sense.json()["summary"]["global"], 2)
        self.assertEqual(sense.json()["summary"]["enabled"], 1)
        self.assertEqual(sense.json()["core_files"], 2)
        self.assertEqual(
            {item["id"]: item["status"] for item in sense.json()["sources"]},
            {"network": "filtered", "runtime": "active"},
        )
        self.assertNotIn('"project"', sense.text)

        prompt = self.request(app, "GET", "/api/users/alice/prompt/sections")
        self.assertEqual(len(prompt.json()["sections"]), 14)
        self.assertNotIn("safe memory preview", prompt.text)
        self.assertIn("expand", prompt.json())
        memory = self.request(app, "GET", "/api/users/alice/memory/summary")
        self.assertEqual(memory.json()["summary"]["seven_days"], 1)
        self.assertEqual(memory.json()["items"][0]["tier_weight"], 2)

        settings = self.request(app, "GET", "/api/users/alice/settings")
        self.assertEqual(settings.json()["provider"]["model"], "test-model")
        self.assertFalse(settings.json()["authentication"]["enabled"])
        self.assertEqual(
            settings.json()["source_policy"]["kemo_graph"]["status"],
            "not_connected",
        )
        self.assertNotIn("super-secret", settings.text)
        self.assertNotIn("api_key", settings.text)


if __name__ == "__main__":
    unittest.main()
