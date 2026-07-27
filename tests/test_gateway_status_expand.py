from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = ROOT / "global_expand" / "kemo_gateway_status"
sys.path.insert(0, str(MODULE_ROOT))
import gateway_status as gateway  # noqa: E402

from run.prompt_sources import read_expand_meta  # noqa: E402


def status_payload() -> dict[str, object]:
    metrics = {
        "calls": 12,
        "successes": 10,
        "failures": 2,
        "cancellations": 0,
        "incompletes": 0,
        "running": 0,
        "success_rate": 10 / 12,
        "average_latency_ms": 42.5,
        "cache_hit_rate": 0.4,
        "tokens": {
            "input_tokens": 1000,
            "cached_input_tokens": 400,
            "output_tokens": 200,
            "reasoning_tokens": 100,
            "total_tokens": 1200,
        },
    }
    return {
        "object": "kemo.gateway_status",
        "generated_at": "2026-07-28T12:00:00+00:00",
        "protocol_version": "1.0",
        "runtime": {
            "instance_id": "gateway-1",
            "phase": "running",
            "active_executions": 2,
            "started_at": "2026-07-28T00:00:00+00:00",
            "private_future_field": "must-not-persist",
        },
        "version": {
            "status": "up_to_date",
            "update_available": False,
            "local": {"version": "0.6.0", "protocol_version": "1.0", "secret": "no"},
            "remote": {"version": "0.6.0"},
            "message": "当前已是最新版本",
        },
        "registry": {
            "providers": [{
                "provider_id": "fake",
                "enabled": True,
                "registered_models": ["fake-model"],
            }],
            "registered_provider_ids": ["fake"],
            "enabled_models": [{"model": "fake-model", "provider_id": "fake"}],
        },
        "control": {
            "highest_priority_system_prompt": "never-persist-this-system-prompt",
            "disabled_providers": [],
            "disabled_models": [],
        },
        "statistics": {
            "date": "2026-07-28",
            "timezone": "Asia/Shanghai",
            "summary": metrics,
            "token_cache_rate": 0.4,
            "rankings": {
                "providers": [{"id": "fake", **metrics}],
                "models": [{"id": "fake-model", **metrics}],
                "gateway_keys": [{"id": "agent-key", **metrics}],
            },
        },
        "logs": {
            "recent": [{
                "started_at": "2026-07-28T11:00:00+00:00",
                "provider_id": "fake",
                "model": "fake-model",
                "status": "completed",
                "request_body": "must-not-persist",
                "tokens": {"total_tokens": 120},
            }],
            "successful": [],
            "failed": [{
                "started_at": "2026-07-28T10:00:00+00:00",
                "provider_id": "fake",
                "model": "fake-model",
                "status": "failed",
                "error_code": "UPSTREAM_UNAVAILABLE",
                "error_message": "must-not-persist-raw-error",
            }],
            "last_invocation": None,
        },
    }


class GatewayStatusExpandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.module = Path(self.temporary.name)
        (self.module / "expand.json").write_text(
            json.dumps({
                "name": "Kemo 网关运行状态",
                "explain": "test",
                "open_input": False,
                "input_data": "input_data.md",
                "input_health": "正常",
                "start_update": "data_update.py",
                "open_control": True,
                "start_expand": "start_expand.py",
                "start_control": "expand_control.md",
            }, ensure_ascii=False),
            "utf-8",
        )
        (self.module / "input_data.md").write_text("inactive", "utf-8")
        self.path_patch = patch.multiple(
            gateway,
            BASE_DIR=self.module,
            CONFIG_PATH=self.module / "gateway_config.json",
            MANIFEST_PATH=self.module / "expand.json",
            INPUT_PATH=self.module / "input_data.md",
            LAST_RUN_PATH=self.module / "_last_run.json",
            DATA_PATH=self.module / "data" / "gateway_status.json",
            CHART_PATH=self.module / "artifacts" / "gateway_status.png",
        )
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temporary.cleanup()

    def test_real_manifest_is_discoverable_and_defaults_to_inactive(self) -> None:
        meta = read_expand_meta(MODULE_ROOT)
        self.assertTrue(meta.valid, meta.error)
        self.assertFalse(meta.open_input)
        self.assertTrue(meta.open_control)
        self.assertEqual(meta.start_update, "data_update.py")

    def test_unconfigured_update_is_successful_but_does_not_enable_injection(self) -> None:
        result = gateway.update_snapshot()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "inactive")
        manifest = json.loads((self.module / "expand.json").read_text("utf-8"))
        self.assertFalse(manifest["open_input"])
        self.assertEqual(manifest["input_health"], "正常")
        self.assertFalse((self.module / "data" / "gateway_status.json").exists())

    def test_activation_persists_only_token_config_and_secret_free_outputs(self) -> None:
        safe = gateway.sanitize_snapshot(status_payload())
        with patch.object(gateway, "fetch_status", return_value=safe):
            result = gateway.activate({
                "base_url": "http://127.0.0.1:7531/",
                "status_token": "status-secret-token",
                "ranking_limit": 10,
                "log_limit": 5,
            })

        self.assertTrue(result["ok"])
        self.assertNotIn("status-secret-token", json.dumps(result, ensure_ascii=False))
        config = json.loads((self.module / "gateway_config.json").read_text("utf-8"))
        self.assertEqual(config["status_token"], "status-secret-token")
        manifest = json.loads((self.module / "expand.json").read_text("utf-8"))
        self.assertTrue(manifest["open_input"])
        self.assertEqual(manifest["input_health"], "正常")

        persisted = "\n".join(
            path.read_text("utf-8")
            for path in (
                self.module / "input_data.md",
                self.module / "data" / "gateway_status.json",
                self.module / "_last_run.json",
            )
        )
        self.assertNotIn("status-secret-token", persisted)
        self.assertNotIn("never-persist-this-system-prompt", persisted)
        self.assertNotIn("must-not-persist-raw-error", persisted)
        self.assertNotIn("must-not-persist", persisted)
        with Image.open(self.module / "artifacts" / "gateway_status.png") as chart:
            self.assertEqual(chart.format, "PNG")
            self.assertEqual(chart.size, (1600, 900))

    def test_status_request_uses_dedicated_bearer_endpoint_and_sanitizes_response(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit: int) -> bytes:
                return json.dumps(status_payload(), ensure_ascii=False).encode("utf-8")

        config = gateway.GatewayConfig(
            base_url="http://127.0.0.1:7531",
            status_token="status-only-token",
            ranking_limit=7,
            log_limit=9,
        )
        with patch.object(gateway, "_open_status_request", return_value=FakeResponse()) as request_call:
            snapshot = gateway.fetch_status(config, target_date="2026-07-28")

        request = request_call.call_args.args[0]
        self.assertTrue(request.full_url.startswith("http://127.0.0.1:7531/status?"))
        self.assertIn("ranking_limit=7", request.full_url)
        self.assertIn("log_limit=9", request.full_url)
        self.assertIn("date=2026-07-28", request.full_url)
        self.assertEqual(request.headers["Authorization"], "Bearer status-only-token")
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("never-persist-this-system-prompt", serialized)
        self.assertNotIn("request_body", serialized)
        self.assertNotIn("error_message", serialized)

    def test_status_token_rejects_control_characters_and_http_redirects(self) -> None:
        with self.assertRaises(gateway.GatewayStatusError):
            gateway.config_from_mapping({
                "base_url": "http://127.0.0.1:7531",
                "status_token": "secret\r\nForwarded: value",
            })
        handler = gateway._RejectRedirects()  # noqa: SLF001 - security boundary contract
        self.assertIsNone(
            handler.redirect_request(None, None, 302, "Found", {}, "https://example.test/status")
        )

    def test_deactivate_deletes_local_credentials_and_artifacts(self) -> None:
        gateway._atomic_json(  # noqa: SLF001 - verifies module-local lifecycle
            self.module / "gateway_config.json",
            {"base_url": "http://127.0.0.1:7531", "status_token": "secret"},
        )
        (self.module / "data").mkdir()
        (self.module / "data" / "gateway_status.json").write_text("{}", "utf-8")
        (self.module / "artifacts").mkdir()
        (self.module / "artifacts" / "gateway_status.png").write_bytes(b"png")

        result = gateway.deactivate()
        self.assertTrue(result["ok"])
        self.assertFalse((self.module / "gateway_config.json").exists())
        self.assertFalse((self.module / "data" / "gateway_status.json").exists())
        self.assertFalse((self.module / "artifacts" / "gateway_status.png").exists())
        manifest = json.loads((self.module / "expand.json").read_text("utf-8"))
        self.assertFalse(manifest["open_input"])


if __name__ == "__main__":
    unittest.main()
