from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from agents._runtime.user_packages import create_user_agent_package
from web.app import create_app
from web.auth import WebAuthConfig
from web.service import AVATAR_MAX_BYTES, WebRunService


class WebCompletionApiTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for path in (
            root / "config",
            root / "users" / "alice" / "history",
            root / "users" / "alice" / "file_upload",
            root / "users" / "alice" / "download",
            root / "tmp",
        ):
            path.mkdir(parents=True, exist_ok=True)
        (root / "config" / "global_config.json").write_text("{}", "utf-8")
        return temporary, root

    def request(self, app, method: str, url: str, **kwargs):
        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(invoke())

    def test_user_and_tmp_file_apis_are_recursive_and_traversal_safe(self) -> None:
        _, root = self.make_root()
        upload = root / "users" / "alice" / "file_upload"
        (upload / "nested").mkdir()
        (upload / "nested" / "a.txt").write_text("alpha", "utf-8")
        (upload / "root.bin").write_bytes(b"123")
        (upload / ".hidden").write_text("hidden", "utf-8")
        (upload / "__pycache__").mkdir()
        (upload / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")
        (root / "tmp" / "cache.txt").write_text("cache", "utf-8")
        app = create_app(service=WebRunService(root))

        listed = self.request(app, "GET", "/api/users/alice/files/file_upload")
        self.assertEqual(listed.status_code, 200)
        body = listed.json()
        self.assertEqual(body["summary"]["total_files"], 2)
        self.assertEqual(body["summary"]["total_dirs"], 1)
        self.assertEqual(
            [(item["type"], item["name"]) for item in body["tree"]],
            [("directory", "nested"), ("file", "root.bin")],
        )
        self.assertEqual(
            body["tree"][0]["children"][0]["relative_path"],
            "nested/a.txt",
        )

        downloaded = self.request(
            app,
            "GET",
            "/api/users/alice/files/file_upload/download",
            params={"path": "nested/a.txt"},
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b"alpha")
        self.assertIn("attachment", downloaded.headers["content-disposition"])

        for method, url in (
            ("GET", "/api/users/alice/files/file_upload/download"),
            ("DELETE", "/api/users/alice/files/file_upload"),
            ("DELETE", "/api/tmp"),
        ):
            with self.subTest(method=method, url=url):
                response = self.request(
                    app,
                    method,
                    url,
                    params={"path": "../outside.txt"},
                )
                self.assertEqual(response.status_code, 400)

        directory_delete = self.request(
            app,
            "DELETE",
            "/api/users/alice/files/file_upload",
            params={"path": "nested"},
        )
        self.assertEqual(directory_delete.status_code, 404)
        deleted = self.request(
            app,
            "DELETE",
            "/api/users/alice/files/file_upload",
            params={"path": "nested/a.txt"},
        )
        self.assertTrue(deleted.json()["deleted"])
        self.assertFalse((upload / "nested" / "a.txt").exists())

        invalid_scope = self.request(
            app,
            "GET",
            "/api/users/alice/files/history",
        )
        self.assertEqual(invalid_scope.status_code, 400)
        tmp = self.request(app, "GET", "/api/tmp")
        self.assertEqual(tmp.json()["summary"]["total_files"], 1)
        removed_tmp = self.request(
            app,
            "DELETE",
            "/api/tmp",
            params={"path": "cache.txt"},
        )
        self.assertTrue(removed_tmp.json()["deleted"])

    def test_tmp_batch_and_all_delete_validate_before_mutating(self) -> None:
        _, root = self.make_root()
        tmp = root / "tmp"
        (tmp / "nested").mkdir()
        (tmp / "nested" / "a.tmp").write_text("a", "utf-8")
        (tmp / "nested" / "b.tmp").write_text("b", "utf-8")
        app = create_app(service=WebRunService(root))

        rejected = self.request(
            app,
            "POST",
            "/api/tmp/delete-many",
            json={"paths": ["nested/a.tmp", "../outside.tmp"]},
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertTrue((tmp / "nested" / "a.tmp").is_file())
        self.assertTrue((tmp / "nested" / "b.tmp").is_file())

        removed = self.request(
            app,
            "POST",
            "/api/tmp/delete-many",
            json={"paths": ["nested/a.tmp", "nested/b.tmp", "nested/a.tmp"]},
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["deleted_count"], 2)
        self.assertEqual(
            removed.json()["deleted_paths"],
            ["nested/a.tmp", "nested/b.tmp"],
        )
        self.assertFalse((tmp / "nested").exists())

        (tmp / "cache" / "deep").mkdir(parents=True)
        (tmp / "cache" / "deep" / "one.log").write_text("1", "utf-8")
        (tmp / "root.log").write_text("2", "utf-8")
        removed_all = self.request(app, "DELETE", "/api/tmp/all")
        self.assertEqual(removed_all.status_code, 200, removed_all.text)
        self.assertEqual(removed_all.json()["deleted_count"], 2)
        self.assertTrue(tmp.is_dir())
        self.assertEqual(list(tmp.iterdir()), [])

        empty = self.request(app, "DELETE", "/api/tmp/all")
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json(), {"deleted_paths": [], "deleted_count": 0})

    def test_user_file_batch_and_all_delete_are_scoped_and_atomic(self) -> None:
        _, root = self.make_root()
        upload = root / "users" / "alice" / "file_upload"
        download = root / "users" / "alice" / "download"
        (upload / "nested").mkdir()
        (upload / "nested" / "a.txt").write_text("a", "utf-8")
        (upload / "nested" / "b.txt").write_text("b", "utf-8")
        app = create_app(service=WebRunService(root))

        rejected = self.request(
            app,
            "POST",
            "/api/users/alice/files/file_upload/delete-many",
            json={"paths": ["nested/a.txt", "../outside.txt"]},
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertTrue((upload / "nested" / "a.txt").is_file())
        self.assertTrue((upload / "nested" / "b.txt").is_file())

        removed = self.request(
            app,
            "POST",
            "/api/users/alice/files/file_upload/delete-many",
            json={"paths": ["nested/a.txt", "nested/b.txt", "nested/a.txt"]},
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["user"], "alice")
        self.assertEqual(removed.json()["scope"], "file_upload")
        self.assertEqual(removed.json()["deleted_count"], 2)
        self.assertFalse((upload / "nested").exists())
        self.assertTrue(upload.is_dir())

        (download / "reports" / "daily").mkdir(parents=True)
        (download / "reports" / "daily" / "one.md").write_text("1", "utf-8")
        (download / "result.txt").write_text("2", "utf-8")
        removed_all = self.request(
            app,
            "DELETE",
            "/api/users/alice/files/download/all",
        )
        self.assertEqual(removed_all.status_code, 200, removed_all.text)
        self.assertEqual(removed_all.json()["scope"], "download")
        self.assertEqual(removed_all.json()["deleted_count"], 2)
        self.assertTrue(download.is_dir())
        self.assertEqual(list(download.iterdir()), [])

        empty = self.request(
            app,
            "DELETE",
            "/api/users/alice/files/download/all",
        )
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["deleted_paths"], [])
        self.assertEqual(empty.json()["deleted_count"], 0)

    def test_avatar_upload_read_validation_and_public_logo(self) -> None:
        _, root = self.make_root()
        (root / "kemo-agent.jpg").write_bytes(b"logo")
        app = create_app(
            service=WebRunService(root),
            auth_config=WebAuthConfig(
                access_token="token",
                session_secret="test-secret",
            ),
        )

        logo = self.request(app, "GET", "/api/logo")
        self.assertEqual(logo.status_code, 200)
        self.assertEqual(logo.content, b"logo")
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/avatar").status_code,
            401,
        )
        token = {"token": "token"}
        missing = self.request(
            app,
            "GET",
            "/api/users/alice/avatar",
            params=token,
        )
        self.assertEqual(missing.status_code, 204)

        png = b"\x89PNG\r\n\x1a\n" + b"image-data"
        uploaded = self.request(
            app,
            "POST",
            "/api/users/alice/avatar",
            params=token,
            files={"file": ("avatar.png", png, "image/png")},
        )
        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(uploaded.json()["format"], "image/png")
        avatar = self.request(app, "GET", "/api/users/alice/avatar", params=token)
        self.assertEqual(avatar.content, png)
        self.assertTrue(avatar.headers["content-type"].startswith("image/png"))

        spoofed = self.request(
            app,
            "POST",
            "/api/users/alice/avatar",
            params=token,
            files={"file": ("fake.png", b"not-an-image", "image/png")},
        )
        self.assertEqual(spoofed.status_code, 400)
        oversized = b"\x89PNG\r\n\x1a\n" + b"x" * AVATAR_MAX_BYTES
        too_large = self.request(
            app,
            "POST",
            "/api/users/alice/avatar",
            params=token,
            files={"file": ("large.png", oversized, "image/png")},
        )
        self.assertEqual(too_large.status_code, 400)

    def test_user_and_global_soul_read_write_contract(self) -> None:
        _, root = self.make_root()
        app = create_app(service=WebRunService(root))

        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/soul").status_code,
            404,
        )
        content = "# Alice\n\n保持简洁。"
        updated = self.request(
            app,
            "PUT",
            "/api/users/alice/soul",
            json={"content": content},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["content"], content)
        self.assertEqual(
            (root / "users" / "alice" / "user_soul.md").read_text("utf-8"),
            content,
        )
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/soul").json()["content"],
            content,
        )
        self.assertEqual(
            self.request(
                app,
                "PUT",
                "/api/users/alice/soul",
                json={"content": " "},
            ).status_code,
            400,
        )

        global_content = "# Global\n\n安全底线。"
        global_updated = self.request(
            app,
            "PUT",
            "/api/global-soul",
            json={"content": global_content},
        )
        self.assertEqual(global_updated.status_code, 200)
        self.assertEqual(
            self.request(app, "GET", "/api/global-soul").json()["content"],
            global_content,
        )

    def test_agents_message_status_and_expand_inventory(self) -> None:
        _, root = self.make_root()
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
        global_expand = root / "global_expand" / "weather"
        global_expand.mkdir(parents=True)
        (global_expand / "expand.json").write_text("{}", "utf-8")
        (global_expand / "nested").mkdir()
        (global_expand / "nested" / "data.md").write_text("weather", "utf-8")
        user_expand = root / "users" / "alice" / "expand" / "personal"
        user_expand.mkdir(parents=True)
        (user_expand / "notes.md").write_text("personal", "utf-8")

        message_config = {
            "bindings": [
                {
                    "platform": "example",
                    "external_user_id": "external-1",
                    "internal_user": "alice",
                    "chat_type": "private",
                }
            ]
        }
        (root / "config" / "message_config.json").write_text(
            json.dumps(message_config),
            "utf-8",
        )
        plugin = root / "message" / "out" / "example"
        plugin.mkdir(parents=True)
        for module in ("input.py", "output.py", "detect.py"):
            (plugin / module).write_text("", "utf-8")
        (plugin / "message.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "machine_id": "example_01",
                    "platform": "example",
                    "display_name": "Example",
                    "bound_user": "alice",
                    "modules": {
                        "input": "input.py",
                        "output": "output.py",
                        "detect": "detect.py",
                    },
                    "capabilities": ["receive_text", "send_text"],
                    "allowed_tools": None,
                    "message_buffer": "message.md",
                    "files_dir": "files",
                    "log_dir": "log",
                }
            ),
            "utf-8",
        )
        (plugin / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "health": "healthy",
                    "last_check": "2026-07-21T00:00:00+08:00",
                    "last_message_at": None,
                    "error": None,
                    "latency_ms": 3,
                    "messages_received_today": 2,
                    "messages_sent_today": 1,
                }
            ),
            "utf-8",
        )
        service = WebRunService(
            root,
            runtime_status_provider=lambda: {
                "state": "running",
                "components": {
                    "transport:example": {
                        "name": "example",
                        "state": "running",
                        "last_error": None,
                    }
                },
            },
        )
        app = create_app(service=service)

        agents = self.request(app, "GET", "/api/users/alice/agents").json()
        self.assertEqual(agents["summary"]["user"], 1)
        self.assertEqual(agents["agents"][0]["name"], "observer_agent")
        self.assertIn(
            "agent.json",
            {item["relative_path"] for item in agents["agents"][0]["files"]},
        )

        message = self.request(
            app,
            "GET",
            "/api/users/alice/message/status",
        ).json()
        self.assertEqual(message["bindings"][0]["match_priority"], 3)
        self.assertEqual(message["transports"][0]["state"], "running")
        self.assertEqual(message["transports"][0]["health"], "healthy")
        self.assertEqual(message["summary"]["running_transports"], 1)

        expands = self.request(app, "GET", "/api/users/alice/expand").json()
        self.assertEqual(expands["summary"], {"total": 2, "global": 1, "shared": 0, "user": 1})
        global_item = expands["expands"][0]["items"][0]
        self.assertTrue(global_item["has_register"])
        self.assertIn(
            "weather/nested/data.md",
            {item["relative_path"] for item in global_item["files"]},
        )


if __name__ == "__main__":
    unittest.main()
