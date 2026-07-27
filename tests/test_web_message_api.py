from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from web.app import create_app
from web.service import WebRunService


INPUT_SOURCE = '''import threading
_stop = threading.Event()
def start(config, message_buffer, files_dir, state_path):
    del config, message_buffer, files_dir, state_path
    _stop.wait()
def stop():
    _stop.set()
'''
OUTPUT_SOURCE = '''def send(message):
    del message
    return True
'''
DETECT_SOURCE = '''from datetime import datetime
def check(config, state):
    del config
    return {**state, "schema_version": 1, "health": "healthy", "last_check": datetime.now().astimezone().isoformat(), "error": None}
'''


class WebMessageApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "users" / "alice").mkdir(parents=True)
        (self.root / "users" / "bob").mkdir(parents=True)
        (self.root / "config").mkdir()
        (self.root / "config" / "message_config.json").write_text("{}\n", "utf-8")

    def request(self, method: str, url: str):
        app = create_app(root=self.root, service=WebRunService(self.root))

        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, url)

        return asyncio.run(invoke())

    def create_module(self, directory_name: str, user: str, *, healthy: bool = True) -> Path:
        module = self.root / "message" / "out" / directory_name
        (module / "files").mkdir(parents=True)
        (module / "log").mkdir()
        (module / "input.py").write_text(INPUT_SOURCE, "utf-8")
        (module / "output.py").write_text(OUTPUT_SOURCE, "utf-8")
        (module / "detect.py").write_text(DETECT_SOURCE, "utf-8")
        (module / "message.md").write_text("", "utf-8")
        (module / "message.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "machine_id": f"machine_{directory_name}",
                    "platform": directory_name,
                    "display_name": f"{directory_name} 适配器",
                    "bound_user": user,
                    "modules": {"input": "input.py", "output": "output.py", "detect": "detect.py"},
                    "capabilities": ["receive_text", "send_text", "receive_file", "send_file"],
                    "allowed_tools": None,
                    "message_buffer": "message.md",
                    "files_dir": "files/",
                    "log_dir": "log/",
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        (module / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "health": "healthy" if healthy else "dead",
                    "last_check": "2026-07-21T12:49:08+08:00",
                    "last_message_at": "2026-07-21T12:49:08+08:00",
                    "error": None if healthy else "connection failed",
                    "latency_ms": 8,
                    "messages_received_today": 2,
                    "messages_sent_today": 1,
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        return module

    def test_status_only_returns_bound_modules_with_paths_files_and_parsed_logs(self) -> None:
        alice = self.create_module("alice_bridge", "alice")
        self.create_module("bob_bridge", "bob")
        (alice / "files" / "meeting_notes.docx").write_bytes(b"document")
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        (alice / "log" / f"{today}.md").write_text(
            f"## {today} 12:49:08 | private | chat-1\n\n"
            "**入站**：请查看会议纪要\n"
            "  - 附件：meeting_notes.docx (application/vnd.openxmlformats-officedocument.wordprocessingml.document, 8 bytes)\n\n"
            "**出站**：会议纪要已收到。\n"
            "  - 出站附件：summary.pdf (users/alice/download/summary.pdf)\n\n---\n",
            "utf-8",
        )

        response = self.request("GET", "/api/users/alice/message/status")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["summary"]["total_transports"], 1)
        self.assertEqual(payload["summary"]["connected_transports"], 1)
        self.assertEqual(payload["summary"]["temporary_files"], 1)
        self.assertEqual(payload["summary"]["today_logs"], 4)
        transport = payload["transports"][0]
        self.assertEqual(transport["id"], "alice_bridge")
        self.assertEqual(transport["bound_user"], "alice")
        self.assertEqual(transport["path"], "message/out/alice_bridge")
        self.assertEqual(transport["files_path"], "message/out/alice_bridge/files")
        self.assertEqual(transport["log_path"], "message/out/alice_bridge/log")
        self.assertEqual(transport["structured_log_path"], "runtime/logs.sqlite3")
        self.assertEqual(transport["temporary_file_count"], 1)
        self.assertEqual(len(transport["logs"]), 4)
        self.assertEqual(
            {(item["direction"], item["kind"]) for item in transport["logs"]},
            {("receive", "text"), ("receive", "file"), ("send", "text"), ("send", "file")},
        )
        self.assertNotIn("bob_bridge", response.text)

    def test_check_connection_updates_state_and_returns_refreshed_transport(self) -> None:
        module = self.create_module("check_bridge", "alice", healthy=False)
        response = self.request(
            "POST",
            "/api/users/alice/message/modules/check_bridge/check",
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["checked"])
        self.assertEqual(payload["transport"]["connection_status"], "connected")
        state = json.loads((module / "state.json").read_text("utf-8"))
        self.assertEqual(state["health"], "healthy")
        self.assertIsNone(state["error"])

    def test_delete_rechecks_bound_user_and_removes_only_current_users_module(self) -> None:
        alice = self.create_module("alice_delete", "alice")
        bob = self.create_module("bob_keep", "bob")

        denied = self.request("DELETE", "/api/users/alice/message/modules/bob_keep")
        self.assertEqual(denied.status_code, 404, denied.text)
        self.assertTrue(bob.is_dir())

        deleted = self.request("DELETE", "/api/users/alice/message/modules/alice_delete")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["deleted"])
        self.assertFalse(alice.exists())
        self.assertTrue(bob.is_dir())

    def test_runtime_callbacks_are_used_for_check_and_safe_removal(self) -> None:
        module = self.create_module("runtime_bridge", "alice")
        calls: list[tuple[str, str, str]] = []

        service = WebRunService(
            self.root,
            message_health_checker=lambda platform, user: (
                calls.append(("check", platform, user))
                or {
                    "schema_version": 1,
                    "health": "healthy",
                    "last_check": "2026-07-21T12:49:08+08:00",
                    "last_message_at": None,
                    "error": None,
                    "latency_ms": 1,
                    "messages_received_today": 0,
                    "messages_sent_today": 0,
                }
            ),
            message_transport_remover=lambda platform, user: calls.append(("delete", platform, user)),
        )
        checked = service.check_message_module("alice", "runtime_bridge")
        self.assertTrue(checked["checked"])
        deleted = service.delete_message_module("alice", "runtime_bridge")
        self.assertTrue(deleted["deleted"])
        self.assertFalse(module.exists())
        self.assertEqual(
            calls,
            [("check", "runtime_bridge", "alice"), ("delete", "runtime_bridge", "alice")],
        )


if __name__ == "__main__":
    unittest.main()
