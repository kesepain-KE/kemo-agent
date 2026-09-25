from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from web.errors import InvalidRequestError
from web.services.module_panels import (
    load_module_panel,
    module_panel_response,
    save_module_panel_values,
    validate_expand_panel_action,
)


class ModulePanelTests(unittest.TestCase):
    def make_module(self, panel: dict[str, object]) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "demo"
        (root / "module").mkdir(parents=True)
        (root / "module" / "panel.json").write_text(
            json.dumps(panel, ensure_ascii=False),
            encoding="utf-8",
        )
        return temporary, root

    @staticmethod
    def full_panel() -> dict[str, object]:
        return {
            "schema_version": 1,
            "title": "演示面板",
            "containers": [
                {
                    "kind": "status",
                    "title": "状态",
                    "source": "module/status.json",
                    "fields": [
                        {"key": "health", "label": "健康", "type": "badge"},
                        {"key": "secret", "label": "状态密钥", "type": "text", "masked": True},
                    ],
                },
                {
                    "kind": "config",
                    "title": "配置",
                    "values": "module/panel.values.json",
                    "fields": [
                        {"key": "api_key", "label": "API Key", "type": "string", "masked": True},
                        {"key": "mode", "label": "模式", "type": "enum", "options": ["safe", "fast"]},
                        {"key": "limit", "label": "上限", "type": "number", "min": 1, "max": 10},
                        {"key": "enabled", "label": "启用", "type": "boolean"},
                    ],
                    "presets": [{"name": "快速", "values": {"mode": "fast", "limit": 8}}],
                },
                {
                    "kind": "action",
                    "title": "操作",
                    "controls": [
                        {"type": "button", "label": "刷新", "command": "refresh"},
                        {
                            "type": "send",
                            "label": "探测",
                            "command": "probe",
                            "inputs": [
                                {"key": "target", "label": "目标", "type": "string", "required": True},
                            ],
                        },
                    ],
                },
            ],
        }

    def test_response_only_returns_declared_fields_and_never_returns_masked_plaintext(self) -> None:
        _, root = self.make_module(self.full_panel())
        (root / "module" / "status.json").write_text(
            json.dumps({"health": "healthy", "secret": "status-plaintext", "ignored": "no"}),
            encoding="utf-8",
        )
        (root / "module" / "panel.values.json").write_text(
            json.dumps({"api_key": "config-plaintext", "mode": "fast", "limit": 5, "enabled": True, "ignored": "no"}),
            encoding="utf-8",
        )

        response = module_panel_response(root, "expand")
        serialized = json.dumps(response, ensure_ascii=False)

        self.assertNotIn("status-plaintext", serialized)
        self.assertNotIn("config-plaintext", serialized)
        self.assertNotIn('"ignored"', serialized)
        status, config, _ = response["panel"]["containers"]
        self.assertEqual(status["data"], {"health": "healthy"})
        self.assertEqual(status["secret_set"], {"secret": True})
        self.assertEqual(config["current_values"], {"mode": "fast", "limit": 5, "enabled": True})
        self.assertEqual(config["secret_set"], {"api_key": True})

    def test_save_validates_values_preserves_omitted_secrets_and_can_clear_them(self) -> None:
        _, root = self.make_module(self.full_panel())
        values_path = root / "module" / "panel.values.json"
        values_path.write_text(
            json.dumps({"api_key": "keep-me", "mode": "safe", "limit": 2, "enabled": False}),
            encoding="utf-8",
        )

        saved = save_module_panel_values(root, "expand", {"mode": "fast", "limit": 8, "enabled": True})
        stored = json.loads(values_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["api_key"], "keep-me")
        self.assertEqual(stored["mode"], "fast")
        self.assertTrue(saved["panel"])
        self.assertFalse(any(values_path.parent.glob(f".{values_path.name}.*.tmp")))

        save_module_panel_values(root, "expand", {}, ["api_key"])
        self.assertEqual(json.loads(values_path.read_text(encoding="utf-8"))["api_key"], "")

        with self.assertRaisesRegex(InvalidRequestError, "未声明字段"):
            save_module_panel_values(root, "expand", {"unknown": "x"})
        with self.assertRaisesRegex(InvalidRequestError, "枚举"):
            save_module_panel_values(root, "expand", {"mode": "invalid"})
        with self.assertRaisesRegex(InvalidRequestError, "不能大于"):
            save_module_panel_values(root, "expand", {"limit": 11})
        with self.assertRaisesRegex(InvalidRequestError, "非密钥字段"):
            save_module_panel_values(root, "expand", {}, ["mode"])
        with self.assertRaisesRegex(InvalidRequestError, "字符串数组"):
            save_module_panel_values(root, "expand", {}, {})

    def test_invalid_paths_and_sense_actions_are_local_panel_errors(self) -> None:
        panel = self.full_panel()
        panel["containers"][1]["values"] = "../../outside.json"
        _, root = self.make_module(panel)
        parsed, error = load_module_panel(root, "expand")
        self.assertIsNone(parsed)
        self.assertIn("相对路径", error)

        sense_panel = self.full_panel()
        sense_panel["containers"] = [{
            "kind": "action",
            "title": "操作",
            "controls": [{"type": "button", "label": "探测", "command": "probe"}],
        }]
        _, sense_root = self.make_module(sense_panel)
        parsed, error = load_module_panel(sense_root, "sense")
        self.assertIsNone(parsed)
        self.assertIn("只允许 refresh", error)

        masked_number = self.full_panel()
        masked_number["containers"][1]["fields"][2]["masked"] = True
        _, masked_root = self.make_module(masked_number)
        parsed, error = load_module_panel(masked_root, "expand")
        self.assertIsNone(parsed)
        self.assertIn("string 或 text", error)

    def test_symbolic_link_values_file_is_rejected_when_supported(self) -> None:
        _, root = self.make_module(self.full_panel())
        outside = root.parent / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        link = root / "module" / "panel.values.json"
        try:
            os.symlink(outside, link)
        except OSError as exc:
            self.skipTest(f"当前环境不能创建符号链接：{exc}")
        parsed, error = load_module_panel(root, "expand")
        self.assertIsNone(parsed)
        self.assertIn("符号链接", error)

    def test_expand_action_accepts_only_declared_command_and_inputs(self) -> None:
        _, root = self.make_module(self.full_panel())
        panel, error = load_module_panel(root, "expand")
        self.assertEqual(error, "")
        assert panel is not None
        command, params = validate_expand_panel_action(panel, "probe", {"target": "localhost"})
        self.assertEqual((command, params), ("probe", {"target": "localhost"}))
        with self.assertRaisesRegex(InvalidRequestError, "未在面板"):
            validate_expand_panel_action(panel, "delete", {})
        with self.assertRaisesRegex(InvalidRequestError, "未声明字段"):
            validate_expand_panel_action(panel, "probe", {"target": "localhost", "extra": True})
        with self.assertRaisesRegex(InvalidRequestError, "缺少必填字段"):
            validate_expand_panel_action(panel, "probe", {})


if __name__ == "__main__":
    unittest.main()
