from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from plugins.manifest import parse_plugin_manifest
from plugins.skill_creater.tool import run


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SkillCreaterPluginTests(unittest.TestCase):
    @staticmethod
    def context(root: Path, user: str = "alice", agent: str = "") -> dict[str, str]:
        context = {"root": str(root), "user": user, "source": "test"}
        if agent:
            context["agent"] = agent
        return context

    @staticmethod
    def prepare_root(root: Path) -> None:
        (root / "users" / "alice").mkdir(parents=True)

    def test_instruction_create_list_get_validate_update_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            context = self.context(root)

            created = run(
                "create",
                "user_create",
                name="writing-guide",
                title="写作指南",
                description="提供统一的项目写作风格。",
                instruction="回答时先给结论，再补充必要依据。",
                context=context,
            )
            self.assertTrue(created["valid"])
            self.assertTrue(Path(created["path"]).is_dir())

            listing = run("list", "user_create", context=context)
            self.assertEqual(listing["skills"], [{"name": "writing-guide", "title": "写作指南"}])
            fetched = run("get", "user_create", name="writing-guide", context=context)
            self.assertIn("# 写作指南", fetched["content"])
            self.assertIn("回答时先给结论", fetched["content"])
            self.assertTrue(run("validate", "user_create", name="writing-guide", context=context)["valid"])

            updated = run(
                "update",
                "user_create",
                name="writing-guide",
                title="写作指南 v2",
                description="更新后的项目写作风格。",
                instruction="用简洁中文回答。",
                context=context,
            )
            self.assertTrue(updated["valid"])
            self.assertIn("# 写作指南 v2", run("get", "user_create", name="writing-guide", context=context)["content"])
            self.assertTrue(run("delete", "user_create", name="writing-guide", context=context)["deleted"])
            self.assertFalse(run("delete", "user_create", name="writing-guide", context=context)["deleted"])

    def test_all_scopes_and_tool_schema_documentation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            context = self.context(root)
            schema = {
                "name": "documented_action",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            }
            expected_bases = {
                "agent_create": root / "users" / "alice" / "user_skills" / "agent_create",
                "user_create": root / "users" / "alice" / "user_skills" / "user_create",
                "shared": root / "shared_skills",
            }
            for scope, base in expected_bases.items():
                result = run(
                    "create",
                    scope,
                    name=f"{scope}-skill",
                    title=f"{scope} skill",
                    description="记录结构化 Tool 信息，但不注册可执行工具。",
                    tool_schema=schema,
                    context=context,
                )
                self.assertEqual(Path(result["path"]), base / f"{scope}-skill")
                content = run("get", scope, name=f"{scope}-skill", context=context)["content"]
                parsed = json.loads(content.split("```json\n", 1)[1].split("\n```", 1)[0])
                self.assertEqual(parsed, schema)
                self.assertTrue(run("validate", scope, name=f"{scope}-skill", context=context)["valid"])
                self.assertEqual(len(run("list", scope, context=context)["skills"]), 1)

    def test_full_content_mode_and_invalid_create_are_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            context = self.context(root)
            content = "# 全文技能\n\n全文模式优先于结构化参数。\n\n执行约定。\n"
            run(
                "create",
                "agent_create",
                name="full-content",
                content=content,
                title="会被忽略",
                description="会被忽略",
                instruction="会被忽略",
                context=context,
            )
            fetched = run("get", "agent_create", name="full-content", context=context)["content"]
            self.assertEqual(fetched, content)
            self.assertNotIn("会被忽略", fetched)

            invalid_dir = root / "users" / "alice" / "user_skills" / "agent_create" / "invalid"
            with self.assertRaisesRegex(ValueError, "创建后的技能未通过校验"):
                run(
                    "create",
                    "agent_create",
                    name="invalid",
                    content="没有一级标题",
                    context=context,
                )
            self.assertFalse(invalid_dir.exists())

    def test_invalid_update_restores_previous_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            context = self.context(root)
            original = "# stable\n\n原始内容。\n"
            run("create", "user_create", name="stable", content=original, context=context)
            with self.assertRaisesRegex(ValueError, "更新后的技能未通过校验"):
                run(
                    "update",
                    "user_create",
                    name="stable",
                    content="# stable\n\n## Tool\n\n```json\n[]\n```",
                    context=context,
                )
            restored = run("get", "user_create", name="stable", context=context)["content"]
            self.assertEqual(restored, original)

    def test_validate_reports_title_and_tool_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            context = self.context(root)
            base = root / "users" / "alice" / "user_skills" / "user_create"
            cases = {
                "missing-title": ("正文\n", "一级标题"),
                "second-title": ("# one\n\n# two\n", "只能包含一个一级标题"),
                "missing-json": ("# tool\n\n## Tool\n\n正文\n", "缺少 JSON"),
                "invalid-json": ("# tool\n\n## Tool\n\n```json\n{bad}\n```\n", "JSON 无效"),
                "array-json": ("# tool\n\n## Tool\n\n```json\n[]\n```\n", "必须是对象"),
            }
            for name, (content, expected) in cases.items():
                skill_dir = base / name
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text(content, "utf-8")
                result = run("validate", "user_create", name=name, context=context)
                self.assertFalse(result["valid"])
                self.assertTrue(any(expected in error for error in result["errors"]), result["errors"])

    def test_argument_permissions_context_and_path_safety(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            context = self.context(root)
            with self.assertRaisesRegex(ValueError, "技能名称无效"):
                run("get", "user_create", name="../escape", context=context)
            with self.assertRaisesRegex(ValueError, "非法用户名称"):
                run("list", "user_create", context=self.context(root, "../escape"))
            with self.assertRaisesRegex(ValueError, "instruction 与 tool_schema"):
                run(
                    "create",
                    "user_create",
                    name="ambiguous",
                    title="ambiguous",
                    description="不能同时提供两类正文。",
                    instruction="instruction",
                    tool_schema={},
                    context=context,
                )
            for scope in ("user_create", "shared"):
                with self.assertRaises(PermissionError):
                    run("list", scope, context=self.context(root, agent="self_improve"))

            if hasattr(os, "symlink"):
                outside = root / "outside"
                outside.mkdir()
                link = root / "users" / "alice" / "user_skills" / "user_create"
                link.parent.mkdir(parents=True)
                try:
                    os.symlink(outside, link, target_is_directory=True)
                except OSError:
                    pass
                else:
                    with self.assertRaisesRegex(ValueError, "符号链接|目录联接"):
                        run("list", "user_create", context=context)

    def test_manifest_exposes_six_actions_and_version_1_1_0(self) -> None:
        manifest = parse_plugin_manifest(
            PROJECT_ROOT / "plugins" / "skill_creater" / "SKILL.md",
            root=PROJECT_ROOT,
        )
        self.assertEqual(manifest.tool["version"], "1.1.0")
        self.assertEqual(
            set(manifest.tool["input_schema"]["properties"]["action"]["enum"]),
            {"list", "get", "validate", "create", "update", "delete"},
        )
        self.assertEqual(manifest.tool["input_schema"]["required"], ["action", "scope"])


if __name__ == "__main__":
    unittest.main()
