from __future__ import annotations

from http.client import HTTPMessage

import io
import copy
import urllib.request
from tests.type_assertions import http_headers

import yaml
import json
from pathlib import Path
from typing import Any
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
MODULE_ROOT = ROOT / "expand" / "global" / "kemo_gateway_status"
sys.path.insert(0, str(MODULE_ROOT))
import gateway_status as gateway  # noqa: E402

from run.modules import read_manifest, ModuleSpecError  # noqa: E402


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


def current_status_payload() -> dict[str, Any]:
    payload: dict[str, Any] = status_payload()
    payload["protocol_version"] = "2.0"
    payload["version"].update(branch="kemo-preview", scope="framework")
    payload["control"].update(enabled=True, revision="r1", disabled_upstreams=["offline"])
    payload["upstreams"] = [{
        "id": "local", "name": "Local", "adapter_id": "openai-chat", "enabled": True,
        "base_url": "must-not-persist-url", "headers": {"X-Key": "must-not-persist-header"},
        "credentials": [{
            "id": "c1", "status": "cooldown", "checked_at": "2026-11-10T10:00:00+00:00",
            "error_code": "RATE_LIMITED", "provider_status": 429, "cooldown_seconds": 12,
            "encrypted": "must-not-persist-credential",
        }],
    }]
    payload["routing"] = {
        "max_attempts": 3, "cooldown_seconds": 30,
        "models": [{"model": "assistant", "task": "llm", "enabled": True, "verification": "verified"}],
        "deployments": [{
            "id": "d1", "public_model": "assistant", "upstream_id": "local",
            "upstream_model": "remote", "enabled": True, "priority": 2, "weight": 3,
            "rpm": 10, "cooldown_seconds": 12, "private": "must-not-persist-deployment",
        }],
    }
    payload["plugins"] = {"revision": "p1", "items": [{
        "id": "example", "name": "Example", "version": "1.2.0", "category": "model",
        "enabled": True, "status": "degraded", "model_count": 1, "models": ["plugin-model"],
        "provider_id": "plugin", "error_code": "PLUGIN_START_FAILED",
        "update_available": True, "update_version": "1.3.0",
        "config": {"token": "must-not-persist-plugin"}, "error": "must-not-persist-error",
    }]}
    pack = {"version": "1.0.0", "items": {"example": "1.0.0"}, "private": "must-not-persist-pack"}
    payload["resources"] = {
        "running": {"protocol_adapters": pack, "provider_templates": pack},
        "installed": {"protocol_adapters": {**pack, "version": "1.1.0"}, "provider_templates": pack},
        "restart_required": True, "error_code": None,
        "user": {"provider_templates": [{
            "id": "custom", "name": "Custom", "version": "1.0.0", "source": "user",
            "enabled": False, "valid": True, "template": {"secret": "must-not-persist-template"},
        }]},
        "update": {
            "available": True, "branch": "kemo-preview", "target_commit": "a" * 40,
            "private": "must-not-persist-plan",
            "groups": {"protocol_adapters": {
                "current": "1.1.0", "target": "1.2.0", "available": True,
                "changes": [{"id": "example", "current": "1.0.0", "target": "1.2.0",
                             "url": "must-not-persist-download"}],
            }},
        },
    }
    metrics = {
        "calls": 40, "successful": 30, "finished": 35, "success_rate": 30 / 35,
        "input_tokens": 2000, "output_tokens": 500, "total_tokens": 2500,
        "cached_tokens": 1000, "cache_hit_rate": .5, "latency_ms": 80, "first_token_ms": 15,
        "unknown_cost_calls": 2, "estimated_calls": 1, "costs": {"USD": .5},
        "display_cost": {"amount": 3.5, "currency": "CNY", "approximate": True,
                         "unconverted_currencies": [], "secret": "must-not-persist-cost"},
        "private": "must-not-persist-metrics",
    }
    payload["statistics"]["overview"] = {
        "period": "7d", "timezone": "Asia/Shanghai",
        "start": "2026-11-03T10:00:00+00:00", "end": "2026-11-10T10:00:00+00:00",
        "exchange_rate_date": "2026-11-06", "summary": metrics,
        "rankings": {"provider": [{"id": "local", "name": "Local", **metrics}],
                     "model": [], "key": []},
    }
    return payload


class GatewayStatusExpandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.module = Path(self.temporary.name)
        (self.module / "module.yaml").write_text(
            json.dumps({
                'name': 'Kemo 网关运行状态',
                'explain': 'test',
                'input_open': False,
                'input_file': 'input_data.md',
                'health': '正常',
                'update_script': 'data_update.py',
                'control_open': True,
                'control_script': 'start_expand.py',
                'control_entry': 'expand_control.md',
                'kind': 'expand',
            }, ensure_ascii=False),
            "utf-8",
        )
        (self.module / "input_data.md").write_text("inactive", "utf-8")
        self.path_patch = patch.multiple(
            gateway,
            BASE_DIR=self.module,
            CONFIG_PATH=self.module / "gateway_config.json",
            MANIFEST_PATH=self.module / "module.yaml",
            INPUT_PATH=self.module / "input_data.md",
            LAST_RUN_PATH=self.module / "_last_run.json",
            DATA_PATH=self.module / "data" / "gateway_status.json",
            CHART_PATH=self.module / "artifacts" / "gateway_status.png",
            PANEL_VALUES_PATH=self.module / "module" / "panel.values.json",
            PANEL_STATUS_PATH=self.module / "module" / "status.json",
        )
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temporary.cleanup()

    def test_real_manifest_is_discoverable_and_defaults_to_inactive(self) -> None:
        meta = read_manifest(MODULE_ROOT, kind_hint="expand", scope="global")
        self.assertEqual(meta.kind, "expand")
        self.assertFalse(meta.input_open)
        self.assertTrue(meta.control_open)
        self.assertEqual(meta.update_script.name, "data_update.py")

    def test_unconfigured_update_is_successful_but_does_not_enable_injection(self) -> None:
        result = gateway.update_snapshot()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "inactive")
        manifest = yaml.safe_load((self.module / "module.yaml").read_text("utf-8"))
        self.assertFalse(manifest["input_open"])
        self.assertEqual(manifest["health"], "正常")
        self.assertFalse((self.module / "data" / "gateway_status.json").exists())

    def test_inactive_check_time_does_not_change_collected_business_data(self) -> None:
        from cron.system_execution import module_data_signature

        gateway._inactive_output("2026-10-31 19:00:00")
        first = module_data_signature(self.module, "module.yaml")
        original = (self.module / "input_data.md").read_bytes()
        gateway._inactive_output("2026-10-31 19:00:30")
        self.assertEqual((self.module / "input_data.md").read_bytes(), original)
        self.assertEqual(module_data_signature(self.module, "module.yaml"), first)
        manifest = yaml.safe_load((self.module / "module.yaml").read_text("utf-8"))
        self.assertEqual(manifest["recent_update"], "2026-10-31 19:00:30")

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
        manifest = yaml.safe_load((self.module / "module.yaml").read_text("utf-8"))
        self.assertTrue(manifest["input_open"])
        self.assertEqual(manifest["health"], "正常")

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

    def test_panel_endpoint_can_be_saved_before_token_and_reused_on_activation(self) -> None:
        pending = gateway.configure_endpoint({
            "scheme": "http",
            "gateway_ip": "127.0.0.1",
            "gateway_port": 9753,
        })
        self.assertTrue(pending["ok"])
        self.assertEqual(pending["status"], "pending_token")
        self.assertEqual(pending["base_url"], "http://127.0.0.1:9753")

        safe = gateway.sanitize_snapshot(status_payload())
        with patch.object(gateway, "fetch_status", return_value=safe):
            activated = gateway.activate({"status_token": "status-secret-token"})
        self.assertEqual(activated["base_url"], "http://127.0.0.1:9753")
        config = json.loads((self.module / "gateway_config.json").read_text("utf-8"))
        self.assertEqual(config["status_token"], "status-secret-token")

    def test_panel_endpoint_change_preserves_token_and_failed_probe_keeps_old_config(self) -> None:
        gateway._atomic_json(  # noqa: SLF001 - fixture for panel reconfiguration
            self.module / "gateway_config.json",
            {
                "schema_version": 1,
                "base_url": "http://127.0.0.1:7531",
                "status_token": "status-secret-token",
                "timeout_seconds": 15,
                "ranking_limit": 20,
                "log_limit": 20,
            },
        )
        with patch.object(gateway, "fetch_status", side_effect=gateway.GatewayStatusError("new endpoint offline")):
            with self.assertRaisesRegex(gateway.GatewayStatusError, "offline"):
                gateway.configure_endpoint({
                    "scheme": "http",
                    "gateway_ip": "127.0.0.1",
                    "gateway_port": 9753,
                })

        config = json.loads((self.module / "gateway_config.json").read_text("utf-8"))
        self.assertEqual(config["base_url"], "http://127.0.0.1:7531")
        self.assertEqual(config["status_token"], "status-secret-token")
        panel_status = json.loads((self.module / "module" / "status.json").read_text("utf-8"))
        self.assertEqual(panel_status["backend_status"], "离线")
        self.assertIn("offline", panel_status["last_error"])
        self.assertNotIn("status-secret-token", json.dumps(panel_status, ensure_ascii=False))

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
            period="7d",
        )
        with patch.object(gateway, "_open_status_request", return_value=FakeResponse()) as request_call:
            snapshot = gateway.fetch_status(config, target_date="2026-07-28")

        request = request_call.call_args.args[0]
        self.assertTrue(request.full_url.startswith("http://127.0.0.1:7531/status?"))
        self.assertIn("ranking_limit=7", request.full_url)
        self.assertIn("log_limit=9", request.full_url)
        self.assertIn("date=2026-07-28", request.full_url)
        self.assertIn("period=7d", request.full_url)
        self.assertEqual(request.headers["Authorization"], "Bearer status-only-token")
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("never-persist-this-system-prompt", serialized)
        self.assertNotIn("request_body", serialized)
        self.assertNotIn("error_message", serialized)

    def test_current_snapshot_keeps_operational_fields_and_filters_nested_secrets(self):
        snapshot = gateway.sanitize_snapshot(current_status_payload())
        self.assertEqual(snapshot["version"]["branch"], "kemo-preview")
        self.assertTrue(snapshot["control"]["enabled"])
        self.assertEqual(snapshot["control"]["disabled_upstreams"], ["offline"])
        self.assertEqual(snapshot["upstreams"][0]["credentials"][0]["cooldown_seconds"], 12)
        self.assertEqual(snapshot["routing"]["deployments"][0]["weight"], 3)
        self.assertEqual(snapshot["plugins"]["items"][0]["status"], "degraded")
        self.assertEqual(snapshot["plugins"]["items"][0]["update_version"], "1.3.0")
        self.assertTrue(snapshot["resources"]["restart_required"])
        self.assertEqual(snapshot["resources"]["installed"]["protocol_adapters"]["version"], "1.1.0")
        overview = snapshot["statistics"]["overview"]
        self.assertEqual(overview["summary"]["display_cost"]["amount"], 3.5)
        self.assertEqual(overview["rankings"]["provider"][0]["id"], "local")
        self.assertNotIn("must-not-persist", json.dumps(snapshot))
        self.assertNotIn("never-persist", json.dumps(snapshot))
        # The dashboard reads an already sanitized file through the same whitelist.
        self.assertEqual(gateway.sanitize_snapshot(snapshot), snapshot)
        summary = gateway.public_summary(snapshot)
        self.assertEqual(summary["calls"], 12)
        self.assertEqual(summary["overview"]["summary"]["calls"], 40)
        self.assertEqual(summary["plugin_states"], {"degraded": 1})
        self.assertEqual(summary["cooling_deployments"], 1)
        markdown = gateway._markdown(snapshot, collected_at="2026-11-10 18:00:00")
        self.assertIn("最近 7 天", markdown)
        self.assertIn("3.5 CNY", markdown)
        self.assertIn("单日辅助统计", markdown)
        gateway._render_chart(snapshot, self.module / "current.png")
        with Image.open(self.module / "current.png") as image:
            self.assertEqual(image.size, (1600, 900))

    def test_period_override_preserves_saved_default_and_invalid_period_never_connects(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps(current_status_payload()).encode()

        with patch.object(gateway, "_open_status_request", return_value=Response()) as request:
            gateway.activate({"base_url": "http://example.test", "status_token": "secret", "period": "7d"})
            self.assertEqual(gateway.configuration_status()["period"], "7d")
            result = gateway.update_snapshot(period="30d", target_date="2026-11-01")
            self.assertTrue(result["ok"])
            self.assertIn("period=30d", request.call_args.args[0].full_url)
            self.assertIn("date=2026-11-01", request.call_args.args[0].full_url)
            config = gateway.load_config()
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.period, "7d")
            before = request.call_count
            failed = gateway.update_snapshot(period="90d")
            self.assertFalse(failed["ok"])
            self.assertEqual(request.call_count, before)
        old_config = gateway.config_from_mapping({"base_url": "http://example.test", "status_token": "secret"})
        self.assertEqual(old_config.period, "today")

    def test_rolling_window_clock_changes_do_not_rewrite_artifacts_but_metrics_do(self):
        first = gateway.sanitize_snapshot(current_status_payload())
        clock = copy.deepcopy(first)
        clock["generated_at"] = "2026-11-10T10:00:05+00:00"
        clock["statistics"]["overview"].update(
            start="2026-11-03T10:00:05+00:00", end="2026-11-10T10:00:05+00:00",
        )
        changed = copy.deepcopy(clock)
        changed["statistics"]["overview"]["summary"]["calls"] = 41
        gateway._atomic_json(gateway.CONFIG_PATH, {"base_url": "http://example.test", "status_token": "secret"})
        with patch.object(gateway, "fetch_status", side_effect=[first, clock, changed]):
            self.assertTrue(gateway.update_snapshot()["changed"])
            before = gateway.CHART_PATH.stat().st_mtime_ns
            self.assertFalse(gateway.update_snapshot()["changed"])
            self.assertEqual(gateway.CHART_PATH.stat().st_mtime_ns, before)
            self.assertTrue(gateway.update_snapshot()["changed"])
        self.assertEqual(json.loads(gateway.DATA_PATH.read_text("utf-8"))["statistics"]["overview"]["summary"]["calls"], 41)

    def test_unknown_cost_and_resource_failures_remain_explicit(self):
        payload = current_status_payload()
        payload["statistics"]["overview"]["summary"].update(
            total_tokens=None, display_cost={"amount": None, "currency": "CNY", "approximate": True},
        )
        payload["resources"].update(installed=None, restart_required=None,
                                    error_code="RESOURCE_INVENTORY_UNAVAILABLE", update=None)
        snapshot = gateway.sanitize_snapshot(payload)
        self.assertIsNone(snapshot["resources"]["installed"])
        self.assertIsNone(snapshot["resources"]["update"])
        self.assertIsNone(snapshot["statistics"]["overview"]["summary"]["display_cost"]["amount"])
        markdown = gateway._markdown(snapshot, collected_at="2026-11-10")
        self.assertIn("未知 CNY", markdown)
        self.assertIn("RESOURCE_INVENTORY_UNAVAILABLE", markdown)

    def test_status_token_rejects_control_characters_and_http_redirects(self) -> None:
        with self.assertRaises(gateway.GatewayStatusError):
            gateway.config_from_mapping({
                "base_url": "http://127.0.0.1:7531",
                "status_token": "secret\r\nForwarded: value",
            })
        handler = gateway._RejectRedirects()  # noqa: SLF001 - security boundary contract
        self.assertIsNone(
            handler.redirect_request(urllib.request.Request("https://example.test"), io.BytesIO(), 302, "Found", HTTPMessage(), "https://example.test/status")
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
        manifest = yaml.safe_load((self.module / "module.yaml").read_text("utf-8"))
        self.assertFalse(manifest["input_open"])

    def test_unchanged_snapshot_does_not_rewrite_prompt_or_artifacts(self) -> None:
        gateway._atomic_json(  # noqa: SLF001 - module-local fixture
            self.module / "gateway_config.json",
            {
                "base_url": "http://127.0.0.1:7531",
                "status_token": "secret-token",
                "timeout_seconds": 15,
                "ranking_limit": 20,
                "log_limit": 20,
            },
        )
        first = gateway.sanitize_snapshot(status_payload())
        second_payload = status_payload()
        second_payload["generated_at"] = "2026-07-28T12:00:05+00:00"
        second = gateway.sanitize_snapshot(second_payload)
        with patch.object(gateway, "fetch_status", side_effect=[first, second, second]):
            self.assertTrue(gateway.update_snapshot()["changed"])
            paths = (
                self.module / "data" / "gateway_status.json",
                self.module / "artifacts" / "gateway_status.png",
                self.module / "input_data.md",
            )
            before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in paths}
            self.assertFalse(gateway.update_snapshot()["changed"])
            self.assertFalse(gateway.update_snapshot()["changed"])
            after = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in paths}
        self.assertEqual(after, before)

    def test_manifest_recent_update_never_moves_backwards(self) -> None:
        gateway._atomic_json(  # noqa: SLF001 - module-local fixture
            self.module / "module.yaml",
            {'input_open': True, 'health': '正常', 'recent_update': '2026-08-16 12:03:00', 'kind': 'expand'},
        )
        gateway._update_manifest(  # noqa: SLF001 - module-local fixture
            active=True,
            healthy=True,
            update_time="2026-08-16 12:00:00",
        )
        manifest = yaml.safe_load((self.module / "module.yaml").read_text("utf-8"))
        self.assertEqual(manifest["recent_update"], "2026-08-16 12:03:00")


if __name__ == "__main__":
    unittest.main()
