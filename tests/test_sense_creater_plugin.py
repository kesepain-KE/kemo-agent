from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.manifest import parse_plugin_manifest
from plugins.sense_creater.tool import run
from run.prompt_sources import load_prompt_source_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTER_SOURCE = (
    "from pathlib import Path\n\n"
    "def register(registry):\n"
    "    registry.add_perception(Path(__file__).resolve().parent)\n"
)


class SenseCreaterPluginTests(unittest.TestCase):
    @staticmethod
    def context(root: Path) -> dict[str, str]:
        return {"root": str(root), "user": "alice", "source": "test"}

    @staticmethod
    def prepare_root(root: Path) -> None:
        (root / "users" / "alice").mkdir(parents=True)
        sense = root / "global_sense"
        sense.mkdir()
        (sense / "register.py").write_text(REGISTER_SOURCE, "utf-8")

    def test_create_default_module_runs_and_matches_prompt_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            context = self.context(root)
            sense_content = (
                "# 系统资源感知\n\n"
                "> 最后更新: 待采集\n"
                "> 状态: 正常\n\n"
                "## CPU\n- 使用率: 待采集\n"
            )
            created = run(
                "create",
                name="system_monitor",
                explain="只读采集系统资源并注入提示词。",
                sense_content=sense_content,
                context=context,
            )
            self.assertTrue(created["valid"])
            self.assertEqual(created["path"], "global_sense/system_monitor")
            self.assertEqual(
                created["files"],
                ["sense.json", "sense.md", "data_update.py"],
            )
            module = root / created["path"]
            self.assertTrue(all((module / name).is_file() for name in created["files"]))
            manifest = json.loads((module / "sense.json").read_text("utf-8"))
            self.assertEqual(
                set(manifest),
                {"name", "data_md", "recent_update", "health", "start_update"},
            )
            self.assertEqual(manifest["name"], "system_monitor")
            self.assertEqual(manifest["data_md"], "sense.md")
            self.assertEqual(manifest["start_update"], "data_update.py")
            self.assertNotIn("explain", manifest)
            self.assertEqual((module / "sense.md").read_text("utf-8"), sense_content)

            validation = run("validate", name="system_monitor", context=context)
            self.assertTrue(validation["valid"], validation["errors"])
            listing = run("list", context=context)
            self.assertEqual(len(listing["modules"]), 1)
            self.assertEqual(listing["modules"][0]["name"], "system_monitor")
            self.assertTrue(listing["modules"][0]["valid"])

            selection = load_prompt_source_registry(root, "alice").select_perception(max_chars=10_000)
            self.assertIn("[system_monitor]", selection.text)
            self.assertIn("系统资源感知", selection.text)

            process = subprocess.run(
                [sys.executable, str(module / "data_update.py")],
                cwd=module,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)["status"], "ok")
            self.assertIn("暂无数据", (module / "sense.md").read_text("utf-8"))
            self.assertTrue(run("validate", name="system_monitor", context=context)["valid"])

            with self.assertRaises(FileExistsError):
                run(
                    "create",
                    name="system_monitor",
                    explain="不能覆盖已有模块。",
                    sense_content="# duplicate",
                    context=context,
                )

    def test_custom_update_code_allows_environment_credentials_and_rejects_literals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            context = self.context(root)
            environment_code = (
                "import os\n"
                "api_key = os.environ.get('SENSE_API_KEY', '')\n"
                "def collect_data():\n"
                "    return {'configured': bool(api_key)}\n"
            )
            created = run(
                "create",
                name="environment_sensor",
                explain="验证环境变量凭据读取。",
                sense_content="# 环境变量感知\n\n- configured: 待采集",
                data_update=environment_code,
                context=context,
            )
            self.assertTrue(run("validate", name=created["name"], context=context)["valid"])

            with self.assertRaisesRegex(ValueError, "硬编码敏感凭据"):
                run(
                    "create",
                    name="hardcoded_sensor",
                    explain="不应创建。",
                    sense_content="# 测试",
                    data_update="api_key = 'abcd123456'\ndef collect_data():\n    return {}\n",
                    context=context,
                )
            self.assertFalse((root / "global_sense" / "hardcoded_sensor").exists())

    def test_invalid_inputs_and_post_publish_failure_leave_no_partial_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            context = self.context(root)
            with self.assertRaisesRegex(ValueError, "感知模块名无效"):
                run(
                    "create",
                    name="../escape",
                    explain="测试",
                    sense_content="# test",
                    context=context,
                )
            with self.assertRaisesRegex(ValueError, "非空 explain"):
                run("create", name="missing_explain", sense_content="# test", context=context)
            with self.assertRaisesRegex(ValueError, "非空 sense_content"):
                run("create", name="missing_content", explain="测试", context=context)
            with self.assertRaisesRegex(ValueError, "Python 代码无效"):
                run(
                    "create",
                    name="broken_python",
                    explain="测试",
                    sense_content="# test",
                    data_update="def broken(:\n",
                    context=context,
                )
            self.assertFalse((root / "global_sense" / "broken_python").exists())

            invalid_result = {
                "valid": False,
                "errors": ["模拟发布后运行时校验失败"],
            }
            with patch("plugins.sense_creater.tool._run_validate", return_value=invalid_result):
                with self.assertRaisesRegex(ValueError, "发布后运行时校验失败"):
                    run(
                        "create",
                        name="rollback_sensor",
                        explain="测试回滚",
                        sense_content="# rollback",
                        context=context,
                    )
            self.assertFalse((root / "global_sense" / "rollback_sensor").exists())
            self.assertEqual(
                [path.name for path in (root / "global_sense").iterdir() if path.name.startswith(".")],
                [],
            )

    def test_validate_reports_manifest_files_time_health_and_python_damage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            context = self.context(root)
            base = root / "global_sense"

            missing = base / "missing_manifest"
            missing.mkdir()

            damaged = base / "damaged"
            damaged.mkdir()
            (damaged / "sense.md").write_text("# damaged\n", "utf-8")
            (damaged / "data_update.py").write_text("def broken(:\n", "utf-8")
            (damaged / "sense.json").write_text(
                json.dumps(
                    {
                        "name": "damaged",
                        "data_md": "sense.md",
                        "recent_update": "not-a-time",
                        "health": "未知",
                        "start_update": "data_update.py",
                        "extra": True,
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )

            traversal = base / "traversal"
            traversal.mkdir()
            (traversal / "data_update.py").write_text("pass\n", "utf-8")
            (traversal / "sense.json").write_text(
                json.dumps(
                    {
                        "name": "traversal",
                        "data_md": "../outside.md",
                        "recent_update": "2026-07-21 12:00:00",
                        "health": "正常",
                        "start_update": "data_update.py",
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )

            missing_result = run("validate", name="missing_manifest", context=context)
            self.assertFalse(missing_result["valid"])
            self.assertIn("sense.json 缺失", missing_result["errors"])
            damaged_result = run("validate", name="damaged", context=context)
            self.assertFalse(damaged_result["valid"])
            self.assertTrue(any("未知字段" in error for error in damaged_result["errors"]))
            self.assertTrue(any("recent_update" in error for error in damaged_result["errors"]))
            self.assertTrue(any("health" in error for error in damaged_result["errors"]))
            self.assertTrue(any("Python 代码无效" in error for error in damaged_result["errors"]))
            traversal_result = run("validate", name="traversal", context=context)
            self.assertFalse(traversal_result["valid"])
            self.assertTrue(any("模块目录内" in error for error in traversal_result["errors"]))

            listing = {item["name"]: item for item in run("list", context=context)["modules"]}
            self.assertEqual(set(listing), {"damaged", "missing_manifest", "traversal"})
            self.assertTrue(all(not item["valid"] for item in listing.values()))

    def test_context_symlink_and_manifest_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "缺少 root"):
                run("list", context={})
            with self.assertRaisesRegex(ValueError, "未知 sense_creater action"):
                run("unknown", context={"root": str(root)})

            if hasattr(os, "symlink"):
                outside = root / "outside"
                outside.mkdir()
                try:
                    os.symlink(outside, root / "global_sense", target_is_directory=True)
                except OSError:
                    pass
                else:
                    with self.assertRaisesRegex(ValueError, "符号链接|目录联接"):
                        run("list", context=self.context(root))

        manifest = parse_plugin_manifest(
            PROJECT_ROOT / "plugins" / "sense_creater" / "SKILL.md",
            root=PROJECT_ROOT,
        )
        self.assertEqual(manifest.tool["version"], "1.0.0")
        self.assertEqual(
            set(manifest.tool["input_schema"]["properties"]["action"]["enum"]),
            {"list", "create", "validate"},
        )
        self.assertNotIn("scope", manifest.tool["input_schema"]["properties"])
        self.assertIn(
            "创建感知模块四步流程",
            (PROJECT_ROOT / "plugins" / "sense_creater" / "SKILL.md").read_text("utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
