from __future__ import annotations

import asyncio
import copy
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
from run.cron_store import CronStore, normalize_task
from run.history import (
    commit_window,
    empty_window,
    load_window,
    runtime_window_path,
    synthesize_items,
)
from run.prompt import PROMPT_SECTION_ORDER
from run.task_plan_store import PlanStore, normalize_plan
from web.app import create_app
from web.auth import WebAuthConfig, WebAuthConfigError
from web.service import ActiveRun, WebRunService, _usage_cache_tokens


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

    def sessions(self, user, *, source="web", query=""):
        return {"user": user, "source": source, "query": query, "sessions": []}

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

    def delete_session(self, user, session_id, *, source="web", client_id=""):
        self.seen = {"user": user, "session_id": session_id, "source": source}
        if client_id:
            self.seen["client_id"] = client_id
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

    def compress_session(self, user, session_id, *, source="web"):
        self.seen = {"user": user, "session_id": session_id, "source": source}
        return {
            "user": user,
            "source": source,
            "session_id": session_id,
            "requested": True,
            "compressed": True,
            "rounds_removed": 2,
            "summary_cache_exists": True,
            "context": {"rounds_removed": 2},
        }

    def extract_session_memory(self, user, session_id, *, source="web"):
        self.seen = {"user": user, "session_id": session_id, "source": source}
        return {
            "status": "completed",
            "user": user,
            "source": source,
            "session_id": session_id,
            "round": 2,
            "candidates": 1,
            "extraction": {"status": "completed", "candidate_count": 1},
        }

    def settings(self, user):
        return {"user": user, "schema_version": 1}

    def stream_chat(self, user, session_id, prompt, *, cancel_event, run_id="", client_id="", **kwargs):
        self.cancel_event = cancel_event
        self.seen = {"user": user, "session_id": session_id, "prompt": prompt, "run_id": run_id}
        if client_id:
            self.seen["client_id"] = client_id
        self.seen.update(kwargs)
        return iter(self.events)

    def stream_plan(self, user, session_id, plan_id, *, cancel_event, run_id="", client_id=""):
        self.cancel_event = cancel_event
        self.seen = {
            "user": user,
            "session_id": session_id,
            "plan_id": plan_id,
            "run_id": run_id,
        }
        if client_id:
            self.seen["client_id"] = client_id
        return iter(self.events)


class WebBackendTests(unittest.TestCase):
    def test_upload_avoids_overwrite_and_chat_validates_attached_file_paths(self) -> None:
        _, root = self.make_root()
        captured: list[dict[str, Any]] = []

        def source(request, **_kwargs):
            captured.append(request)
            yield RunEvent(type="done")

        service = WebRunService(root, event_source=source)
        first = service.save_file("alice", "file_upload", "note.txt", b"first")
        second = service.save_file("alice", "file_upload", "note.txt", b"second")
        self.assertEqual(first["path"], "note.txt")
        self.assertFalse(first["renamed"])
        self.assertEqual(second["path"], "note (2).txt")
        self.assertTrue(second["renamed"])
        self.assertEqual((root / "users" / "alice" / "file_upload" / "note.txt").read_bytes(), b"first")
        self.assertEqual((root / "users" / "alice" / "file_upload" / "note (2).txt").read_bytes(), b"second")

        events = list(
            service.stream_chat(
                "alice",
                "upload-session",
                "读取附件",
                cancel_event=threading.Event(),
                uploaded_files=[second["path"]],
            )
        )
        self.assertEqual([event.type for event in events], ["done"])
        attached = captured[0]["uploaded_files"][0]
        self.assertEqual(attached["name"], "note (2).txt")
        self.assertEqual(attached["path"], "users/alice/file_upload/note (2).txt")
        self.assertEqual(attached["size"], 6)
        self.assertEqual(attached["mime_type"], "text/plain")
        self.assertFalse(attached["is_image"])
        self.assertRegex(attached["asset_id"], r"^asset_[0-9a-f]{32}$")
        self.assertRegex(attached["checksum_sha256"], r"^[0-9a-f]{64}$")
        attachment_only = list(
            service.stream_chat(
                "alice",
                "attachment-only",
                "",
                cancel_event=threading.Event(),
                uploaded_files=[first["path"]],
            )
        )
        self.assertEqual([event.type for event in attachment_only], ["done"])
        self.assertEqual(captured[-1]["prompt"], "")
        self.assertEqual(captured[-1]["uploaded_files"][0]["name"], "note.txt")
        with self.assertRaisesRegex(Exception, "上传文件不存在"):
            list(
                service.stream_chat(
                    "alice",
                    "missing-upload",
                    "读取附件",
                    cancel_event=threading.Event(),
                    uploaded_files=["missing.txt"],
                )
            )

    def test_usage_cache_tokens_prefers_normalized_fields_and_preserves_zero(self) -> None:
        self.assertEqual(
            _usage_cache_tokens({"cached_input_tokens": 12, "provider_raw": [{"cached_tokens": 3}]}),
            12,
        )
        self.assertEqual(_usage_cache_tokens({"cached_prompt_tokens": 0}), 0)
        self.assertEqual(
            _usage_cache_tokens({"provider_raw": [{"cache_read_input_tokens": 7}]}),
            7,
        )

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

    def test_restart_endpoint_launches_detached_helper_for_requested_port(self) -> None:
        _, root = self.make_root()
        app = create_app(root=root, service=FakeService())
        with patch("web.app._spawn_restart_helper", return_value=4321) as launcher:
            response = self.request(
                app,
                "POST",
                "/api/system/restart",
                json={"port": 1360},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "port": 1360, "helper_pid": 4321})
        launcher.assert_called_once_with(root.resolve(), 1360)

    def test_restart_endpoint_rejects_invalid_port_and_active_chat(self) -> None:
        invalid = self.request(
            create_app(service=FakeService()),
            "POST",
            "/api/system/restart",
            json={"port": 0},
        )
        self.assertEqual(invalid.status_code, 400)

        class ActiveService(FakeService):
            def has_active_runs(self) -> bool:
                return True

        active_app = create_app(service=ActiveService())
        with patch("web.app._spawn_restart_helper", return_value=4321) as launcher:
            active = self.request(
                active_app,
                "POST",
                "/api/system/restart",
                json={"port": 1360},
            )
            self.assertEqual(active.status_code, 409)
            self.assertEqual(active.json()["error"]["code"], "conflict")
            launcher.assert_not_called()

            forced = self.request(
                active_app,
                "POST",
                "/api/system/restart",
                json={"port": 1360, "force": True},
            )
        self.assertEqual(forced.status_code, 200, forced.text)
        self.assertEqual(forced.json(), {"ok": True, "port": 1360, "helper_pid": 4321})
        launcher.assert_called_once()

    def test_agents_expose_runtime_details_and_only_delete_user_layer(self) -> None:
        _, root = self.make_root()
        create_user_agent_package(
            root,
            "bob",
            {
                "name": "builtin_agent",
                "version": "2.1.0",
                "description": "global runtime agent",
                "instruction": "Apply the global agent rules.",
                "trigger_condition": "上下文达到全局阈值时",
            },
        )
        (root / "agents").mkdir()
        (root / "users" / "bob" / "agents" / "builtin_agent").replace(
            root / "agents" / "builtin_agent"
        )
        create_user_agent_package(
            root,
            "alice",
            {
                "name": "custom_agent",
                "version": "1.4.0",
                "description": "user runtime agent",
                "instruction": "Apply the user agent rules.",
                "trigger_condition": "用户明确指定 custom_agent 时",
            },
        )
        app = create_app(service=WebRunService(root))

        response = self.request(app, "GET", "/api/users/alice/agents")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["summary"], {"total": 2, "enabled": 2, "global": 1, "user": 1})
        user_agent = next(item for item in payload["agents"] if item["name"] == "custom_agent")
        self.assertEqual(user_agent["version"], "1.4.0")
        self.assertEqual(user_agent["trigger"], "用户明确指定 custom_agent 时")
        self.assertIn("Apply the user agent rules.", user_agent["rules"])
        self.assertEqual(user_agent["executor"], "builtin:llm")

        rejected = self.request(app, "DELETE", "/api/users/alice/agents/builtin_agent")
        self.assertEqual(rejected.status_code, 404, rejected.text)
        self.assertTrue((root / "agents" / "builtin_agent").is_dir())

        deleted = self.request(app, "DELETE", "/api/users/alice/agents/custom_agent")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["deleted"])
        self.assertFalse((root / "users" / "alice" / "agents" / "custom_agent").exists())
        self.assertEqual(
            self.request(app, "DELETE", "/api/users/alice/agents/custom_agent").status_code,
            404,
        )

    def test_skill_registry_management_respects_category_permissions_and_whitelists(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"schema_version": 1, "tools": {"enabled": True, "timeout": 30}}),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1, "plugins": {"whitelist": []}, "skills": {"shared_whitelist": []}}),
            "utf-8",
        )
        plugin = root / "plugins" / "clock"
        plugin.mkdir(parents=True)
        manifest = {
            "name": "clock",
            "description": "读取当前时间",
            "input_schema": {"type": "object", "properties": {}},
            "version": "1.0.0",
            "enabled": True,
            "entrypoint": "tool.py:run",
        }
        (plugin / "SKILL.md").write_text(
            "# clock\n\n读取当前时间。\n\n## Tool\n\n```json\n"
            + json.dumps(manifest, ensure_ascii=False)
            + "\n```\n",
            "utf-8",
        )
        (plugin / "tool.py").write_text("def run(*, context):\n    return {'ok': True}\n", "utf-8")

        shared_root = root / "shared_skills"
        shared_root.mkdir()
        (shared_root / "register.py").write_text(
            "from pathlib import Path\n\ndef register(registry):\n    registry.add_skills('shared', Path(__file__).resolve().parent)\n",
            "utf-8",
        )
        shared = shared_root / "observer"
        shared.mkdir()
        (shared / "SKILL.md").write_text("# observer\n\n共享观察技能。\n", "utf-8")

        agent_skill = root / "users" / "alice" / "user_skills" / "agent_create" / "generated"
        agent_skill.mkdir(parents=True)
        (agent_skill / "SKILL.md").write_text("# generated\n\n智能体生成技能。\n", "utf-8")
        user_skill = root / "users" / "alice" / "user_skills" / "user_create" / "manual"
        user_skill.mkdir(parents=True)
        (user_skill / "SKILL.md").write_text("# manual\n\n用户自建技能。\n", "utf-8")

        app = create_app(service=WebRunService(root))
        listed = self.request(app, "GET", "/api/users/alice/skills")
        self.assertEqual(listed.status_code, 200, listed.text)
        payload = listed.json()
        self.assertEqual(payload["catalog_summary"]["total"], 4)
        self.assertEqual(
            {item["category"] for item in payload["items"]},
            {"builtin", "shared", "agent_generated", "user_created"},
        )

        preview = self.request(
            app,
            "GET",
            "/api/users/alice/skills/builtin/document?name=clock",
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn("读取当前时间", preview.json()["content"])
        archive = self.request(
            app,
            "GET",
            "/api/users/alice/skills/builtin/download?name=clock",
        )
        self.assertEqual(archive.status_code, 200, archive.text)
        self.assertTrue(archive.content.startswith(b"PK"))

        disabled = self.request(
            app,
            "PATCH",
            "/api/users/alice/skills/builtin/enabled?name=clock",
            json={"enabled": False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        stored = json.loads((root / "users" / "alice" / "user_config.json").read_text("utf-8"))
        self.assertEqual(stored["plugins"]["whitelist"], ["__kemo_none__"])
        refreshed = self.request(app, "GET", "/api/users/alice/skills").json()
        self.assertFalse(next(item for item in refreshed["items"] if item["id"] == "builtin:clock")["enabled"])

        updated = self.request(
            app,
            "PUT",
            "/api/users/alice/skills/user_created/document?name=user_create%2Fmanual",
            json={"content": "# manual\n\n更新后的技能正文。\n"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertIn("更新后的技能正文", (user_skill / "SKILL.md").read_text("utf-8"))
        rejected = self.request(
            app,
            "DELETE",
            "/api/users/alice/skills/builtin?name=clock",
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)
        deleted = self.request(
            app,
            "DELETE",
            "/api/users/alice/skills/user_created?name=user_create%2Fmanual",
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertFalse(user_skill.exists())

    def test_auth_config_rejects_partial_password_and_generates_session_secret(self) -> None:
        with self.assertRaisesRegex(WebAuthConfigError, "必须同时配置"):
            WebAuthConfig(username="alice")
        generated = WebAuthConfig(access_token="token")
        another = WebAuthConfig(access_token="token")
        self.assertEqual(len(generated.session_secret), 64)
        self.assertNotEqual(generated.session_secret, another.session_secret)
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
                header_only = await client.get(
                    "/api/users",
                    headers={"Authorization": "Bearer token-secret"},
                )
                wrong = await client.get("/api/auth/status?token=wrong")
                bootstrap = await client.get(
                    "/api/auth/status?token=token-secret"
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
                    "header_only": header_only,
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
        self.assertEqual(result["header_only"].status_code, 401)
        self.assertEqual(result["wrong"].status_code, 401)
        self.assertEqual(result["bootstrap"].status_code, 200)
        cookie = result["bootstrap"].headers["set-cookie"]
        self.assertIn("kemo_test_session=", cookie)
        self.assertIn("httponly", cookie.lower())
        self.assertIn("samesite=lax", cookie.lower())
        self.assertIn("max-age=7200", cookie.lower())
        self.assertEqual(result["allowed"].status_code, 200)
        self.assertTrue(result["settings"].json()["authentication"]["enabled"])
        for secret in ("token-secret", "password-secret", "session-secret"):
            self.assertNotIn(secret, result["settings"].text)
        self.assertTrue(result["refreshed"].json()["authenticated"])
        self.assertEqual(result["logout"].status_code, 200)
        self.assertEqual(result["denied_again"].status_code, 401)
        self.assertEqual(result["login"].status_code, 200)
        self.assertEqual(result["allowed_by_password"].status_code, 200)

    def test_user_config_api_is_redacted_and_read_only(self) -> None:
        _, root = self.make_root()
        user_config_path = root / "users" / "alice" / "user_config.json"
        user_config_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "kemo",
                "base_url": "http://127.0.0.1:8741",
                        "model": "old-model",
                        "api_key": "disk-secret",
                        "stream": False,
                    },
                }
            ),
            "utf-8",
        )
        original = user_config_path.read_bytes()
        app = create_app(
            service=WebRunService(root),
            auth_config=WebAuthConfig(access_token="view-token"),
        )

        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                denied = await client.get("/api/users/alice/config/full")
                await client.get("/api/auth/status?token=view-token")
                loaded = await client.get("/api/users/alice/config/full")
                blocked = await client.put(
                    "/api/users/alice/config",
                    json={"config": {"schema_version": 1}},
                )
                return denied, loaded, blocked

        denied, loaded, blocked = asyncio.run(invoke())
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["config"]["provider"]["api_key"], "***")
        self.assertNotIn("disk-secret", loaded.text)
        self.assertEqual(
            set(loaded.json()),
            {"user", "config", "redacted_paths"},
        )
        self.assertEqual(blocked.status_code, 405)
        self.assertEqual(user_config_path.read_bytes(), original)

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
                login = await first_client.get("/api/auth/status?token=token")
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

    def test_history_paginates_complete_rounds_from_newest_to_oldest(self) -> None:
        _, root = self.make_root()
        window = empty_window("alice", "web", "s1")
        for round_number in range(1, 46):
            window["text"]["messages"].extend(
                [
                    {"role": "user", "content": f"question {round_number}"},
                    {"role": "assistant", "content": f"answer {round_number}"},
                ]
            )
            window["think"]["rounds"].append(
                {"round": round_number, "content": f"reasoning {round_number}"}
            )
            window["data"]["round_metrics"].append(
                {
                    "round": round_number,
                    "usage": {"total_tokens": round_number},
                    "elapsed_ms": round_number,
                    "tool_calls": 0,
                    "guidance": [],
                }
            )
        window["data"]["rounds"] = 45
        commit_window(root / "users" / "alice" / "history" / "window-1", window)
        app = create_app(service=WebRunService(root))

        latest = self.request(
            app,
            "GET",
            "/api/users/alice/sessions/s1/history?limit=20",
        ).json()
        self.assertEqual(len(latest["messages"]), 40)
        self.assertEqual(latest["messages"][0]["content"], "question 26")
        self.assertEqual(latest["messages"][-1]["content"], "answer 45")
        self.assertEqual(
            latest["pagination"],
            {
                "limit": 20,
                "total_rounds": 45,
                "first_round": 26,
                "last_round": 45,
                "has_more_before": True,
                "next_before": 26,
            },
        )
        self.assertEqual(
            [item["round"] for item in latest["round_metrics"]],
            list(range(26, 46)),
        )
        self.assertEqual(
            [item["round"] for item in latest["round_traces"]],
            list(range(26, 46)),
        )

        earlier = self.request(
            app,
            "GET",
            "/api/users/alice/sessions/s1/history?limit=20&before=26",
        ).json()
        self.assertEqual(earlier["messages"][0]["content"], "question 6")
        self.assertEqual(earlier["messages"][-1]["content"], "answer 25")
        self.assertEqual(earlier["pagination"]["next_before"], 6)
        self.assertTrue(earlier["pagination"]["has_more_before"])

        oldest = self.request(
            app,
            "GET",
            "/api/users/alice/sessions/s1/history?limit=20&before=6",
        ).json()
        self.assertEqual(oldest["messages"][0]["content"], "question 1")
        self.assertEqual(oldest["messages"][-1]["content"], "answer 5")
        self.assertFalse(oldest["pagination"]["has_more_before"])
        self.assertIsNone(oldest["pagination"]["next_before"])

        full = self.request(app, "GET", "/api/users/alice/sessions/s1/history").json()
        self.assertEqual(len(full["messages"]), 90)

    def test_active_create_and_close_session_api_uses_durable_reservations(self) -> None:
        _, root = self.make_root()
        app = create_app(service=WebRunService(root))

        first = self.request(app, "GET", "/api/users/alice/sessions/active")
        self.assertEqual(first.status_code, 200)
        first_payload = first.json()
        first_id = first_payload["session"]["session_id"]
        self.assertTrue(first_payload["created"])
        self.assertTrue(first_id.startswith("conv_"))
        history_dir = root / "users" / "alice" / "history"
        self.assertFalse((history_dir / first_id).exists())
        renamed = self.request(
            app,
            "PATCH",
            f"/api/users/alice/sessions/{first_id}",
            json={"title": "零轮会话"},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["session"]["title"], "零轮会话")

        restored = self.request(app, "GET", "/api/users/alice/sessions/active")
        self.assertFalse(restored.json()["created"])
        self.assertEqual(restored.json()["session"]["session_id"], first_id)

        created = self.request(app, "POST", "/api/users/alice/sessions")
        second_id = created.json()["session"]["session_id"]
        self.assertNotEqual(second_id, first_id)
        closed = self.request(
            app,
            "POST",
            f"/api/users/alice/sessions/{second_id}/close",
        )
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["session"]["state"], "closed")
        self.assertEqual(closed.json()["memory"]["reason"], "no_archive")

        replacement = self.request(app, "GET", "/api/users/alice/sessions/active")
        replacement_id = replacement.json()["session"]["session_id"]
        self.assertTrue(replacement.json()["created"])
        self.assertNotIn(replacement_id, {first_id, second_id})
        deleted = self.request(
            app,
            "DELETE",
            f"/api/users/alice/sessions/{replacement_id}",
        )
        self.assertEqual(deleted.status_code, 200)

    def test_client_scoped_sessions_and_leases_isolate_browser_pages(self) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        app = create_app(service=service)
        client_a = "web_client_a"
        client_b = "web_client_b"

        active_a = self.request(
            app, "GET", f"/api/users/alice/sessions/active?client_id={client_a}"
        ).json()
        active_b = self.request(
            app, "GET", f"/api/users/alice/sessions/active?client_id={client_b}"
        ).json()
        self.assertEqual(active_a["active_key"], f"interactive:alice:{client_a}")
        self.assertEqual(active_b["active_key"], f"interactive:alice:{client_b}")
        self.assertNotEqual(active_a["active_key"], active_b["active_key"])

        service._active_runs["run_client_a"] = ActiveRun(
            "run_client_a", "alice", active_a["session"]["session_id"]
        )
        created = self.request(
            app,
            "POST",
            "/api/users/alice/sessions",
            json={"client_id": client_b},
        )
        self.assertEqual(created.status_code, 200, created.text)
        service._active_runs.pop("run_client_a", None)

        session_id = created.json()["session"]["session_id"]
        leased = self.request(
            app,
            "POST",
            f"/api/users/alice/sessions/{session_id}/lease",
            json={"client_id": client_a},
        )
        self.assertEqual(leased.status_code, 200, leased.text)
        self.assertEqual(leased.json()["active_clients"], 2)

        deferred = self.request(
            app,
            "POST",
            f"/api/users/alice/sessions/{session_id}/close?client_id={client_a}",
        )
        self.assertEqual(deferred.status_code, 200, deferred.text)
        self.assertFalse(deferred.json()["closed"])
        self.assertTrue(deferred.json()["deferred"])
        self.assertEqual(deferred.json()["active_clients"], 1)

        closed = self.request(
            app,
            "POST",
            f"/api/users/alice/sessions/{session_id}/close?client_id={client_b}",
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertTrue(closed.json()["closed"])
        self.assertFalse(closed.json()["deferred"])

    def test_session_delete_rejects_other_page_lease_and_allows_expired_lease(self) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        app = create_app(service=service)
        client_a = "web_client_a"
        client_b = "web_client_b"
        created = self.request(
            app,
            "POST",
            "/api/users/alice/sessions",
            json={"client_id": client_a},
        ).json()
        session_id = created["session"]["session_id"]
        self.request(
            app,
            "POST",
            f"/api/users/alice/sessions/{session_id}/lease",
            json={"client_id": client_b},
        )

        blocked = self.request(
            app,
            "DELETE",
            f"/api/users/alice/sessions/{session_id}?client_id={client_a}",
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)

        with service._active_runs_lock:
            service._session_leases[("alice", "web", session_id)][client_b] = 0.0
        deleted = self.request(
            app,
            "DELETE",
            f"/api/users/alice/sessions/{session_id}?client_id={client_a}",
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)

    def test_stream_chat_uses_client_scoped_history_key_and_touches_lease(self) -> None:
        _, root = self.make_root()
        requests: list[dict[str, Any]] = []

        def source(request, **_kwargs):
            requests.append(request)
            yield RunEvent(type="done")

        service = WebRunService(root, event_source=source)
        events = list(
            service.stream_chat(
                "alice",
                "client-session",
                "hello",
                cancel_event=threading.Event(),
                client_id="web_client_a",
            )
        )
        self.assertEqual([event.type for event in events], ["done"])
        self.assertEqual(
            requests[0]["_history_active_key"],
            "interactive:alice:web_client_a",
        )
        self.assertIn(
            "web_client_a",
            service._session_leases[("alice", "web", "client-session")],
        )

    def test_delete_all_sessions_includes_uncommitted_reservations(self) -> None:
        _, root = self.make_root()
        app = create_app(service=WebRunService(root))
        self.request(app, "GET", "/api/users/alice/sessions/active")
        self.request(app, "POST", "/api/users/alice/sessions")

        deleted = self.request(app, "DELETE", "/api/users/alice/sessions")

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted_sessions"], 2)
        sessions = self.request(app, "GET", "/api/users/alice/sessions")
        self.assertEqual(sessions.json()["sessions"], [])

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

    def test_session_manual_compression_uses_runtime_compressor(self) -> None:
        _, root = self.make_root()
        commit_window(
            root / "users" / "alice" / "history" / "window-1",
            empty_window("alice", "web", "s1"),
        )
        observed: dict[str, Any] = {}

        def compressor(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
            observed.update({"request": request, "root": root})
            return {
                "context": {"rounds_removed": 3},
                "summary_cache": "context_summary.json",
            }

        app = create_app(
            service=WebRunService(root, context_compressor=compressor)
        )
        response = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/s1/compress",
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["compressed"])
        self.assertEqual(response.json()["rounds_removed"], 3)
        self.assertEqual(response.json()["memory"]["status"], "skipped")
        self.assertEqual(response.json()["memory"]["reason"], "already_processed")
        self.assertFalse(response.json()["memory"]["retry_pending"])
        self.assertEqual(
            observed["request"],
            {
                "user": "alice",
                "source": "web",
                "session_id": "s1",
                "memory_extraction_policy": "queue",
            },
        )
        self.assertEqual(observed["root"], root.resolve())

    def test_session_manual_compression_extracts_pending_memory(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text("{}", "utf-8")
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "remember this"},
            {"role": "assistant", "content": "saved answer"},
        ]
        window["think"]["rounds"] = [{"round": 1, "content": "reasoning"}]
        window["tool"]["rounds"] = [{"round": 1, "calls": []}]
        window["data"]["rounds"] = 1
        window["data"]["memory_processed_round"] = 0
        archive = root / "users" / "alice" / "history" / "window-1"
        commit_window(archive, window)

        def compressor(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
            return {
                "context": {"rounds_removed": 1},
                "summary_cache": "context_summary.json",
            }

        with (
            patch("web.service.AgentRunner", return_value=object()),
            patch(
                "run.engine._extract_round_memory",
                return_value={
                    "status": "completed",
                    "candidate_count": 1,
                    "error": None,
                },
            ) as extracted,
        ):
            response = self.request(
                create_app(
                    service=WebRunService(root, context_compressor=compressor)
                ),
                "POST",
                "/api/users/alice/sessions/s1/compress",
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["compressed"])
        self.assertEqual(payload["memory"]["status"], "queued")
        self.assertEqual(payload["memory"]["pending_rounds"], 1)
        self.assertEqual(payload["memory"]["target_round"], 1)
        self.assertFalse(payload["memory"]["retry_pending"])
        extracted.assert_not_called()
        stored = load_window(archive)
        self.assertEqual(stored["data"]["memory_processed_round"], 0)
        self.assertEqual(stored["data"]["memory_status"], "queued")
        self.assertEqual(stored["data"]["memory_target_round"], 1)

    def test_session_manual_compression_fails_when_memory_queue_registration_fails(
        self,
    ) -> None:
        _, root = self.make_root()
        commit_window(
            root / "users" / "alice" / "history" / "window-1",
            empty_window("alice", "web", "s1"),
        )

        def compressor(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
            return {
                "context": {"rounds_removed": 2},
                "summary_cache": "context_summary.json",
            }

        service = WebRunService(root, context_compressor=compressor)
        with patch(
            "web.service.queue_memory_extraction",
            side_effect=RuntimeError("memory queue unavailable"),
        ):
            response = self.request(
                create_app(service=service),
                "POST",
                "/api/users/alice/sessions/s1/compress",
            )

        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(
            response.json()["error"]["message"],
            "上下文压缩成功，但后台记忆任务登记失败",
        )

    def test_session_manual_compression_reports_summary_failure_without_queueing(
        self,
    ) -> None:
        _, root = self.make_root()
        commit_window(
            root / "users" / "alice" / "history" / "window-1",
            empty_window("alice", "web", "s1"),
        )

        def compressor(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
            return {
                "context": {
                    "rounds_removed": 2,
                    "summary": {
                        "failed": True,
                        "error": "摘要响应格式无效",
                    },
                },
                "memory": {
                    "status": "failed",
                    "reason": "context_summary_failed",
                },
            }

        with patch("web.service.queue_memory_extraction") as queued:
            response = self.request(
                create_app(
                    service=WebRunService(root, context_compressor=compressor)
                ),
                "POST",
                "/api/users/alice/sessions/s1/compress",
            )

        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(
            response.json()["error"]["message"],
            "手动上下文压缩失败：摘要响应格式无效",
        )
        queued.assert_not_called()

    def test_session_memory_extraction_uses_latest_complete_round(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text("{}", "utf-8")
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer one"},
            {"role": "user", "content": "remember latest"},
            {"role": "assistant", "content": "answer two"},
        ]
        window["think"]["rounds"] = [
            {"round": 1, "content": "think one"},
            {"round": 2, "content": "think two"},
        ]
        window["tool"]["rounds"] = [
            {"round": 1, "calls": []},
            {"round": 2, "calls": [{"name": "lookup", "status": "completed"}]},
        ]
        window["data"]["rounds"] = 2
        commit_window(root / "users" / "alice" / "history" / "window-1", window)
        observed: dict[str, Any] = {}

        def extract(**kwargs):
            observed.update(kwargs)
            return {
                "status": "completed",
                "candidate_count": 2,
                "candidates": [],
                "source": {"source": "round_commit"},
                "error": None,
            }

        def persist(**kwargs):
            return {
                "status": "completed",
                "candidate_count": kwargs["analysis"]["candidate_count"],
                "error": None,
            }

        with (
            patch("web.service.AgentRunner", return_value=object()),
            patch("run.engine._analyze_memory_batch", side_effect=extract) as extracted,
            patch("run.engine._persist_round_memory_analysis", side_effect=persist),
        ):
            app = create_app(service=WebRunService(root))
            response = self.request(
                app,
                "POST",
                "/api/users/alice/sessions/s1/extract-memory",
            )
            repeated = self.request(
                app,
                "POST",
                "/api/users/alice/sessions/s1/extract-memory",
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["candidates"], 2)
        self.assertEqual(len(observed["rounds"]), 1)
        extracted_round = observed["rounds"][0]
        self.assertEqual(extracted_round["round"], 2)
        self.assertEqual(extracted_round["messages"][0]["content"], "remember latest")
        self.assertEqual(extracted_round["messages"][1]["content"], "answer two")
        self.assertEqual(extracted_round["think"]["content"], "think two")
        self.assertEqual(extracted_round["tools"][0]["name"], "lookup")
        self.assertEqual(extracted.call_count, 1)
        self.assertEqual(repeated.json()["reason"], "already_processed")
        stored = load_window(root / "users" / "alice" / "history" / "window-1")
        self.assertEqual(stored["data"]["memory_processed_round"], 2)

    def test_extract_memory_route_forwards_to_service(self) -> None:
        fake = FakeService()
        response = self.request(
            create_app(service=fake),
            "POST",
            "/api/users/alice/sessions/s1/extract-memory",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["candidates"], 1)
        self.assertEqual(
            fake.seen,
            {"user": "alice", "session_id": "s1", "source": "web"},
        )

    def test_session_undo_last_round_updates_archive_and_runtime(self) -> None:
        _, root = self.make_root()
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "retry me"},
            {"role": "assistant", "content": "partial answer"},
        ]
        window["think"]["rounds"] = [
            {"round": 1, "content": "think one"},
            {"round": 2, "content": "think two"},
        ]
        window["tool"]["rounds"] = [
            {"round": 1, "calls": []},
            {"round": 2, "calls": [{"id": "call-2", "name": "demo"}]},
        ]
        window["data"]["rounds"] = 2
        window["data"]["memory_processed_round"] = 2
        window["data"]["memory_status"] = "completed"
        window["data"]["round_metrics"] = [
            {
                "round": 1,
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "estimated": False,
                },
            },
            {
                "round": 2,
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 4,
                    "total_tokens": 24,
                    "estimated": False,
                },
            },
        ]
        window["data"]["token_usage"] = {
            "prompt_tokens": 30,
            "completion_tokens": 6,
            "total_tokens": 36,
            "estimated": False,
        }
        window["items"] = synthesize_items(window)
        archive_path = root / "users" / "alice" / "history" / "window-1"
        commit_window(archive_path, window)
        runtime_path = runtime_window_path(archive_path)
        runtime_window = copy.deepcopy(window)
        runtime_window["data"]["context"] = {
            "round_offset": 0,
            "workspace_rounds": 2,
        }
        commit_window(runtime_path, runtime_window)
        app = create_app(service=WebRunService(root))

        response = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/s1/undo-last-round",
            json={"expected_round": 2, "prompt": "retry me"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["rolled_back"])
        self.assertEqual(response.json()["remaining_rounds"], 1)
        self.assertEqual(response.json()["content"], [{"type": "text", "text": "retry me"}])
        for path in (archive_path, runtime_path):
            rolled_back = load_window(path)
            self.assertEqual(rolled_back["data"]["rounds"], 1)
            self.assertEqual(
                rolled_back["text"]["messages"],
                [
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "first answer"},
                ],
            )
            self.assertEqual([item["round"] for item in rolled_back["think"]["rounds"]], [1])
            self.assertEqual([item["round"] for item in rolled_back["tool"]["rounds"]], [1])
            self.assertTrue(all(
                (item.get("metadata") or {}).get("round") == 1
                for item in rolled_back["items"]["items"]
            ))
            self.assertEqual(rolled_back["data"]["token_usage"]["total_tokens"], 12)
            self.assertEqual(rolled_back["data"]["memory_processed_round"], 1)
            self.assertEqual(rolled_back["data"]["memory_status"], "completed")
            self.assertNotIn("context", rolled_back["data"])

        interrupted = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/s1/undo-last-round",
            json={"expected_round": 2, "prompt": "retry me"},
        )
        self.assertEqual(interrupted.status_code, 200, interrupted.text)
        self.assertFalse(interrupted.json()["rolled_back"])
        self.assertEqual(load_window(archive_path)["data"]["rounds"], 1)

    def test_session_undo_last_round_rejects_stale_or_active_requests(self) -> None:
        _, root = self.make_root()
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ]
        window["think"]["rounds"] = [{"round": 1, "content": "think"}]
        window["tool"]["rounds"] = [{"round": 1, "calls": []}]
        window["data"]["rounds"] = 1
        window["items"] = synthesize_items(window)
        archive_path = root / "users" / "alice" / "history" / "window-1"
        commit_window(archive_path, window)
        service = WebRunService(root)
        app = create_app(service=service)

        stale = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/s1/undo-last-round",
            json={"expected_round": 1, "prompt": "different"},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(load_window(archive_path)["data"]["rounds"], 1)

        service._active_runs["run_busy_undo"] = ActiveRun("run_busy_undo", "alice", "s1")
        active = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/s1/undo-last-round",
            json={"expected_round": 1, "prompt": "question"},
        )
        self.assertEqual(active.status_code, 409)
        self.assertEqual(load_window(archive_path)["data"]["rounds"], 1)

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
        self.assertEqual(queued["status"], "accepted_current_run")
        self.assertEqual(seen, ["adjust target"])
        self.assertEqual(captured[-1].metadata["run_id"], "run_guidance_123")
        with self.assertRaisesRegex(Exception, "运行不存在"):
            service.submit_guidance("alice", "run_guidance_123", "too late")

    def test_web_guidance_after_final_boundary_is_queued_for_next_turn(self) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        active = ActiveRun("run_guidance_closed", "alice", "guided-session")
        active.guidance.close()
        service._active_runs[active.run_id] = active

        response = service.submit_guidance(
            "alice", active.run_id, "continue as a new turn"
        )

        self.assertEqual(response["status"], "queued_next_turn")
        self.assertEqual(response["queued"], 0)
        self.assertEqual(active.guidance.qsize(), 0)

    def test_web_cancel_run_is_user_scoped_and_sets_active_event(self) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        active = ActiveRun("run_cancel_123", "alice", "cancel-session")
        service._active_runs[active.run_id] = active

        response = self.request(
            create_app(service=service),
            "POST",
            "/api/runs/run_cancel_123/cancel",
            json={"user": "alice"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "stopping")
        self.assertTrue(active.cancel_event.is_set())

        active.cancel_event.clear()
        denied = self.request(
            create_app(service=service),
            "POST",
            "/api/runs/run_cancel_123/cancel",
            json={"user": "bob"},
        )
        self.assertEqual(denied.status_code, 404, denied.text)
        self.assertFalse(active.cancel_event.is_set())

    def test_explicit_cancel_keeps_terminal_done_visible_to_stream_consumer(self) -> None:
        _, root = self.make_root()

        def source(request, *, cancel_event, **_kwargs):
            yield RunEvent(type="text_delta", content="partial")
            self.assertTrue(cancel_event.wait(timeout=2))
            yield RunEvent(
                type="done",
                metadata={
                    "run_id": request["run_id"],
                    "committed": True,
                    "status": "cancelled",
                    "cancelled": True,
                },
            )

        service = WebRunService(root, event_source=source)
        iterator = service.stream_chat(
            "alice",
            "cancel-stream",
            "start",
            cancel_event=threading.Event(),
            run_id="run_cancel_stream_123",
        )
        self.assertEqual(next(iterator).type, "text_delta")
        service.cancel_run("alice", "run_cancel_stream_123")
        terminal = list(iterator)
        self.assertEqual([event.type for event in terminal], ["done"])
        self.assertEqual(terminal[0].metadata["status"], "cancelled")

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

    def test_chat_route_accepts_uploaded_files_without_text(self) -> None:
        fake = FakeService(events=[RunEvent(type="done")])
        response = self.request(
            create_app(service=fake),
            "POST",
            "/api/chat",
            json={
                "user": "alice",
                "session_id": "attachment-only",
                "prompt": "",
                "uploaded_files": ["screenshot.png"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(fake.seen["prompt"], "")
        self.assertEqual(fake.seen["uploaded_files"], ["screenshot.png"])

    def test_plan_chat_route_starts_plan_stream_without_a_prompt(self) -> None:
        fake = FakeService(events=[RunEvent(type="text_delta", content="执行中"), RunEvent(type="done")])
        response = self.request(
            create_app(service=fake),
            "POST",
            "/api/chat",
            json={
                "user": "alice",
                "session_id": "s1",
                "prompt": "",
                "plan_id": "plan_12345678",
                "run_id": "run_plan_123",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item[0] for item in self.parse_sse(response.text)], ["text_delta", "done"])
        self.assertEqual(
            fake.seen,
            {
                "user": "alice",
                "session_id": "s1",
                "plan_id": "plan_12345678",
                "run_id": "run_plan_123",
            },
        )

    def test_stream_plan_uses_one_agent_run_for_multiple_steps(self) -> None:
        _, root = self.make_root()
        store = PlanStore(root, "alice")
        plan = store.create(
            normalize_plan(
                title="连续执行",
                description="单轮完成两步",
                user="alice",
                source="web",
                session_id="s1",
                steps=[
                    {
                        "step_id": "step_1",
                        "title": "第一步",
                        "description": "执行第一步",
                        "critical": True,
                    },
                    {
                        "step_id": "step_2",
                        "title": "第二步",
                        "description": "执行第二步",
                        "depends_on": ["step_1"],
                        "critical": True,
                    },
                ],
            )
        )
        requests: list[dict[str, Any]] = []

        def source(request, **_kwargs):
            requests.append(request)
            context = {
                "root": str(root),
                "user": "alice",
                "source": "web",
                "task_plan_id": plan["plan_id"],
                "task_plan_mode": request["_task_plan_mode"],
            }
            from plugins.task_plan.tool import run as run_task_plan_tool

            for index in (1, 2):
                result = run_task_plan_tool(
                    action="step_done",
                    plan_id=plan["plan_id"],
                    step_id=f"step_{index}",
                    result=f"步骤 {index} 完成",
                    context=context,
                )
                yield RunEvent(
                    type="tool_call_result",
                    tool_call_id=f"call_{index}",
                    tool_name="task_plan",
                    result=result,
                )
            yield RunEvent(type="text_delta", content="全部完成")
            yield RunEvent(type="done")

        service = WebRunService(root, event_source=source)
        events = list(
            service.stream_plan(
                "alice",
                "s1",
                plan["plan_id"],
                cancel_event=threading.Event(),
                run_id="run_plan_single",
            )
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["_task_plan_id"], plan["plan_id"])
        self.assertEqual(requests[0]["_task_plan_mode"], "agent_managed")
        self.assertIn("【任务计划连续执行】", requests[0]["prompt"])
        self.assertEqual([event.type for event in events], ["tool_call_result", "tool_call_result", "text_delta", "done"])
        stored = store.read(plan["plan_id"])
        self.assertEqual(stored["status"], "completed")
        self.assertTrue(all(step["status"] == "completed" for step in stored["steps"]))

    def test_plan_pause_command_uses_latest_disk_state_without_revision(self) -> None:
        _, root = self.make_root()
        store = PlanStore(root, "alice")
        plan = store.create(
            normalize_plan(
                title="可暂停计划",
                description="验证无 revision 指令",
                user="alice",
                status="running",
                steps=[
                    {
                        "step_id": "step_1",
                        "title": "执行",
                        "description": "执行中",
                        "critical": True,
                    }
                ],
            )
        )
        store.update(plan["plan_id"], lambda current: {**current, "current_step": "step_1"})
        app = create_app(service=WebRunService(root))

        response = self.request(
            app,
            "POST",
            f"/api/users/alice/tasks/plans/{plan['plan_id']}/actions/pause",
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["plan"]["status"], "paused")
        self.assertEqual(store.read(plan["plan_id"])["status"], "paused")

    def test_editable_web_resource_apis_are_scoped_and_validated(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"schema_version": 1, "tools": {"enabled": True}}), "utf-8"
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "chat",
                        "base_url": "https://example.test/v1",
                        "model": "test",
                        "api_key": "keep-secret",
                    },
                }
            ),
            "utf-8",
        )
        app = create_app(service=WebRunService(root))

        plan = self.request(
            app,
            "POST",
            "/api/users/alice/tasks/plans",
            json={
                "title": "Web plan",
                "description": "created by web",
                "steps": [
                    {
                        "step_id": "step_1",
                        "title": "First",
                        "description": "Do first",
                        "critical": True,
                    }
                ],
            },
        )
        self.assertEqual(plan.status_code, 200, plan.text)
        plan_id = plan.json()["plan"]["plan_id"]
        paused = self.request(
            app,
            "PUT",
            f"/api/users/alice/tasks/plans/{plan_id}",
            json={"revision": 1, "status": "paused"},
        )
        self.assertEqual(paused.json()["plan"]["status"], "paused")

        cron = self.request(
            app,
            "POST",
            "/api/users/alice/tasks/crons",
            json={
                "title": "Hourly",
                "prompt": "run hourly",
                "type": "recurring",
                "interval_seconds": 3600,
            },
        )
        self.assertEqual(cron.status_code, 200, cron.text)
        too_fast = self.request(
            app,
            "POST",
            "/api/users/alice/tasks/crons",
            json={
                "title": "Too fast",
                "prompt": "x",
                "type": "recurring",
                "interval_seconds": 10,
            },
        )
        self.assertEqual(too_fast.status_code, 400)

        put_knowledge = self.request(
            app,
            "PUT",
            "/api/users/alice/knowledge/user/document?path=notes%2Fweb.md",
            json={"content": "# Web knowledge"},
        )
        self.assertEqual(put_knowledge.status_code, 200, put_knowledge.text)
        knowledge = self.request(
            app,
            "GET",
            "/api/users/alice/knowledge/user/document?path=notes%2Fweb.md",
        )
        self.assertEqual(knowledge.json()["content"], "# Web knowledge")
        escaped_knowledge = self.request(
            app,
            "PUT",
            "/api/users/alice/knowledge/user/document?path=..%2Fescape.md",
            json={"content": "bad"},
        )
        self.assertEqual(escaped_knowledge.status_code, 400)

        memory = self.request(
            app,
            "PUT",
            "/api/users/alice/memory/item?filename=web-memory.md",
            json={"content": "remember this", "tier": "one_month"},
        )
        self.assertEqual(memory.status_code, 200, memory.text)
        self.assertEqual(memory.json()["tier"], "one_month")
        self.assertEqual(memory.json()["memory_ref"], "one_month:web-memory.md")
        fetched_memory = self.request(
            app,
            "GET",
            "/api/users/alice/memory/item?tier=one_month&filename=web-memory.md",
        )
        self.assertEqual(fetched_memory.status_code, 200, fetched_memory.text)
        self.assertEqual(fetched_memory.json()["content"], "remember this")
        self.assertEqual(
            fetched_memory.json()["memory_ref"], "one_month:web-memory.md"
        )
        deleted_memory = self.request(
            app,
            "DELETE",
            "/api/users/alice/memory/item?tier=one_month&filename=web-memory.md",
        )
        self.assertEqual(deleted_memory.status_code, 200, deleted_memory.text)
        self.assertTrue(deleted_memory.json()["deleted"])
        self.assertEqual(deleted_memory.json()["tier"], "one_month")
        important = self.request(
            app,
            "PUT",
            "/api/users/alice/memory/important",
            json={"content": "important context"},
        )
        self.assertEqual(important.status_code, 200, important.text)
        deleted_important = self.request(
            app,
            "DELETE",
            "/api/users/alice/memory/important",
        )
        self.assertEqual(deleted_important.status_code, 405, deleted_important.text)
        cleared_important = self.request(
            app,
            "PUT",
            "/api/users/alice/memory/important",
            json={"content": "   "},
        )
        self.assertEqual(cleared_important.status_code, 400, cleared_important.text)
        preserved_important = self.request(
            app,
            "GET",
            "/api/users/alice/memory/important",
        )
        self.assertEqual(preserved_important.status_code, 200, preserved_important.text)
        self.assertEqual(preserved_important.json()["content"], "important context")

        upload = self.request(
            app,
            "POST",
            "/api/users/alice/files/file_upload/upload?path=folder%2Fnote.txt",
            files={"file": ("note.txt", b"hello", "text/plain")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        moved = self.request(
            app,
            "PATCH",
            "/api/users/alice/files/file_upload/move?path=folder%2Fnote.txt&new_path=renamed.txt",
        )
        self.assertTrue(moved.json()["moved"])
        escaped_upload = self.request(
            app,
            "POST",
            "/api/users/alice/files/file_upload/upload?path=..%2Fescape.txt",
            files={"file": ("escape.txt", b"bad", "text/plain")},
        )
        self.assertEqual(escaped_upload.status_code, 400)

        patched = self.request(
            app,
            "PATCH",
            "/api/users/alice/config",
            json={
                "changes": {
                    "tools": {"enabled": False},
                    "agent_models": {
                        "default": "",
                        "cheap": "summary-test-model",
                        "reasoning": "",
                    },
                }
            },
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        stored_config = json.loads(
            (root / "users" / "alice" / "user_config.json").read_text("utf-8")
        )
        self.assertEqual(stored_config["provider"]["api_key"], "keep-secret")
        self.assertFalse(stored_config["tools"]["enabled"])
        self.assertEqual(stored_config["agent_models"]["cheap"], "summary-test-model")
        rejected_placeholder = self.request(
            app,
            "PATCH",
            "/api/users/alice/config",
            json={"changes": {"provider": {"api_key": "***"}}},
        )
        self.assertEqual(rejected_placeholder.status_code, 400)
        preferences = self.request(
            app,
            "PATCH",
            "/api/users/alice/preferences",
            json={"theme": "dark", "font_size": "large"},
        )
        self.assertEqual(preferences.json()["appearance"]["theme"], "dark")

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
                    "tools": {"enabled": True, "max_iterations": 4, "timeout": 10},
                    "kemo_graph": {
                        "kemo_graph_user_knowledge": True,
                        "kemo_graph_temporary_memory": True,
                    },
                    "memory": {"history_read_enabled": True},
                    "task_plan": {"auto_accept": False, "max_steps": 8},
                    "cron": {"enabled": True},
                    "agents": {"max_rounds": 30, "token_limit": 100000},
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "chat",
                        "base_url": "https://example.test/v1",
                        "model": "test-model",
                        "api_key": "super-secret",
                    },
                    "knowledge": {
                        "use_shared": False,
                        "use_global": True,
                    },
                    "skills": {
                        "shared_whitelist": ["observer"],
                    },
                    "expand": {
                        "global_whitelist": [],
                        "shared_whitelist": [],
                    },
                    "perception": {"global_whitelist": ["runtime"]},
                    "plugins": {"whitelist": []},
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
            (module / "sense.md").write_text(module_name, "utf-8")
            (module / "data_update.py").write_text(
                "from pathlib import Path\n"
                f"Path('sense.md').write_text('{module_name} refreshed', encoding='utf-8')\n",
                "utf-8",
            )
            (module / "sense.json").write_text(
                json.dumps(
                    {
                        "name": f"{module_name} display",
                        "data_md": "sense.md",
                        "recent_update": "2026-07-19 12:00:00",
                        "health": "正常" if module_name == "runtime" else "异常",
                        "start_update": "data_update.py",
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )
        broken_module = root / "global_sense" / "broken"
        broken_module.mkdir()
        (broken_module / "legacy.md").write_text("must not be injected", "utf-8")
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
        expand_roots = {
            "global": root / "global_expand",
            "shared": root / "shared_expand",
            "user": root / "users" / "alice" / "expand",
        }
        for scope, expand_root in expand_roots.items():
            expand_root.mkdir(parents=True)
            if scope != "user":
                (expand_root / "register.py").write_text(
                    "from pathlib import Path\n\n"
                    "def register(registry):\n"
                    f"    registry.add_expand_root('{scope}', Path(__file__).resolve().parent)\n",
                    "utf-8",
                )
            module_name = {"global": "lights", "shared": "bridge", "user": "personal"}[scope]
            module = expand_root / module_name
            module.mkdir()
            (module / "input_data.md").write_text(f"# {scope} data\nready", "utf-8")
            (module / "expand_control.md").write_text(
                "## 注入层\n\n可执行安全操作。\n\n"
                "## 操作层\n\n### 触发场景\n用户明确请求时。\n\n"
                "### 使用操作\n运行 start_expand.py。",
                "utf-8",
            )
            (module / "data_update.py").write_text(
                "from pathlib import Path\n"
                f"Path('input_data.md').write_text('# {scope} data\\nrefreshed', encoding='utf-8')\n",
                "utf-8",
            )
            (module / "start_expand.py").write_text("print('ok')\n", "utf-8")
            (module / "expand.json").write_text(
                json.dumps(
                    {
                        "name": f"{scope} display",
                        "explain": f"{scope} extension",
                        "open_input": True,
                        "input_data": "input_data.md",
                        "input_health": "正常",
                        "start_update": "data_update.py",
                        "open_control": True,
                        "start_expand": "start_expand.py",
                        "start_control": "expand_control.md",
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
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
                type="daily",
                time="09:00",
                next_run_at="2026-07-20T09:00:00+08:00",
            )
        )
        window = empty_window("alice", "web", "observer-session")
        window["text"]["messages"] = [
            {"role": "user", "content": "observer prompt"},
            {"role": "assistant", "content": "observer response"},
        ]
        window["data"]["rounds"] = 1
        window["data"]["token_usage"] = {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
            "estimated": False,
        }
        commit_window(root / "users" / "alice" / "history" / "observer-window", window)
        other_window = empty_window("alice", "web", "other-session")
        other_window["text"]["messages"] = [
            {"role": "user", "content": "old prompt"},
            {"role": "assistant", "content": "old response"},
        ]
        other_window["data"]["rounds"] = 1
        other_window["data"]["token_usage"] = {}
        other_window["data"]["updated_at"] = "2020-01-01T00:00:00+00:00"
        other_window["data"]["round_metrics"] = [
            {
                "round": 1,
                "usage": {},
                "tool_calls": 7,
                "provider_responses": [
                    {
                        "created_at": "2020-01-01T00:00:00+00:00",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 1,
                            "total_tokens": 11,
                        },
                    }
                ],
            }
        ]
        commit_window(
            root / "users" / "alice" / "history" / "other-window",
            other_window,
        )
        runtime_cache = runtime_window_path(
            root / "users" / "alice" / "history" / "observer-window"
        ) / "context_summary.json"
        runtime_cache.parent.mkdir(parents=True, exist_ok=True)
        runtime_cache.write_text(
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
        (memory_dir / "safe-memory.md").write_text("safe memory preview", "utf-8")
        (memory_dir / "data.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "files": {
                        "safe-memory.md": {
                            "weight": 2,
                            "updated_at": "2026-07-18T00:00:00+00:00",
                            "last_weight_date": None,
                            "expires_at": "2099-07-25T00:00:00+00:00",
                        }
                    }
                }
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
        context_window = overview.json()["context_window"]
        self.assertEqual(
            context_window["tokens"]["total_tokens"],
            context_window["tokens"]["system_prompt_tokens"]
            + context_window["tokens"]["context_tokens"],
        )
        self.assertEqual(
            context_window["tokens"]["capacity_tokens"],
            overview.json()["context"]["limit"],
        )
        self.assertEqual(context_window["conversation"]["foreground_rounds"], 1)
        self.assertEqual(context_window["conversation"]["archived_rounds"], 0)
        self.assertEqual(context_window["conversation"]["total_tool_calls"], 0)
        self.assertEqual(context_window["knowledge"]["enabled"], 1)
        self.assertIsInstance(context_window["knowledge"]["graph_enabled"], bool)
        self.assertIn("connected", context_window["messages"])
        self.assertIn("expands", context_window["integrations"])
        self.assertIn("senses", context_window["integrations"])

        runtime_status = self.request(
            app,
            "GET",
            "/api/users/alice/runtime/status?session_id=observer-session",
        )
        self.assertEqual(runtime_status.status_code, 200, runtime_status.text)
        runtime_payload = runtime_status.json()
        self.assertEqual(runtime_payload["user"], "alice")
        self.assertEqual(runtime_payload["context"]["rounds"], 1)
        self.assertEqual(runtime_payload["tokens"]["total_tokens"], 1500)
        self.assertEqual(runtime_payload["tokens"]["request_count"], 1)
        self.assertTrue(runtime_payload["prompt"]["content"])
        self.assertEqual(
            [item["id"] for item in runtime_payload["prompt"]["components"]],
            list(PROMPT_SECTION_ORDER),
        )
        runtime_sense = next(
            item for item in runtime_payload["components"]["sense"] if item["id"] == "runtime"
        )
        self.assertEqual(runtime_sense["name"], "runtime display")
        self.assertEqual(len(runtime_payload["components"]["expand"]), 3)
        self.assertEqual(runtime_payload["runtime_host"]["state"], "running")
        self.assertEqual(
            set(runtime_payload["congestion"]),
            {"provider", "web", "message_router"},
        )
        self.assertIn("active_requests", runtime_payload["congestion"]["provider"])
        self.assertIn("active_chats", runtime_payload["congestion"]["web"])
        self.assertIn("queued_messages", runtime_payload["congestion"]["message_router"])
        self.assertNotIn("api_key", runtime_status.text)

        tasks = self.request(app, "GET", "/api/users/alice/tasks")
        self.assertEqual(len(tasks.json()["plans"]), 1)
        self.assertEqual(len(tasks.json()["cron_tasks"]), 1)
        self.assertTrue(tasks.json()["cron_tasks"][0]["user_defined"])
        self.assertFalse(WebRunService._cron_summary({"exec_mode": "system"})["user_defined"])
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
            [False, False, True],
        )
        self.assertEqual(
            knowledge.json()["source_policy"]["knowledge"]["effective_scopes"],
            ["global"],
        )
        self.assertEqual(knowledge.json()["extensions"]["kemo_graph"], "not_connected")
        self.assertNotIn("private index", knowledge.text)

        skills = self.request(app, "GET", "/api/users/alice/skills")
        self.assertEqual(skills.json()["tools"][0]["name"], "clock")
        self.assertEqual(skills.json()["prompt_summary"]["registered"], 2)
        self.assertEqual(skills.json()["prompt_summary"]["active"], 1)
        self.assertNotIn("project", skills.text)

        expands = self.request(app, "GET", "/api/users/alice/expand")
        self.assertEqual(expands.status_code, 200)
        self.assertEqual(expands.json()["summary"]["total"], 3)
        self.assertEqual(expands.json()["status_summary"]["enabled"], 3)
        self.assertEqual(expands.json()["status_summary"]["healthy"], 3)
        self.assertEqual(expands.json()["injection"]["injected_items"], 3)
        self.assertGreater(expands.json()["injection"]["estimated_tokens"], 0)
        expand_items = {
            item["id"]: item
            for group in expands.json()["expands"]
            for item in group["items"]
        }
        lights = expand_items["global:lights"]
        self.assertEqual(lights["display_name"], "global display")
        self.assertEqual(lights["collected_markdown"], "# global data\nready")
        self.assertEqual(lights["control_injection_markdown"], "可执行安全操作。")
        self.assertIn("用户明确请求时", lights["control_operation_markdown"])
        self.assertIn("[global:lights]", lights["injected_markdown"])
        self.assertTrue(lights["whitelisted"])
        self.assertTrue(lights["open_control"])

        refreshed_expand = self.request(
            app, "POST", "/api/users/alice/expand/global/lights/refresh"
        )
        self.assertEqual(refreshed_expand.status_code, 200)
        self.assertTrue(refreshed_expand.json()["updated"])
        self.assertIn("refreshed", refreshed_expand.json()["item"]["collected_markdown"])

        disabled_expand = self.request(
            app,
            "PATCH",
            "/api/users/alice/expand/global/lights/enabled",
            json={"enabled": False},
        )
        self.assertEqual(disabled_expand.status_code, 200)
        self.assertEqual(disabled_expand.json()["whitelist"], ["__kemo_none__"])
        self.assertFalse(
            next(
                item
                for group in self.request(app, "GET", "/api/users/alice/expand").json()["expands"]
                if group["scope"] == "global"
                for item in group["items"]
                if item["name"] == "lights"
            )["whitelisted"]
        )
        enabled_expand = self.request(
            app,
            "PATCH",
            "/api/users/alice/expand/global/lights/enabled",
            json={"enabled": True},
        )
        self.assertEqual(enabled_expand.status_code, 200)
        self.assertEqual(enabled_expand.json()["whitelist"], [])
        self.assertEqual(
            self.request(
                app,
                "PATCH",
                "/api/users/alice/expand/user/personal/enabled",
                json={"enabled": False},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.request(app, "DELETE", "/api/users/alice/expand/global/lights").status_code,
            400,
        )
        deleted_expand = self.request(
            app, "DELETE", "/api/users/alice/expand/user/personal"
        )
        self.assertEqual(deleted_expand.status_code, 200)
        self.assertTrue(deleted_expand.json()["deleted"])
        self.assertFalse((root / "users" / "alice" / "expand" / "personal").exists())

        sense = self.request(app, "GET", "/api/users/alice/sense")
        self.assertTrue(sense.json()["core_available"])
        self.assertEqual(
            [item["layer"] for item in sense.json()["sources"]],
            ["global", "global", "global"],
        )
        self.assertEqual(sense.json()["summary"]["global"], 3)
        self.assertEqual(sense.json()["summary"]["enabled"], 1)
        self.assertEqual(sense.json()["summary"]["healthy"], 1)
        self.assertEqual(sense.json()["summary"]["unhealthy"], 2)
        self.assertEqual(sense.json()["summary"]["invalid"], 1)
        self.assertEqual(sense.json()["core_files"], 2)
        self.assertEqual(sense.json()["summary"]["registered_data"], 2)
        self.assertEqual(sense.json()["summary"]["injected_data"], 1)
        self.assertTrue(sense.json()["injection"]["enabled"])
        self.assertEqual(sense.json()["injection"]["injected_items"], 1)
        self.assertGreater(sense.json()["injection"]["estimated_tokens"], 0)
        self.assertEqual(
            sense.json()["injection"]["source_files"],
            ["global_sense/runtime/sense.md"],
        )
        self.assertEqual(
            {item["id"]: item["status"] for item in sense.json()["sources"]},
            {"broken": "invalid", "network": "filtered", "runtime": "active"},
        )
        runtime_source = next(item for item in sense.json()["sources"] if item["id"] == "runtime")
        self.assertEqual(runtime_source["display_name"], "runtime display")
        self.assertEqual(runtime_source["data_md"], "sense.md")
        self.assertEqual(runtime_source["recent_update"], "2026-07-19 12:00:00")
        self.assertEqual(runtime_source["health"], "正常")
        self.assertEqual(runtime_source["value_preview"], "runtime")
        self.assertEqual(runtime_source["collected_markdown"], "runtime")
        self.assertEqual(runtime_source["injected_markdown"], "[runtime]\nruntime")
        self.assertTrue(runtime_source["whitelisted"])
        self.assertEqual(runtime_source["update_interval"], "")
        self.assertTrue(runtime_source["valid"])
        broken_source = next(item for item in sense.json()["sources"] if item["id"] == "broken")
        self.assertFalse(broken_source["enabled"])
        self.assertFalse(broken_source["valid"])
        self.assertEqual(broken_source["health"], "异常")
        self.assertIn("sense.json", broken_source["error"])
        self.assertNotIn("must not be injected", sense.text)
        self.assertNotIn('"project"', sense.text)
        self.assertEqual(sense.json()["injection"]["content"], "[runtime]\nruntime")

        refreshed = self.request(app, "POST", "/api/users/alice/sense/runtime/refresh")
        self.assertEqual(refreshed.status_code, 200)
        self.assertTrue(refreshed.json()["updated"])
        self.assertEqual(refreshed.json()["source"]["collected_markdown"], "runtime refreshed")

        disabled_runtime = self.request(
            app,
            "PATCH",
            "/api/users/alice/sense/runtime/enabled",
            json={"enabled": False},
        )
        self.assertEqual(disabled_runtime.status_code, 200)
        self.assertEqual(disabled_runtime.json()["whitelist"], ["__kemo_none__"])
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/sense").json()["summary"]["enabled"],
            0,
        )
        reenabled_runtime = self.request(
            app,
            "PATCH",
            "/api/users/alice/sense/runtime/enabled",
            json={"enabled": True},
        )
        self.assertEqual(reenabled_runtime.status_code, 200)

        enabled_network = self.request(
            app,
            "PATCH",
            "/api/users/alice/sense/network/enabled",
            json={"enabled": True},
        )
        self.assertEqual(enabled_network.status_code, 200)
        self.assertTrue(enabled_network.json()["enabled"])
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/sense").json()["summary"]["enabled"],
            2,
        )
        disabled_network = self.request(
            app,
            "PATCH",
            "/api/users/alice/sense/network/enabled",
            json={"enabled": False},
        )
        self.assertEqual(disabled_network.status_code, 200)
        deleted_network = self.request(app, "DELETE", "/api/users/alice/sense/network")
        self.assertEqual(deleted_network.status_code, 200)
        self.assertTrue(deleted_network.json()["deleted"])
        self.assertFalse((root / "global_sense" / "network").exists())

        prompt = self.request(app, "GET", "/api/users/alice/prompt/sections")
        self.assertEqual(len(prompt.json()["sections"]), len(PROMPT_SECTION_ORDER))
        self.assertNotIn("safe memory preview", prompt.text)
        self.assertIn("expand", prompt.json())
        memory = self.request(app, "GET", "/api/users/alice/memory/summary")
        self.assertEqual(memory.json()["summary"]["seven_days"], 1)
        self.assertEqual(memory.json()["items"][0]["weight"], 2)
        self.assertEqual(memory.json()["items"][0]["filename"], "safe-memory.md")

        settings = self.request(app, "GET", "/api/users/alice/settings")
        self.assertEqual(settings.json()["provider"]["model"], "test-model")
        self.assertEqual(settings.json()["provider"]["reasoning_effort"], "medium")
        self.assertFalse(settings.json()["authentication"]["enabled"])
        self.assertEqual(
            settings.json()["source_policy"]["kemo_graph"]["status"],
            "not_connected",
        )
        self.assertNotIn("super-secret", settings.text)
        self.assertNotIn("api_key", settings.text)


if __name__ == "__main__":
    unittest.main()
