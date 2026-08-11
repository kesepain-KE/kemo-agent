from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
MODULE_ROOT = ROOT / "global_expand" / "kemo_app"


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, MODULE_ROOT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bridge_auth = _load_module("kemo_app_test_auth", "auth.py")
bridge_upstream = _load_module("kemo_app_test_upstream", "upstream.py")
bridge_lifecycle = _load_module("lifecycle", "lifecycle.py")
bridge_initialize = _load_module("kemo_app_test_initialize", "initialize_config.py")
bridge_update = _load_module("kemo_app_test_update", "data_update.py")
bridge_start = _load_module("kemo_app_test_start", "start_expand.py")

from run.prompt_sources import read_expand_meta  # noqa: E402


class KemoAppExpandTests(unittest.TestCase):
    def test_bridge_declares_current_version(self) -> None:
        source = (MODULE_ROOT / "app.py").read_text(encoding="utf-8")
        manifest = json.loads((MODULE_ROOT / "expand.json").read_text(encoding="utf-8"))
        self.assertIn('VERSION = "1.1.1"', source)
        self.assertIn("v1.1.1", manifest["explain"])
        self.assertIn("**1.1.1**", (MODULE_ROOT / "README.md").read_text(encoding="utf-8"))

    def test_bridge_keeps_android_conversations_in_app_partition(self) -> None:
        source = (MODULE_ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('APP_SOURCE = "app"', source)
        self.assertIn('"source": APP_SOURCE', source)
        self.assertIn('params={"source": APP_SOURCE}', source)
        self.assertIn('@app.get("/v1/conversations/active")', source)
        self.assertIn('params={"source": APP_SOURCE, "client_id": client_id}', source)
        self.assertNotIn('"web" if source == "app" else source', source)

    def test_manifest_is_discoverable_but_published_inactive(self) -> None:
        meta = read_expand_meta(MODULE_ROOT)
        self.assertTrue(meta.valid, meta.error)
        self.assertFalse(meta.open_input)
        self.assertEqual(meta.input_health, "异常")
        self.assertTrue(meta.open_control)
        self.assertEqual(meta.start_update, "data_update.py")
        self.assertEqual(meta.start_expand, "start_expand.py")
        manifest = json.loads((MODULE_ROOT / "expand.json").read_text(encoding="utf-8"))
        self.assertNotIn("recent_update", manifest)

    def test_published_tree_has_no_runtime_identity_or_active_status(self) -> None:
        for name in ("config.json", "users.json", "credential_registry.json", "_server.pid"):
            self.assertFalse((MODULE_ROOT / name).exists(), name)
        initial = (MODULE_ROOT / "input_data.md").read_text(encoding="utf-8")
        self.assertIn("未激活", initial)
        self.assertIn("未完成", initial)
        self.assertNotIn("上游: online", initial)
        self.assertNotIn("在线设备", initial)
        for name in ("initialize_config.py", "lifecycle.py"):
            self.assertTrue((MODULE_ROOT / name).is_file(), name)

    def test_example_configuration_contains_no_live_credentials(self) -> None:
        config = json.loads((MODULE_ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(config["token_sha256"], "")
        self.assertEqual(config["session_secret"], "")
        self.assertEqual(config["upstream_token"], "")
        self.assertEqual(config["upstream_username"], "")
        self.assertEqual(config["upstream_password"], "")

    def test_initializer_creates_local_files_but_does_not_activate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.example.json").write_text(
                (MODULE_ROOT / "config.example.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            result = bridge_initialize.initialize(root)
            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertTrue(result["initialized"])
            self.assertFalse(result["configured"])
            self.assertGreaterEqual(len(config["session_secret"]), 32)
            self.assertEqual(config["token_sha256"], "")
            self.assertEqual(json.loads((root / "users.json").read_text(encoding="utf-8")), {})

    def test_lifecycle_becomes_configured_only_after_all_local_credentials_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = json.loads((MODULE_ROOT / "config.example.json").read_text(encoding="utf-8"))
            config.update(
                {
                    "token_sha256": "a" * 64,
                    "session_secret": "s" * 48,
                    "upstream": "http://127.0.0.1:1457",
                }
            )
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
            (root / "users.json").write_text(
                json.dumps({"mobile-user": {"salt": "salt", "hash": "hash", "enabled": True}}),
                encoding="utf-8",
            )
            state = bridge_lifecycle.inspect_configuration(root)
            self.assertTrue(state["initialized"])
            self.assertTrue(state["configured"])
            self.assertEqual(state["missing"], [])
            self.assertEqual(state["enabled_users"], 1)

    def test_uninitialized_update_never_probes_localhost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "expand.json"
            input_path = root / "input_data.md"
            manifest_path.write_text(
                (MODULE_ROOT / "expand.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            with (
                mock.patch.object(bridge_update, "BASE_DIR", root),
                mock.patch.object(bridge_update, "INPUT_PATH", input_path),
                mock.patch.object(bridge_update, "MANIFEST_PATH", manifest_path),
                mock.patch.object(bridge_update, "CONNECTIONS_PATH", root / "_connections.json"),
                mock.patch.object(bridge_update.urllib.request, "urlopen") as urlopen,
            ):
                result = bridge_update.update()
            self.assertEqual(result["status"], "inactive")
            self.assertFalse(result["active"])
            urlopen.assert_not_called()
            self.assertIn("未激活", input_path.read_text(encoding="utf-8"))
            refreshed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertFalse(refreshed_manifest["open_input"])
            self.assertEqual(refreshed_manifest["input_health"], "正常")

    def test_start_refuses_to_spawn_before_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch.object(bridge_start, "BASE_PATH", root),
                mock.patch.object(bridge_start, "PID_PATH", str(root / "_server.pid")),
                mock.patch.object(bridge_start, "LOG_PATH", str(root / "logs" / "server.log")),
                mock.patch.object(bridge_start, "_port_open") as port_open,
                mock.patch.object(bridge_start.subprocess, "Popen") as popen,
            ):
                result = bridge_start.start()
            self.assertFalse(result["ok"])
            self.assertFalse(result["active"])
            self.assertEqual(result["error"], "bridge_not_initialized")
            port_open.assert_not_called()
            popen.assert_not_called()

    def test_device_token_user_password_and_session_lifecycle(self) -> None:
        raw_token = "test-device-token-with-at-least-32-characters"
        config = {"token_sha256": hashlib.sha256(raw_token.encode()).hexdigest()}
        self.assertTrue(bridge_auth.token_ok(f"Bearer {raw_token}", config))
        self.assertFalse(bridge_auth.token_ok("Bearer wrong", config))

        with tempfile.TemporaryDirectory() as directory:
            store = bridge_auth.UserStore(Path(directory) / "users.json", 100_000)
            store.set_password("mobile-user", "long-enough-password")
            self.assertTrue(store.verify("mobile-user", "long-enough-password"))
            self.assertFalse(store.verify("mobile-user", "wrong-password"))

        sessions = bridge_auth.SessionManager("test-session-secret", 300)
        token, expires_at = sessions.issue("mobile-user")
        self.assertGreater(expires_at, 0)
        self.assertEqual(sessions.verify(token).username, "mobile-user")
        sessions.revoke(token)
        self.assertIsNone(sessions.verify(token))

    def test_sse_stream_has_unbounded_read_timeout_but_rest_stays_bounded(self) -> None:
        client = bridge_upstream.UpstreamClient(
            {"upstream": "http://127.0.0.1:1457", "request_timeout": 30}
        )
        try:
            self.assertEqual(client.request_timeout, 30)
            self.assertEqual(client.client.timeout.read, 30)
            self.assertIsNone(client._sse_timeout.read)  # noqa: SLF001 - transport contract
            self.assertEqual(client._sse_timeout.connect, 5)  # noqa: SLF001
        finally:
            asyncio.run(client.close())


if __name__ == "__main__":
    unittest.main()
