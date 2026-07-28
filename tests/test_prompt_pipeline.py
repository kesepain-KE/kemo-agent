from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from events import RunEvent
from provider.adapters.compat import (
    chat_response_to_kemo,
    chat_stream_to_protocol,
    kemo_request_to_chat,
)
from provider.schema import ChatResponse, Usage
from run.config import load_config
from run.engine import EngineError, context_status, handle_request, iter_request_events
from run.kemo_graph import KemoGraphLayerStatus, KemoGraphPromptContext
from run.memory import MemoryStore
from run.prompt import (
    PROMPT_SECTION_ORDER,
    PromptConfigError,
    build_prompt_bundle,
    parse_prompt_settings,
)
from run.prompt_sources import (
    PromptRegistrationError,
    load_prompt_source_registry,
    natural_path_key,
)
from run.task_plan_store import select_prompt_plans
from run.tools import discover_tools


class CaptureProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("provider failed")
        return ChatResponse(text="ok", usage=Usage(1, 1, 2, source="mock"), model="mock")

    def chat_stream(self, request):
        raise AssertionError("streaming not expected")

    def create(self, request):
        return chat_response_to_kemo(self.chat(kemo_request_to_chat(request)), request)

    def stream(self, request):
        return chat_stream_to_protocol(
            self.chat_stream(kemo_request_to_chat(request)),
            request,
        )


class PromptPipelineTests(unittest.TestCase):
    def test_repository_instruction_contract_preserves_user_directed_path_rules(self) -> None:
        root = Path(__file__).resolve().parents[1]
        global_soul = (root / "config" / "global_soul.md").read_text("utf-8")
        agents_manual = (root / "agents.md").read_text("utf-8")

        self.assertIn("## 用户明确路径优先", global_soul)
        self.assertIn("必须按用户指定路径执行", global_soul)
        self.assertIn("未获授权时停在冲突步骤", global_soul)
        self.assertLess(global_soul.index("## 硬性底线"), global_soul.index("## 用户明确路径优先"))

        self.assertIn("### 用户指定执行路径", agents_manual)
        for requirement in (
            "指定执行顺序",
            "指定工具或资源",
            "指定作用范围与禁止事项",
            "指定暂停和授权节点",
            "不得提前执行后续步骤",
            "以最新的明确指令为准",
        ):
            self.assertIn(requirement, agents_manual)

    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path, dict]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for path in (
            root / "config",
            root / "plugins",
            root / "shared_skills",
            root / "shared_knowledge",
            root / "global_knowledge",
            root / "global_expand",
            root / "shared_expand",
            root / "global_sense",
            root / "users" / "alice" / "history",
            root / "users" / "alice" / "knowledge",
            root / "users" / "alice" / "user_skills",
            root / "users" / "alice" / "expand",
            root / "users" / "alice" / "task_plan",
        ):
            path.mkdir(parents=True, exist_ok=True)
        registrars = (
            (root / "global_expand" / "register.py", 'registry.add_expand_root("global", Path(__file__).resolve().parent)'),
            (root / "shared_expand" / "register.py", 'registry.add_expand_root("shared", Path(__file__).resolve().parent)'),
            (root / "users" / "alice" / "expand" / "register.py", "pass"),
            (root / "shared_skills" / "register.py", 'registry.add_skills("shared", Path(__file__).resolve().parent)'),
            (root / "users" / "alice" / "user_skills" / "register.py", 'registry.add_skills("user", Path(__file__).resolve().parent)'),
            (root / "global_sense" / "register.py", "registry.add_perception(Path(__file__).resolve().parent)"),
        )
        for path, statement in registrars:
            path.write_text(
                "from pathlib import Path\n\n"
                "def register(registry):\n"
                f"    {statement}\n",
                "utf-8",
            )
        config = {
            "provider": {
                "type": "kemo",
                "base_url": "http://127.0.0.1:1/v1",
                "api_key_env": "TEST_KEMO_KEY",
                "model": "mock",
                "stream": False,
            },
            "tools": {"enabled": True, "max_iterations": 2, "timeout": 2},
            "agents": {
                "conserved_rounds": 3,
                "max_rounds": 30,
                "rounds_after_compression": 10,
                "token_limit": 120000,
                "token_compression_ratio": 0.6,
            },
        }
        global_config = {key: value for key, value in config.items() if key != "provider"}
        (root / "config" / "global_config.json").write_text(
            json.dumps(global_config),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1, "provider": config["provider"]}),
            "utf-8",
        )
        for tier in ("seven_days", "one_month", "half_year", "permanent"):
            folder = root / "users" / "alice" / "improve" / tier
            folder.mkdir(parents=True)
            if tier != "permanent":
                (folder / "data.json").write_text(
                    json.dumps({"schema_version": 2, "files": {}}), "utf-8"
                )
        return temporary, root, config

    def write_plugin(self, root: Path, name: str = "clock") -> None:
        directory = root / "plugins" / name
        directory.mkdir(parents=True, exist_ok=True)
        tool = {
            "name": name,
            "description": f"{name} tool schema description",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "version": "1",
            "enabled": True,
            "entrypoint": "tool.py:run",
        }
        (directory / "SKILL.md").write_text(
            f"# {name}\n{name} trigger description\n\n## Tool\n\n```json\n"
            f"{json.dumps(tool)}\n```\n",
            "utf-8",
        )
        (directory / "tool.py").write_text("def run():\n    return {'ok': True}\n", "utf-8")

    def write_sense_module(
        self,
        root: Path,
        name: str,
        content: str,
        *,
        display_name: str | None = None,
        health: str = "正常",
        recent_update: str = "2026-07-19 12:00:00",
    ) -> Path:
        module = root / "global_sense" / name
        module.mkdir(parents=True, exist_ok=True)
        (module / "sense.md").write_text(content, "utf-8")
        (module / "sense.json").write_text(
            json.dumps(
                {
                    "name": display_name or name,
                    "data_md": "sense.md",
                    "recent_update": recent_update,
                    "health": health,
                    "start_update": "data_update.py",
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        return module

    def write_expand_module(
        self,
        root: Path,
        scope: str,
        name: str,
        *,
        input_text: str = "",
        control_injection: str = "",
        operation_text: str = "Detailed operation manual.",
        open_input: bool = True,
        open_control: bool = True,
        input_health: str = "正常",
        display_name: str | None = None,
        user: str = "alice",
    ) -> Path:
        base = {
            "global": root / "global_expand",
            "shared": root / "shared_expand",
            "user": root / "users" / user / "expand",
        }[scope]
        module = base / name
        module.mkdir(parents=True, exist_ok=True)
        (module / "input_data.md").write_text(input_text, "utf-8")
        (module / "expand_control.md").write_text(
            f"## 注入层\n\n{control_injection}\n\n## 操作层\n\n{operation_text}",
            "utf-8",
        )
        (module / "expand.json").write_text(
            json.dumps(
                {
                    "name": display_name or name,
                    "explain": f"{name} expand module",
                    "open_input": open_input,
                    "input_data": "input_data.md",
                    "input_health": input_health,
                    "start_update": "data_update.py",
                    "open_control": open_control,
                    "start_expand": "start_expand.py",
                    "start_control": "expand_control.md",
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        return module

    def write_memory(self, root: Path, tier: str, items: list[dict]) -> None:
        directory = root / "users" / "alice" / "improve" / tier
        directory.mkdir(parents=True, exist_ok=True)
        files = {}
        entered = datetime(2098, 1, 1, tzinfo=timezone.utc)
        days = {"seven_days": 7, "one_month": 30, "half_year": 180}
        for item in items:
            filename = str(item.get("filename") or "memory")
            if not filename.endswith(".md"):
                filename += ".md"
            (directory / filename).write_text(str(item.get("content") or ""), "utf-8")
            if tier != "permanent":
                files[filename] = {
                    "weight": int(item.get("weight", 0)),
                    "updated_at": entered.isoformat(),
                    "last_weight_date": item.get("last_weight_date"),
                    "expires_at": (entered + timedelta(days=days[tier])).isoformat(),
                }
        if tier != "permanent":
            (directory / "data.json").write_text(
                json.dumps({"schema_version": 2, "files": files}), "utf-8"
            )

    def populate_all_sections(self, root: Path) -> None:
        (root / "users" / "alice" / "user_soul.md").write_text("USER", "utf-8")
        (root / "config" / "global_soul.md").write_text("GLOBAL", "utf-8")
        (root / "agents.md").write_text("AGENTS", "utf-8")
        self.write_plugin(root)
        shared_skill = root / "shared_skills" / "shared"
        shared_skill.mkdir()
        (shared_skill / "SKILL.md").write_text("# shared\nshared trigger\n\n## Details\nhidden", "utf-8")
        (root / "users" / "alice" / "knowledge" / "index.md").write_text("INDEX", "utf-8")
        self.write_memory(root, "permanent", [{"id": "p", "content": "PERMANENT"}])
        self.write_memory(root, "seven_days", [{"id": "s", "content": "SEVEN"}])
        self.write_memory(root, "one_month", [{"id": "m", "content": "MONTH"}])
        self.write_memory(root, "half_year", [{"id": "h", "content": "HALF"}])
        (root / "users" / "alice" / "memory_temporary_important.md").write_text("IMPORTANT", "utf-8")
        plan = {
            "plan_id": "plan_00000001",
            "title": "Plan",
            "description": "Active plan",
            "status": "running",
            "steps": [{"description": "Do it", "status": "pending"}],
        }
        (root / "users" / "alice" / "task_plan" / "plan_00000001.json").write_text(
            json.dumps(plan), "utf-8"
        )
        self.write_expand_module(
            root,
            "global",
            "water",
            input_text="EXPAND",
            open_control=False,
        )
        self.write_sense_module(root, "runtime", "SENSE")

    def test_exact_section_order_empty_omission_and_perception_last(self) -> None:
        _, root, config = self.make_root()
        empty_bundle = build_prompt_bundle(root, "alice", config)
        self.assertEqual(
            tuple(section.name for section in empty_bundle.sections),
            PROMPT_SECTION_ORDER,
        )
        self.assertTrue(
            all(section.content == "（无）" for section in empty_bundle.sections)
        )
        self.populate_all_sections(root)
        bundle = build_prompt_bundle(root, "alice", config)
        self.assertEqual(
            tuple(section.name for section in bundle.sections),
            PROMPT_SECTION_ORDER,
        )
        self.assertEqual(bundle.sections[-1].name, "perception")
        self.assertEqual(bundle.text.count("[perception]"), 1)

    def test_subagent_registries_split_global_and_user_registration_only(self) -> None:
        _, root, config = self.make_root()
        directory = root / "agents" / "demo_agent"
        directory.mkdir(parents=True)
        (directory / "agent.json").write_text(
            json.dumps(
                {
                    "name": "demo_agent",
                    "version": "1.0.0",
                    "description": "demo",
                    "trigger": "trigger.md",
                }
            ),
            "utf-8",
        )
        (directory / "agent-config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "internal_mode": True,
                    "allowed_callers": ["main_agent"],
                    "tools": {
                        "plugins": {"allow": []},
                        "shared_skills": {"allow": []},
                        "max_iterations": 20,
                    },
                    "global_knowledge": False,
                    "shared_knowledge": False,
                    "inherit_main_history": False,
                }
            ),
            "utf-8",
        )
        (directory / "AGENT.md").write_text("# demo_agent\nHandle demo input.", "utf-8")
        (directory / "executor.py").write_text(
            "def execute(context, input_data):\n    return context.run_model(input_data)\n",
            "utf-8",
        )
        (directory / "trigger.md").write_text(
            "# 注册信息\n\n- **触发**: demo condition\n- **职责**: demo duty\n\n"
            "# 操作信息\n\nOPERATION_SECRET",
            "utf-8",
        )
        user_directory = root / "users" / "alice" / "agents" / "user_demo"
        user_directory.mkdir(parents=True)
        (user_directory / "agent.json").write_text(
            json.dumps(
                {
                    "name": "user_demo",
                    "version": "1.0.0",
                    "description": "user demo",
                    "trigger": "trigger.md",
                }
            ),
            "utf-8",
        )
        (user_directory / "agent-config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "internal_mode": True,
                    "allowed_callers": ["main_agent"],
                    "tools": {
                        "plugins": {"allow": []},
                        "shared_skills": {"allow": []},
                        "max_iterations": 20,
                    },
                    "global_knowledge": False,
                    "shared_knowledge": False,
                    "inherit_main_history": False,
                }
            ),
            "utf-8",
        )
        (user_directory / "AGENT.md").write_text(
            "# user_demo\nHandle user demo input.", "utf-8"
        )
        (user_directory / "trigger.md").write_text(
            "# 注册信息\n\n- **触发**: user demo condition\n"
            "- **职责**: user demo duty\n\n"
            "# 操作信息\n\nUSER_OPERATION_SECRET",
            "utf-8",
        )
        bundle = build_prompt_bundle(root, "alice", config)
        global_section = next(
            item
            for item in bundle.sections
            if item.name == "global_subagent_registry"
        )
        user_section = next(
            item for item in bundle.sections if item.name == "user_subagent_registry"
        )
        self.assertIn("demo condition", global_section.content)
        self.assertNotIn("### user_demo", global_section.content)
        self.assertNotIn("OPERATION_SECRET", global_section.content)
        self.assertEqual(global_section.item_ids, ("demo_agent",))
        self.assertIn("user demo condition", user_section.content)
        self.assertNotIn("### demo_agent", user_section.content)
        self.assertNotIn("USER_OPERATION_SECRET", user_section.content)
        self.assertEqual(user_section.item_ids, ("user_demo",))

    def test_kemo_graph_replaces_selected_knowledge_scope_only(self) -> None:
        _, root, config = self.make_root()
        self.populate_all_sections(root)
        (root / "shared_knowledge" / "index.md").write_text("SHARED_INDEX", "utf-8")
        (root / "global_knowledge" / "index.md").write_text("GLOBAL_INDEX", "utf-8")
        config["kemo_graph"] = {"kemo_graph_user_knowledge": True}

        def graph_loader(*_args, **kwargs):
            self.assertTrue(kwargs["replaces_knowledge"])
            self.assertFalse(kwargs["replaces_memory"])
            return KemoGraphPromptContext(
                layers=(
                    KemoGraphLayerStatus(
                        name="外部知识图谱向量化检索，用户知识库层",
                        switch="kemo_graph_user_knowledge",
                        enabled=True,
                        connected=True,
                        status="connected",
                        text="GRAPH_RETRIEVAL_RESULT",
                    ),
                )
            )

        bundle = build_prompt_bundle(
            root,
            "alice",
            config,
            graph_context_loader=graph_loader,
        )
        self.assertEqual(
            [item["scope"] for item in bundle.diagnostics["knowledge_documents"]],
            ["user", "shared", "global"],
        )
        self.assertIn("GRAPH_RETRIEVAL_RESULT", bundle.text)
        self.assertNotIn("[user:index.md]", bundle.text)
        self.assertIn(
            "# users/alice/knowledge/ 目录结构，已被知识图谱替代",
            bundle.text,
        )
        self.assertIn("[shared:index.md]", bundle.text)
        self.assertIn("[global:index.md]", bundle.text)
        self.assertIn("PERMANENT", bundle.text)
        self.assertIn("IMPORTANT", bundle.text)
        self.assertIn("SEVEN", bundle.text)

    def test_kemo_graph_can_replace_all_knowledge_scopes(self) -> None:
        _, root, config = self.make_root()
        self.populate_all_sections(root)
        config["kemo_graph"] = {
            "kemo_graph_global_knowledge": True,
            "kemo_graph_shared_knowledge": True,
            "kemo_graph_user_knowledge": True,
        }
        (root / "shared_knowledge" / "index.md").write_text(
            "SHARED_INDEX", "utf-8"
        )
        (root / "global_knowledge" / "index.md").write_text(
            "GLOBAL_INDEX", "utf-8"
        )

        bundle = build_prompt_bundle(root, "alice", config)

        self.assertEqual(
            [item["scope"] for item in bundle.diagnostics["knowledge_documents"]],
            ["user", "shared", "global"],
        )
        self.assertEqual(
            bundle.diagnostics["knowledge_replaced_scopes"],
            ["user", "shared", "global"],
        )
        knowledge = next(
            section for section in bundle.sections if section.name == "knowledge_index"
        )
        self.assertEqual(knowledge.content.count("已被知识图谱替代"), 3)
        self.assertNotIn("INDEX", knowledge.content)
        graph = next(section for section in bundle.sections if section.name == "kemo_graph")
        self.assertEqual(graph.content.count("该层检索结果暂不可用"), 3)
        self.assertIn("PERMANENT", bundle.text)
        self.assertIn("IMPORTANT", bundle.text)
        self.assertIn("SEVEN", bundle.text)

    def test_kemo_graph_temporary_memory_keeps_permanent_and_important(self) -> None:
        _, root, config = self.make_root()
        self.populate_all_sections(root)
        config["kemo_graph"] = {"kemo_graph_temporary_memory": True}

        def graph_loader(*_args, **kwargs):
            self.assertFalse(kwargs["replaces_knowledge"])
            self.assertTrue(kwargs["replaces_memory"])
            return KemoGraphPromptContext(
                layers=tuple(
                    KemoGraphLayerStatus(
                        name=name,
                        switch="kemo_graph_temporary_memory",
                        enabled=True,
                        connected=True,
                        status="connected",
                        text=f"GRAPH_MEMORY_RESULT:{name}",
                    )
                    for name in (
                        "用户的临时重要记忆，遗忘周期6个月",
                        "用户的临时重要记忆，遗忘周期一个月",
                        "用户的临时重要记忆，遗忘周期七天",
                    )
                )
            )

        bundle = build_prompt_bundle(
            root,
            "alice",
            config,
            graph_context_loader=graph_loader,
        )
        sections = {section.name: section for section in bundle.sections}
        self.assertIn("INDEX", bundle.text)
        self.assertIn("PERMANENT", sections["permanent_memory"].content)
        self.assertIn("IMPORTANT", sections["important_memory"].content)
        for name in (
            "temporary_memory:half_year",
            "temporary_memory:one_month",
            "temporary_memory:seven_days",
        ):
            self.assertIn("已被知识图谱替代", sections[name].content)
        self.assertNotIn("HALF", bundle.text)
        self.assertNotIn("MONTH", bundle.text)
        self.assertNotIn("SEVEN", bundle.text)
        self.assertEqual(bundle.memory_ids, ())

    def test_kemo_graph_default_loader_reports_all_six_enabled_layers(self) -> None:
        _, root, config = self.make_root()
        self.populate_all_sections(root)
        config["kemo_graph"] = {
            "kemo_graph_global_knowledge": True,
            "kemo_graph_shared_knowledge": True,
            "kemo_graph_user_knowledge": True,
            "kemo_graph_temporary_memory": True,
        }

        bundle = build_prompt_bundle(root, "alice", config)

        graph = next(section for section in bundle.sections if section.name == "kemo_graph")
        self.assertEqual(graph.content.count("该层检索结果暂不可用"), 6)
        self.assertEqual(graph.content.count("# 外部知识图谱向量化检索"), 3)
        self.assertEqual(graph.content.count("# 用户的临时重要记忆"), 3)
        diagnostics = bundle.diagnostics["kemo_graph"]
        self.assertEqual(diagnostics["status"], "not_connected")
        self.assertEqual(len(diagnostics["layers"]), 6)
        self.assertTrue(all(layer["enabled"] for layer in diagnostics["layers"]))

    def test_plugin_whitelist_filters_prompt_manifests(self) -> None:
        _, root, config = self.make_root()
        self.write_plugin(root, "clock")
        self.write_plugin(root, "weather")

        unrestricted_bundle = build_prompt_bundle(root, "alice", config)
        unrestricted_plugins = next(
            section.content
            for section in unrestricted_bundle.sections
            if section.name == "plugins"
        )
        self.assertIn("clock", unrestricted_plugins)
        self.assertIn("weather", unrestricted_plugins)

        config["plugins"] = {"whitelist": ["clock"]}
        filtered_bundle = build_prompt_bundle(root, "alice", config)
        filtered_plugins = next(
            section.content
            for section in filtered_bundle.sections
            if section.name == "plugins"
        )
        self.assertIn("clock", filtered_plugins)
        self.assertNotIn("weather", filtered_plugins)

    def test_config_defaults_switches_limits_and_search_rejection(self) -> None:
        defaults = parse_prompt_settings({})
        self.assertEqual(defaults.temporary_memory_limits["seven_days"], 100)
        self.assertEqual(defaults.char_limits["task_plan"], 6000)
        with self.assertRaisesRegex(PromptConfigError, "已移除"):
            parse_prompt_settings({"prompt": {"include_user_soul": False}})
        with self.assertRaises(PromptConfigError):
            parse_prompt_settings({"prompt": {"char_limits": {"perception": -1}}})
        with self.assertRaisesRegex(PromptConfigError, "未知项"):
            parse_prompt_settings({"memory": {"temporary_injection_limits": {"typo": 1}}})
        with self.assertRaisesRegex(PromptConfigError, "暂不支持"):
            parse_prompt_settings({"prompt": {"injection_mode": {"knowledge_index": "search"}}})

    def test_user_prompt_override_deep_merges_and_base_sections_are_always_on(self) -> None:
        _, root, config = self.make_root()
        (root / "config" / "global_soul.md").write_text("GLOBAL", "utf-8")
        (root / "users" / "alice" / "user_soul.md").write_text("USER", "utf-8")
        (root / "agents.md").write_text("AGENTS", "utf-8")
        (root / "users" / "alice" / "memory_temporary_important.md").write_text("HOT", "utf-8")
        config["prompt"] = {"char_limits": {"perception": 200}}
        (root / "config" / "global_config.json").write_text(json.dumps(config), "utf-8")
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "prompt": {
                        "char_limits": {"task_plan": 7},
                    }
                }
            ),
            "utf-8",
        )
        merged = load_config("alice", root)
        settings = parse_prompt_settings(merged)
        self.assertEqual(settings.char_limits["task_plan"], 7)
        self.assertEqual(settings.char_limits["perception"], 200)
        bundle = build_prompt_bundle(root, "alice", merged)
        self.assertEqual(
            tuple(section.name for section in bundle.sections),
            PROMPT_SECTION_ORDER,
        )
        nonempty = [
            section.name for section in bundle.sections if section.content != "（无）"
        ]
        self.assertEqual(
            nonempty,
            ["user_soul", "global_soul", "agents_manual", "important_memory"],
        )

    def test_natural_sort_and_skill_description_boundary(self) -> None:
        self.assertLess(natural_path_key("file2.md"), natural_path_key("file10.md"))
        _, root, config = self.make_root()
        for name in ("skill10", "skill2"):
            directory = root / "shared_skills" / name
            directory.mkdir()
            (directory / "SKILL.md").write_text(
                f"# {name}\n{name} trigger\nline two\n\n## Tool\nSECRET_SCHEMA", "utf-8"
            )
        bundle = build_prompt_bundle(root, "alice", config)
        content = next(section.content for section in bundle.sections if section.name == "skills")
        self.assertLess(content.index("skill2"), content.index("skill10"))
        self.assertIn("line two", content)
        self.assertNotIn("SECRET_SCHEMA", content)

    def test_main_source_whitelists_filter_prompt_and_report_unmatched(self) -> None:
        _, root, config = self.make_root()
        nested_skill = root / "shared_skills" / "development" / "python"
        nested_skill.mkdir(parents=True)
        (nested_skill / "SKILL.md").write_text(
            "# python\nSHARED_KEEP", "utf-8"
        )
        dropped_skill = root / "shared_skills" / "dropped"
        dropped_skill.mkdir()
        (dropped_skill / "SKILL.md").write_text(
            "# dropped\nSHARED_DROP", "utf-8"
        )
        user_skill = root / "users" / "alice" / "user_skills" / "private"
        user_skill.mkdir()
        (user_skill / "SKILL.md").write_text("# private\nUSER_DROP", "utf-8")

        self.write_expand_module(root, "global", "keep", input_text="GLOBAL_KEEP", open_control=False)
        self.write_expand_module(root, "global", "drop", input_text="GLOBAL_DROP", open_control=False)
        self.write_expand_module(root, "user", "personal", input_text="USER_EXPAND", open_control=False)

        self.write_sense_module(root, "runtime", "SENSE_KEEP")
        self.write_sense_module(root, "network", "SENSE_DROP", health="异常")
        (root / "global_sense" / "root.md").write_text("ROOT_DROP", "utf-8")

        config.update(
            {
                "skills": {
                    "shared_whitelist": ["development/python", "missing/shared"],
                },
                "expand": {
                    "global_whitelist": ["keep", "missing/expand"],
                    "shared_whitelist": [],
                },
                "perception": {
                    "global_whitelist": ["runtime", "missing/sense"]
                },
            }
        )
        bundle = build_prompt_bundle(root, "alice", config)

        self.assertIn("SHARED_KEEP", bundle.text)
        self.assertNotIn("SHARED_DROP", bundle.text)
        self.assertIn("USER_DROP", bundle.text)
        self.assertIn("GLOBAL_KEEP", bundle.text)
        self.assertNotIn("GLOBAL_DROP", bundle.text)
        self.assertIn("USER_EXPAND", bundle.text)
        self.assertIn("SENSE_KEEP", bundle.text)
        self.assertNotIn("SENSE_DROP", bundle.text)
        self.assertNotIn("ROOT_DROP", bundle.text)

        diagnostics = bundle.diagnostics["source_selection"]
        self.assertEqual(
            diagnostics["skills"]["shared"]["selected"],
            ["development/python"],
        )
        self.assertEqual(
            diagnostics["skills"]["shared"]["unmatched"],
            ["missing/shared"],
        )
        self.assertEqual(
            diagnostics["skills"]["user"]["selected"],
            ["private"],
        )
        self.assertEqual(
            diagnostics["expand"]["global"]["unmatched"],
            ["missing/expand"],
        )
        self.assertEqual(
            diagnostics["expand"]["user"]["mode"],
            "all",
        )
        self.assertEqual(
            diagnostics["perception"]["global"]["selected"],
            ["runtime"],
        )
        self.assertEqual(
            diagnostics["perception"]["global"]["unmatched"],
            ["missing/sense"],
        )

    def test_plugin_tool_block_stays_out_of_prompt_and_user_skill_tool_is_ignored(self) -> None:
        _, root, config = self.make_root()
        self.write_plugin(root, "clock")
        skill = root / "users" / "alice" / "user_skills" / "danger"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "# danger\nprompt only\n\n## Tool\n```json\n"
            '{"name":"danger","input_schema":{"type":"object"}}\n```',
            "utf-8",
        )
        bundle = build_prompt_bundle(root, "alice", config)
        registry = discover_tools(root, "alice")
        self.assertEqual(list(registry.tools), ["clock"])
        self.assertIn("prompt only", bundle.text)
        self.assertNotIn("tool.py:run", bundle.text)
        self.assertNotIn("tool schema description", bundle.text)

    def test_static_registration_and_dynamic_user_skill_resolution(self) -> None:
        _, root, config = self.make_root()
        directory = root / "shared_skills" / "shared"
        directory.mkdir()
        (directory / "SKILL.md").write_text("# shared\nshared description", "utf-8")
        user_skill = root / "users" / "alice" / "user_skills" / "private"
        user_skill.mkdir()
        (user_skill / "SKILL.md").write_text("# private\nprivate description", "utf-8")
        bundle = build_prompt_bundle(root, "alice", config)
        skills = next(section.content for section in bundle.sections if section.name == "skills")
        self.assertIn("shared description", skills)
        self.assertIn("private description", skills)
        (root / "users" / "alice" / "user_skills" / "register.py").write_text(
            "raise AssertionError('user Python must never execute')\n",
            "utf-8",
        )
        bundle = build_prompt_bundle(root, "alice", config)
        skills = next(section.content for section in bundle.sections if section.name == "skills")
        self.assertIn("shared description", skills)
        self.assertIn("private description", skills)
        (user_skill / "SKILL.md").unlink()
        bundle = build_prompt_bundle(root, "alice", config)
        skills = next(section.content for section in bundle.sections if section.name == "skills")
        self.assertNotIn("private description", skills)
        (root / "shared_skills" / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    registry.add_skills('shared', Path(__file__).resolve().parent.parent)\n",
            "utf-8",
        )
        with self.assertRaises(PromptRegistrationError):
            build_prompt_bundle(root, "alice", config)

    def test_memory_tiers_weight_limits_stability_and_important_char_limit(self) -> None:
        _, root, config = self.make_root()
        self.write_memory(
            root,
            "seven_days",
            [
                {"filename": "low", "content": "LOW", "weight": 1},
                {"filename": "first", "content": "FIRST", "weight": 9},
                {"filename": "second", "content": "SECOND", "weight": 9},
            ],
        )
        config["prompt"] = {}
        config["memory"] = {}
        config["memory"]["temporary_injection_limits"] = {"seven_days": 2}
        config["memory"]["important_memory_max_chars"] = 4
        (root / "users" / "alice" / "memory_temporary_important.md").write_text("IMPORTANT", "utf-8")
        bundle = build_prompt_bundle(root, "alice", config)
        temporary = next(s for s in bundle.sections if s.name == "temporary_memory:seven_days")
        important = next(s for s in bundle.sections if s.name == "important_memory")
        self.assertEqual(temporary.item_ids, ("first.md", "second.md"))
        self.assertLess(temporary.content.index("FIRST"), temporary.content.index("SECOND"))
        self.assertTrue(temporary.truncated)
        self.assertEqual(important.content, "IMPO")

    def test_missing_memory_body_does_not_block_prompt_and_is_diagnosed(self) -> None:
        _, root, config = self.make_root()
        self.write_memory(
            root,
            "half_year",
            [
                {"filename": "missing", "content": "MISSING", "weight": 10},
                {"filename": "valid", "content": "VALID", "weight": 1},
            ],
        )
        (root / "users" / "alice" / "improve" / "half_year" / "missing.md").unlink()
        config["memory"] = {"temporary_injection_limits": {"half_year": 1}}

        with self.assertLogs("run.memory", level="WARNING"):
            bundle = build_prompt_bundle(root, "alice", config)

        section = next(
            item
            for item in bundle.sections
            if item.name == "temporary_memory:half_year"
        )
        self.assertEqual(section.item_ids, ("valid.md",))
        self.assertIn("VALID", section.content)
        self.assertEqual(
            bundle.diagnostics["memory_integrity_warnings"],
            ["missing_file:half_year/missing.md"],
        )

    def test_plan_mapping_finished_omission_and_step_format(self) -> None:
        _, root, _ = self.make_root()
        directory = root / "users" / "alice" / "task_plan"
        plans = [
            ("plan_2.json", "approved", "active", "running"),
            ("plan_10.json", "completed", "completed", "completed"),
            ("plan_11.json", "failed", "aborted", "failed"),
        ]
        for name, status, _, step_status in plans:
            (directory / name).write_text(
                json.dumps(
                    {
                        "plan_id": name[:-5],
                        "title": name,
                        "description": "desc",
                        "status": status,
                        "steps": [{"description": "step desc", "status": step_status}],
                    }
                ),
                "utf-8",
            )
        selection = select_prompt_plans(root, "alice", max_chars=1000)
        self.assertIn("status: active", selection.text)
        self.assertIn("step desc（running）", selection.text)
        self.assertNotIn("plan_10", selection.text)
        self.assertNotIn("plan_11", selection.text)

    def test_expand_registration_module_controls_root_and_rejects_wrong_root(self) -> None:
        _, root, _ = self.make_root()
        self.write_expand_module(root, "global", "registered", input_text="REGISTERED", open_control=False)
        self.write_expand_module(root, "global", "second", input_text="SECOND", open_control=False)
        registrar = root / "global_expand" / "register.py"
        sources = load_prompt_source_registry(root, "alice")
        selection = sources.select_expand(max_chars=1000)
        self.assertIn("REGISTERED", selection.text)
        self.assertIn("SECOND", selection.text)
        registrar.unlink()
        selection = load_prompt_source_registry(root, "alice").select_expand(max_chars=1000)
        self.assertNotIn("REGISTERED", selection.text)
        self.assertNotIn("SECOND", selection.text)
        registrar.write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    registry.add_expand_root('global', Path(__file__).resolve().parent.parent)\n",
            "utf-8",
        )
        with self.assertRaises(PromptRegistrationError):
            load_prompt_source_registry(root, "alice")
        registrar.write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    base = Path(__file__).resolve().parent\n"
            "    registry.add_expand_root('global', base)\n"
            "    registry.add_expand_root('global', base)\n",
            "utf-8",
        )
        with self.assertRaisesRegex(PromptRegistrationError, "重复"):
            load_prompt_source_registry(root, "alice")

    def test_expand_standard_module_injects_data_and_only_control_injection_layer(self) -> None:
        _, root, _ = self.make_root()
        self.write_expand_module(
            root,
            "global",
            "light",
            input_text="LIGHT_STATE",
            control_injection="LIGHT_CONTROL_AVAILABLE",
            operation_text="OPERATION_SECRET",
        )
        registry = load_prompt_source_registry(root, "alice")
        selection = registry.select_expand(max_chars=2000)
        self.assertIn("[global:light]", selection.text)
        self.assertIn("## 数据采集\nLIGHT_STATE", selection.text)
        self.assertIn("## 操控能力\nLIGHT_CONTROL_AVAILABLE", selection.text)
        self.assertNotIn("OPERATION_SECRET", selection.text)
        self.assertEqual(
            selection.source_files,
            (
                "global_expand/light/input_data.md",
                "global_expand/light/expand_control.md",
            ),
        )
        status = registry.selection_diagnostics()["expand"]["global"]["health_status"]["light"]
        self.assertTrue(status["valid"])
        self.assertEqual(status["input_health"], "正常")
        self.assertEqual(status["control_file"], "global_expand/light/expand_control.md")

    def test_expand_switches_health_and_missing_control_file_are_independent(self) -> None:
        _, root, _ = self.make_root()
        self.write_expand_module(
            root,
            "global",
            "control_only",
            input_text="INPUT_DISABLED",
            control_injection="CONTROL_ONLY",
            open_input=False,
        )
        self.write_expand_module(
            root,
            "global",
            "unhealthy",
            input_text="UNHEALTHY_INPUT",
            control_injection="HEALTH_INDEPENDENT_CONTROL",
            input_health="异常",
        )
        missing_control = self.write_expand_module(
            root,
            "global",
            "missing_control",
            input_text="DATA_STILL_AVAILABLE",
        )
        (missing_control / "expand_control.md").unlink()
        registry = load_prompt_source_registry(root, "alice")
        selection = registry.select_expand(max_chars=4000)
        self.assertNotIn("INPUT_DISABLED", selection.text)
        self.assertIn("CONTROL_ONLY", selection.text)
        self.assertNotIn("UNHEALTHY_INPUT", selection.text)
        self.assertIn("HEALTH_INDEPENDENT_CONTROL", selection.text)
        self.assertIn("DATA_STILL_AVAILABLE", selection.text)
        self.assertNotIn("[global:missing_control]\n## 操控能力", selection.text)
        status = registry.selection_diagnostics()["expand"]["global"]["health_status"]
        self.assertTrue(status["missing_control"]["valid"])
        self.assertEqual(status["unhealthy"]["input_health"], "异常")

    def test_expand_invalid_manifests_are_diagnosed_and_scope_user_isolation_holds(self) -> None:
        _, root, _ = self.make_root()
        missing = root / "global_expand" / "missing_manifest"
        missing.mkdir()
        missing_field = self.write_expand_module(root, "global", "missing_field", input_text="MISSING_FIELD")
        missing_payload = json.loads((missing_field / "expand.json").read_text("utf-8"))
        missing_payload.pop("input_health")
        (missing_field / "expand.json").write_text(json.dumps(missing_payload), "utf-8")
        bad_bool = self.write_expand_module(root, "global", "bad_bool", input_text="BAD_BOOL")
        bad_payload = json.loads((bad_bool / "expand.json").read_text("utf-8"))
        bad_payload["open_input"] = "true"
        (bad_bool / "expand.json").write_text(json.dumps(bad_payload), "utf-8")
        traversal = self.write_expand_module(root, "global", "traversal", input_text="TRAVERSAL")
        traversal_payload = json.loads((traversal / "expand.json").read_text("utf-8"))
        traversal_payload["input_data"] = "../outside.md"
        (traversal / "expand.json").write_text(json.dumps(traversal_payload), "utf-8")

        self.write_expand_module(root, "global", "global_ok", input_text="GLOBAL_LAYER", open_control=False)
        self.write_expand_module(root, "shared", "shared_ok", input_text="SHARED_LAYER", open_control=False)
        self.write_expand_module(root, "user", "alice_only", input_text="ALICE_LAYER", open_control=False)
        self.write_expand_module(root, "user", "bob_only", input_text="BOB_LAYER", open_control=False, user="bob")

        alice_registry = load_prompt_source_registry(root, "alice")
        alice = alice_registry.select_expand(max_chars=5000)
        self.assertLess(alice.text.index("GLOBAL_LAYER"), alice.text.index("SHARED_LAYER"))
        self.assertLess(alice.text.index("SHARED_LAYER"), alice.text.index("ALICE_LAYER"))
        self.assertNotIn("BOB_LAYER", alice.text)
        diagnostics = alice_registry.selection_diagnostics()["expand"]["global"]
        self.assertEqual(
            set(diagnostics["invalid"]),
            {"bad_bool", "missing_field", "missing_manifest", "traversal"},
        )
        self.assertTrue(all(not diagnostics["health_status"][name]["valid"] for name in diagnostics["invalid"]))

        bob = load_prompt_source_registry(root, "bob").select_expand(max_chars=5000)
        self.assertIn("BOB_LAYER", bob.text)
        self.assertNotIn("ALICE_LAYER", bob.text)

    def test_perception_injects_only_declared_data_file(self) -> None:
        _, root, _ = self.make_root()
        base = root / "global_sense"
        sensors = self.write_sense_module(root, "sensors", "ONLY_DECLARED")
        (sensors / "extra.md").write_text("EXTRA_MARKDOWN", "utf-8")
        (sensors / "helper.py").write_text("SECRET_HELPER = True", "utf-8")
        (base / ".hidden").mkdir()
        (base / ".hidden" / "secret.md").write_text("SECRET", "utf-8")
        (base / "root.md").write_text("ROOT", "utf-8")
        selection = load_prompt_source_registry(root, "alice").select_perception(max_chars=1000)
        self.assertEqual(selection.text, "[sensors]\nONLY_DECLARED")
        self.assertNotIn("EXTRA_MARKDOWN", selection.text)
        self.assertNotIn("SECRET_HELPER", selection.text)
        self.assertNotIn("SECRET", selection.text)
        self.assertNotIn("ROOT", selection.text)

    def test_perception_invalid_manifests_are_reported_and_skipped(self) -> None:
        _, root, _ = self.make_root()
        base = root / "global_sense"
        missing_manifest = base / "missing_manifest"
        missing_manifest.mkdir()
        (missing_manifest / "old.md").write_text("OLD_DATA", "utf-8")

        missing_field = base / "missing_field"
        missing_field.mkdir()
        (missing_field / "sense.md").write_text("MISSING_FIELD_DATA", "utf-8")
        (missing_field / "sense.json").write_text(
            json.dumps(
                {
                    "name": "missing field",
                    "recent_update": "2026-07-19 12:00:00",
                    "health": "正常",
                    "start_update": "data_update.py",
                }
            ),
            "utf-8",
        )

        broken_json = base / "broken_json"
        broken_json.mkdir()
        (broken_json / "sense.json").write_text("{broken", "utf-8")

        missing_file = base / "missing_file"
        missing_file.mkdir()
        (missing_file / "sense.json").write_text(
            json.dumps(
                {
                    "name": "missing file",
                    "data_md": "sense.md",
                    "recent_update": "2026-07-19 12:00:00",
                    "health": "正常",
                    "start_update": "data_update.py",
                }
            ),
            "utf-8",
        )

        traversal = base / "traversal"
        traversal.mkdir()
        (traversal / "sense.json").write_text(
            json.dumps(
                {
                    "name": "traversal",
                    "data_md": "../outside.md",
                    "recent_update": "2026-07-19 12:00:00",
                    "health": "正常",
                    "start_update": "data_update.py",
                }
            ),
            "utf-8",
        )

        registry = load_prompt_source_registry(root, "alice")
        inventory = {item["name"]: item for item in registry.perception_inventory()}
        self.assertEqual(set(inventory), {"broken_json", "missing_field", "missing_file", "missing_manifest", "traversal"})
        self.assertTrue(all(not item["valid"] for item in inventory.values()))
        self.assertTrue(all(item["health"] == "异常" for item in inventory.values()))
        self.assertTrue(all(item["status"] == "invalid" for item in inventory.values()))
        selection = registry.select_perception(max_chars=1000)
        self.assertEqual(selection.text, "")
        diagnostics = registry.selection_diagnostics()["perception"]["global"]
        self.assertEqual(set(diagnostics["invalid"]), set(inventory))
        self.assertTrue(all(not item["valid"] for item in diagnostics["health_status"].values()))

    def test_perception_health_diagnostics_and_zero_budget_preserve_discovery(self) -> None:
        _, root, _ = self.make_root()
        self.write_sense_module(root, "healthy", "HEALTHY")
        self.write_sense_module(root, "reported_error", "REPORTED_ERROR", health="异常")
        registry = load_prompt_source_registry(root, "alice")
        selection = registry.select_perception(max_chars=0, allow_modules=("healthy",))
        self.assertEqual(selection.text, "")
        diagnostics = registry.selection_diagnostics()["perception"]["global"]
        self.assertEqual(diagnostics["selected"], ["healthy"])
        self.assertEqual(diagnostics["filtered"], ["reported_error"])
        self.assertEqual(diagnostics["health_status"]["healthy"]["health"], "正常")
        self.assertEqual(diagnostics["health_status"]["reported_error"]["health"], "异常")

    def test_perception_registration_module_controls_source(self) -> None:
        _, root, _ = self.make_root()
        base = root / "global_sense"
        self.write_sense_module(root, "registered", "REGISTERED")
        self.write_sense_module(root, "unregistered", "UNREGISTERED")
        selection = load_prompt_source_registry(root, "alice").select_perception(max_chars=1000)
        self.assertIn("REGISTERED", selection.text)
        self.assertIn("UNREGISTERED", selection.text)
        (base / "register.py").unlink()
        selection = load_prompt_source_registry(root, "alice").select_perception(max_chars=1000)
        self.assertEqual(selection.text, "")
        (base / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    registry.add_perception(Path(__file__).resolve().parent.parent)\n",
            "utf-8",
        )
        with self.assertRaises(PromptRegistrationError):
            load_prompt_source_registry(root, "alice")

    def test_engine_uses_bundle_context_status_matches_and_memory_weights_after_commit(self) -> None:
        _, root, _ = self.make_root()
        self.write_memory(root, "seven_days", [{"filename": "memory", "content": "MEMORY", "weight": 0}])
        provider = CaptureProvider()
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            result = handle_request(
                {"user": "alice", "source": "cli", "session_id": "ok", "prompt": "unrelated"},
                root=root,
                provider_factory=lambda _: provider,
            )
        self.assertEqual(provider.requests[0].messages[0]["role"], "system")
        # This fixture deliberately exposes no Kemo capability endpoint.  The
        # runtime must not guess a fixed reasoning effort when the selected
        # model has no verified declaration.
        self.assertNotIn("reasoning_effort", provider.requests[0].extra)
        self.assertEqual(result["memory"]["injected_files"], ["seven_days/memory.md"])
        weighted = MemoryStore(root, "alice", result_config(root)).load_tier("seven_days")
        self.assertEqual(weighted[0]["weight"], 1)
        status = context_status(
            {"user": "alice", "source": "cli", "session_id": "ok"},
            root=root,
        )
        self.assertEqual(status["prompt"]["section_order"], result["prompt"]["section_order"])
        self.assertEqual(status["prompt"]["total_chars"], result["prompt"]["total_chars"])

    def test_failed_provider_does_not_weight_memory(self) -> None:
        _, root, _ = self.make_root()
        self.write_memory(root, "seven_days", [{"filename": "memory", "content": "MEMORY", "weight": 0}])
        provider = CaptureProvider(fail=True)
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            with self.assertRaises(EngineError):
                handle_request(
                    {"user": "alice", "source": "cli", "session_id": "fail", "prompt": "go"},
                    root=root,
                    provider_factory=lambda _: provider,
                )
        items = MemoryStore(root, "alice", result_config(root)).load_tier("seven_days")
        self.assertEqual(items[0]["weight"], 0)

    def test_cancelled_provider_does_not_weight_memory(self) -> None:
        _, root, _ = self.make_root()
        self.write_memory(root, "seven_days", [{"filename": "memory", "content": "MEMORY", "weight": 0}])
        cancel = threading.Event()

        class CancellingProvider(CaptureProvider):
            def chat_stream(self, request):
                self.requests.append(request)
                cancel.set()
                yield RunEvent(type="text_delta", content="partial")

        provider = CancellingProvider()
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(
                iter_request_events(
                    {
                        "user": "alice",
                        "source": "cli",
                        "session_id": "cancel",
                        "prompt": "go",
                        "stream": True,
                    },
                    root=root,
                    provider_factory=lambda _: provider,
                    cancel_event=cancel,
                )
            )
        self.assertEqual([event.type for event in events], ["done"])
        self.assertEqual(events[0].metadata["status"], "cancelled")
        items = MemoryStore(root, "alice", result_config(root)).load_tier("seven_days")
        self.assertEqual(items[0]["weight"], 0)

    def test_fixed_prompt_over_budget_fails_before_provider(self) -> None:
        _, root, config = self.make_root()
        (root / "config" / "global_soul.md").write_text("X" * 1000, "utf-8")
        config["agents"]["token_limit"] = 40
        config["agents"]["token_compression_ratio"] = 0.5
        (root / "config" / "global_config.json").write_text(json.dumps(config), "utf-8")
        provider = CaptureProvider()
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            with self.assertRaisesRegex(EngineError, "memory.temporary_injection_limits"):
                handle_request(
                    {"user": "alice", "source": "cli", "session_id": "large", "prompt": "go"},
                    root=root,
                    provider_factory=lambda _: provider,
                )
        self.assertEqual(provider.requests, [])


def result_config(root: Path) -> dict:
    return json.loads((root / "config" / "global_config.json").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
