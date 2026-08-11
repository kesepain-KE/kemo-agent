from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


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

from run.prompt_sources import read_expand_meta  # noqa: E402


class KemoAppExpandTests(unittest.TestCase):
    def test_bridge_declares_current_version(self) -> None:
        source = (MODULE_ROOT / "app.py").read_text(encoding="utf-8")
        manifest = json.loads((MODULE_ROOT / "expand.json").read_text(encoding="utf-8"))
        self.assertIn('VERSION = "1.1.0"', source)
        self.assertIn("v1.1.0", manifest["explain"])
        self.assertIn("**1.1.0**", (MODULE_ROOT / "README.md").read_text(encoding="utf-8"))

    def test_manifest_is_discoverable_with_data_and_control_enabled(self) -> None:
        meta = read_expand_meta(MODULE_ROOT)
        self.assertTrue(meta.valid, meta.error)
        self.assertTrue(meta.open_input)
        self.assertTrue(meta.open_control)
        self.assertEqual(meta.start_update, "data_update.py")
        self.assertEqual(meta.start_expand, "start_expand.py")

    def test_example_configuration_contains_no_live_credentials(self) -> None:
        config = json.loads((MODULE_ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(config["token_sha256"], "")
        self.assertEqual(config["session_secret"], "")
        self.assertEqual(config["upstream_token"], "")
        self.assertEqual(config["upstream_username"], "")
        self.assertEqual(config["upstream_password"], "")

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
