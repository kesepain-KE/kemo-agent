from __future__ import annotations

from tests.type_assertions import require_present

import yaml
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from plugins.expand_creater.tool import run
from run.config import load_prompt_source_registry
from run.modules import read_manifest, ModuleSpecError
from web.services.module_panels import load_module_panel


class ExpandCreaterPluginTests(unittest.TestCase):
    @staticmethod
    def context(root: Path, user: str = "alice") -> dict[str, str]:
        return {"root": str(root), "user": user, "source": "test"}

    def test_create_user_module_templates_are_runnable_and_runtime_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            context = self.context(root)
            created = run(
                "create",
                "user",
                name="smart_lights",
                explain="连接并控制家庭智能灯光。",
                injection="智能灯光拓展可用；用户要求控制灯光时读取操作层。",
                operations="### switch\n传入 `action=switch` 和目标区域，返回结构化 JSON。",
                context=context,
            )
            self.assertTrue(created["valid"])
            self.assertEqual(created["path"], "users/alice/expand/smart_lights")
            self.assertEqual(
                set(created["files"]),
                {
                    "module.yaml", "expand_control.md", "start_expand.py", "data_update.py", "input_data.md",
                    "module/panel.json", "module/panel.values.json", "module/status.json", "module/README.md",
                },
            )

            module = root / created["path"]
            self.assertTrue(all((module / name).is_file() for name in created["files"]))
            manifest = yaml.safe_load((module / "module.yaml").read_text("utf-8"))
            self.assertEqual(
                set(manifest),
                {
                    "kind", "name", "explain", "input_open", "input_file", "health",
                    "update_script", "control_open", "control_script", "control_entry",
                },
            )
            self.assertEqual(manifest["health"], "异常")
            self.assertNotIn("recent_update", manifest)
            self.assertTrue(read_manifest(module, kind_hint="expand", scope="user").kind == "expand")
            validation = run("validate", "user", name="smart_lights", context=context)
            self.assertTrue(validation["valid"], validation["errors"])
            self.assertTrue(validation["panel_enabled"])
            panel, panel_error = load_module_panel(module, "expand")
            self.assertEqual(panel_error, "")
            self.assertEqual(require_present(panel)["title"], "smart_lights · 用户配置")
            listing = run("list", "user", context=context)
            self.assertEqual(listing["count"], 1)
            self.assertEqual(listing["modules"][0]["name"], "smart_lights")
            self.assertTrue(listing["modules"][0]["valid"])
            self.assertTrue(listing["modules"][0]["panel_enabled"])
            prompt_selection = load_prompt_source_registry(root, "alice").select_expand(
                max_chars=10_000
            )
            self.assertIn("智能灯光拓展可用", prompt_selection.text)
            self.assertIn("智能灯光拓展可用", prompt_selection.fragment("user:smart_lights"))

            control = (module / "expand_control.md").read_text("utf-8")
            self.assertLess(control.index("## 注入层"), control.index("## 操作层"))
            self.assertIn("智能灯光拓展可用", control)
            self.assertIn("action=switch", control)

            control_process = subprocess.run(
                [sys.executable, str(module / "start_expand.py"), '{"action":"test"}'],
                cwd=module,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=False,
            )
            self.assertEqual(control_process.returncode, 1, control_process.stderr)
            control_result = json.loads(control_process.stdout)
            self.assertFalse(control_result["ok"])
            self.assertIn("未知命令", control_result["error"])

            stdin_process = subprocess.run(
                [sys.executable, str(module / "start_expand.py")],
                cwd=module,
                input=json.dumps(
                    {"command": "example_action", "params": {"param": "中文与引号\""}},
                    ensure_ascii=False,
                ),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=False,
            )
            self.assertEqual(stdin_process.returncode, 1, stdin_process.stderr)
            stdin_result = json.loads(stdin_process.stdout)
            self.assertEqual(stdin_result["data"]["param"], '中文与引号"')

            update_process = subprocess.run(
                [sys.executable, str(module / "data_update.py")],
                cwd=module,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=False,
            )
            self.assertEqual(update_process.returncode, 0, update_process.stderr)
            self.assertTrue(json.loads(update_process.stdout)["ok"])
            prompt_data = (module / "input_data.md").read_text("utf-8")
            self.assertIn("## 数据", prompt_data)
            self.assertNotIn("自动采集时间", prompt_data)
            before = (module / "input_data.md").stat().st_mtime_ns
            repeated = subprocess.run(
                [sys.executable, str(module / "data_update.py")],
                cwd=module,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=False,
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertFalse(json.loads(repeated.stdout)["changed"])
            self.assertEqual((module / "input_data.md").stat().st_mtime_ns, before)
            panel_status = json.loads((module / "module" / "status.json").read_text("utf-8"))
            self.assertEqual(panel_status["phase"], "已完成")
            self.assertTrue(panel_status["last_run"])

            refresh_process = subprocess.run(
                [sys.executable, str(module / "start_expand.py")],
                cwd=module,
                input=json.dumps({"command": "refresh", "params": {}}, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=False,
            )
            self.assertEqual(refresh_process.returncode, 0, refresh_process.stderr)
            self.assertTrue(json.loads(refresh_process.stdout)["ok"])

            panel_source = json.loads((module / "module" / "panel.json").read_text("utf-8"))
            panel_source["title"] = "热加载后的面板"
            (module / "module" / "panel.json").write_text(
                json.dumps(panel_source, ensure_ascii=False),
                "utf-8",
            )
            reloaded, panel_error = load_module_panel(module, "expand")
            self.assertEqual(panel_error, "")
            self.assertEqual(require_present(reloaded)["title"], "热加载后的面板")
            self.assertTrue(run("validate", "user", name="smart_lights", context=context)["valid"])

            with self.assertRaises(FileExistsError):
                run(
                    "create",
                    "user",
                    name="smart_lights",
                    explain="不能覆盖",
                    injection="不能覆盖",
                    operations="不能覆盖",
                    context=context,
                )

    def test_shared_module_custom_code_validation_and_optional_timestamp_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            created = run(
                "create",
                "shared",
                name="weather_station",
                explain="读取共享气象站并提供外部操作。",
                injection="共享气象站可用。",
                operations="### status\n读取温度、湿度和设备状态。",
                open_input=False,
                start_expand="def execute(command):\n    return {'status': 'ok'}\n",
                data_update="def collect():\n    return {}\n",
                context=context,
            )
            module = root / created["path"]
            self.assertTrue(run("validate", "shared", name="weather_station", context=context)["valid"])
            panel, panel_error = load_module_panel(module, "expand")
            self.assertEqual(panel_error, "")
            self.assertFalse(any(container["kind"] == "action" for container in require_present(panel)["containers"]))

            manifest_path = module / "module.yaml"
            manifest = yaml.safe_load(manifest_path.read_text("utf-8"))
            self.assertNotIn("recent_update", manifest)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
            self.assertTrue(read_manifest(module, kind_hint="expand", scope="user").kind == "expand")
            self.assertTrue(run("validate", "shared", name="weather_station", context=context)["valid"])

            (module / "input_data.md").unlink()
            self.assertTrue(run("validate", "shared", name="weather_station", context=context)["valid"])
            manifest["input_open"] = True
            manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True), "utf-8")
            invalid = run("validate", "shared", name="weather_station", context=context)
            self.assertFalse(invalid["valid"])
            self.assertTrue(any("file_missing:input_file:" in error for error in invalid["errors"]))

    def test_invalid_inputs_do_not_leave_partial_modules_and_list_reports_damage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            context = self.context(root)
            with self.assertRaisesRegex(ValueError, "拓展名无效"):
                run("validate", "user", name="../escape", context=context)
            with self.assertRaisesRegex(ValueError, "非法用户名称"):
                run("list", "user", context=self.context(root, "../escape"))
            with self.assertRaisesRegex(ValueError, "重复包含"):
                run(
                    "create",
                    "user",
                    name="bad_heading",
                    explain="测试",
                    injection="## 注入层\n重复标题",
                    operations="操作",
                    context=context,
                )
            with self.assertRaisesRegex(ValueError, "Python 代码无效"):
                run(
                    "create",
                    "user",
                    name="bad_python",
                    explain="测试",
                    injection="可用",
                    operations="操作",
                    start_expand="def broken(:\n",
                    context=context,
                )
            self.assertFalse((root / "users" / "alice" / "expand" / "bad_python").exists())
            with self.assertRaisesRegex(ValueError, "疑似敏感凭据"):
                run(
                    "create",
                    "user",
                    name="secret_module",
                    explain="api_key=abcd1234567890",
                    injection="可用",
                    operations="操作",
                    context=context,
                )
            environment_code = (
                "import os\n"
                "api_key = os.environ.get('EXPAND_API_KEY', '')\n"
                "def execute(command):\n"
                "    return {'status': 'ok', 'configured': bool(api_key)}\n"
            )
            created = run(
                "create",
                "user",
                name="environment_secret",
                explain="从环境变量读取凭据。",
                injection="安全测试模块可用。",
                operations="调用测试操作。",
                start_expand=environment_code,
                context=context,
            )
            self.assertTrue(created["valid"])
            with self.assertRaisesRegex(ValueError, "硬编码敏感凭据"):
                run(
                    "create",
                    "user",
                    name="hardcoded_secret",
                    explain="测试硬编码拦截。",
                    injection="安全测试模块可用。",
                    operations="调用测试操作。",
                    start_expand="api_key = 'abcd123456'\ndef execute(command):\n    return {}\n",
                    context=context,
                )

            broken = root / "users" / "alice" / "expand" / "broken_module"
            broken.mkdir(parents=True)
            listing = run("list", "user", context=context)
            self.assertEqual(listing["count"], 2)
            broken_result = next(item for item in listing["modules"] if item["name"] == "broken_module")
            self.assertFalse(broken_result["valid"])
            self.assertIn("missing_manifest:-:module.yaml 缺失", broken_result["errors"])

    def test_runtime_accepts_valid_optional_timestamp_and_rejects_bad_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            context = self.context(root)
            created = run(
                "create",
                "user",
                name="timestamped",
                explain="测试更新时间兼容。",
                injection="模块可用。",
                operations="无外部副作用的测试操作。",
                context=context,
            )
            manifest_path = root / created["path"] / "module.yaml"
            manifest = yaml.safe_load(manifest_path.read_text("utf-8"))
            manifest["recent_update"] = "not-a-time"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
            with self.assertRaises(ModuleSpecError) as failure:
                read_manifest(manifest_path.parent, kind_hint="expand", scope="user")
            self.assertEqual(failure.exception.error.reason, "bad_type")
            self.assertEqual(failure.exception.error.field, "recent_update")
            validation = run("validate", "user", name="timestamped", context=context)
            self.assertFalse(validation["valid"])
            self.assertTrue(any("recent_update" in error for error in validation["errors"]))


if __name__ == "__main__":
    unittest.main()
