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
from provider.schema import ChatResponse, Usage
from run.config import load_config
from run.engine import EngineError, context_status, handle_request, iter_request_events
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


class PromptPipelineTests(unittest.TestCase):
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
            (root / "global_expand" / "register.py", "pass"),
            (root / "shared_expand" / "register.py", "pass"),
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
            "memory": {"extraction_enabled": False},
            "agents": {
                "n1_recent_rounds_before_tool_compression": 3,
                "n2_max_rounds": 30,
                "n3_rounds_after_compression": 10,
                "n4_token_limit": 120000,
                "n5_token_compression_ratio": 0.6,
            },
        }
        (root / "config" / "global_config.json").write_text(json.dumps(config), "utf-8")
        (root / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
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
        module = root / "global_expand" / "water"
        module.mkdir()
        (module / "inject.md").write_text("EXPAND", "utf-8")
        (root / "global_expand" / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    base = Path(__file__).resolve().parent\n"
            "    registry.add_expand('global', 'water', base / 'water' / 'inject.md')\n",
            "utf-8",
        )
        sense_module = root / "global_sense" / "runtime"
        sense_module.mkdir()
        (sense_module / "sense.md").write_text("SENSE", "utf-8")

    def test_exact_section_order_empty_omission_and_perception_last(self) -> None:
        _, root, config = self.make_root()
        self.assertEqual(build_prompt_bundle(root, "alice", config).sections, ())
        self.populate_all_sections(root)
        bundle = build_prompt_bundle(root, "alice", config)
        self.assertEqual(tuple(section.name for section in bundle.sections), PROMPT_SECTION_ORDER)
        self.assertEqual(bundle.sections[-1].name, "perception")
        self.assertEqual(bundle.text.count("[perception]"), 1)

    def test_config_defaults_switches_limits_and_search_rejection(self) -> None:
        defaults = parse_prompt_settings({})
        self.assertEqual(defaults.temporary_memory_limits["seven_days"], 3)
        self.assertEqual(defaults.char_limits["knowledge_index"], 8000)
        disabled = parse_prompt_settings({"prompt": {"include_user_soul": False}})
        self.assertFalse(disabled.include_user_soul)
        with self.assertRaises(PromptConfigError):
            parse_prompt_settings({"prompt": {"char_limits": {"perception": -1}}})
        with self.assertRaisesRegex(PromptConfigError, "未知项"):
            parse_prompt_settings({"memory": {"temporary_injection_limits": {"typo": 1}}})
        with self.assertRaisesRegex(PromptConfigError, "暂不支持"):
            parse_prompt_settings({"prompt": {"injection_mode": {"knowledge_index": "search"}}})

    def test_user_prompt_override_deep_merges_and_include_switches_apply(self) -> None:
        _, root, config = self.make_root()
        (root / "config" / "global_soul.md").write_text("GLOBAL", "utf-8")
        (root / "users" / "alice" / "user_soul.md").write_text("USER", "utf-8")
        (root / "agents.md").write_text("AGENTS", "utf-8")
        (root / "users" / "alice" / "memory_temporary_important.md").write_text("HOT", "utf-8")
        config["prompt"] = {"char_limits": {"knowledge_index": 100, "perception": 200}}
        (root / "config" / "global_config.json").write_text(json.dumps(config), "utf-8")
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "prompt": {
                        "include_global_soul": False,
                        "include_user_soul": False,
                        "include_agents_manual": False,
                        "include_important_memory": False,
                        "char_limits": {"knowledge_index": 7},
                    }
                }
            ),
            "utf-8",
        )
        merged = load_config("alice", root)
        settings = parse_prompt_settings(merged)
        self.assertEqual(settings.char_limits["knowledge_index"], 7)
        self.assertEqual(settings.char_limits["perception"], 200)
        self.assertEqual(build_prompt_bundle(root, "alice", merged).sections, ())

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

        global_keep = root / "global_expand" / "keep"
        global_drop = root / "global_expand" / "drop"
        user_expand = root / "users" / "alice" / "expand" / "personal"
        for directory, text in (
            (global_keep, "GLOBAL_KEEP"),
            (global_drop, "GLOBAL_DROP"),
            (user_expand, "USER_EXPAND"),
        ):
            directory.mkdir()
            (directory / "inject.md").write_text(text, "utf-8")
        (root / "global_expand" / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    base = Path(__file__).resolve().parent\n"
            "    registry.add_expand('global', 'keep', base / 'keep' / 'inject.md')\n"
            "    registry.add_expand('global', 'drop', base / 'drop' / 'inject.md')\n",
            "utf-8",
        )

        runtime = root / "global_sense" / "runtime"
        network = root / "global_sense" / "network"
        runtime.mkdir()
        network.mkdir()
        (runtime / "status.md").write_text("SENSE_KEEP", "utf-8")
        (network / "status.md").write_text("SENSE_DROP", "utf-8")
        (root / "global_sense" / "root.md").write_text("ROOT_DROP", "utf-8")

        config.update(
            {
                "skills": {
                    "shared_whitelist": ["development/python", "missing/shared"],
                    "user_whitelist": ["missing/user"],
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
        self.assertNotIn("USER_DROP", bundle.text)
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
            diagnostics["skills"]["user"]["filtered"],
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

    def test_expand_registration_module_only_and_path_escape(self) -> None:
        _, root, _ = self.make_root()
        registered = root / "global_expand" / "registered"
        registered.mkdir()
        (registered / "inject.md").write_text("REGISTERED", "utf-8")
        unregistered = root / "global_expand" / "unregistered"
        unregistered.mkdir()
        (unregistered / "inject.md").write_text("UNREGISTERED", "utf-8")
        registrar = root / "global_expand" / "register.py"
        registrar.write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    base = Path(__file__).resolve().parent\n"
            "    registry.add_expand('global', 'registered', base / 'registered' / 'inject.md')\n",
            "utf-8",
        )
        sources = load_prompt_source_registry(root, "alice")
        selection = sources.select_expand(max_chars=1000)
        self.assertIn("REGISTERED", selection.text)
        self.assertNotIn("UNREGISTERED", selection.text)
        registrar.write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    base = Path(__file__).resolve().parent\n"
            "    registry.add_expand('global', 'registered', base / 'unregistered' / 'inject.md')\n",
            "utf-8",
        )
        with self.assertRaises(PromptRegistrationError):
            load_prompt_source_registry(root, "alice")
        registrar.write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    base = Path(__file__).resolve().parent\n"
            "    path = base / 'registered' / 'inject.md'\n"
            "    registry.add_expand('global', 'registered', path)\n"
            "    registry.add_expand('global', 'registered', path)\n",
            "utf-8",
        )
        with self.assertRaisesRegex(PromptRegistrationError, "重复"):
            load_prompt_source_registry(root, "alice")

    def test_perception_is_recursive_md_only_hidden_safe_and_naturally_sorted(self) -> None:
        _, root, _ = self.make_root()
        base = root / "global_sense"
        (base / "sensors").mkdir()
        (base / "sensors" / "file10.md").write_text("TEN", "utf-8")
        (base / "sensors" / "file2.md").write_text("TWO", "utf-8")
        (base / "sensors" / "ignore.txt").write_text("TXT", "utf-8")
        (base / ".hidden").mkdir()
        (base / ".hidden" / "secret.md").write_text("SECRET", "utf-8")
        (base / "root.md").write_text("ROOT", "utf-8")
        selection = load_prompt_source_registry(root, "alice").select_perception(max_chars=1000)
        self.assertLess(selection.text.index("TWO"), selection.text.index("TEN"))
        self.assertNotIn("TXT", selection.text)
        self.assertNotIn("SECRET", selection.text)
        self.assertNotIn("ROOT", selection.text)

    def test_perception_registration_module_controls_source(self) -> None:
        _, root, _ = self.make_root()
        base = root / "global_sense"
        registered = base / "registered"
        unregistered = base / "unregistered"
        registered.mkdir()
        unregistered.mkdir()
        (registered / "status.md").write_text("REGISTERED", "utf-8")
        (unregistered / "status.md").write_text("UNREGISTERED", "utf-8")
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
        self.assertEqual(events, [])
        items = MemoryStore(root, "alice", result_config(root)).load_tier("seven_days")
        self.assertEqual(items[0]["weight"], 0)

    def test_fixed_prompt_over_budget_fails_before_provider(self) -> None:
        _, root, config = self.make_root()
        (root / "config" / "global_soul.md").write_text("X" * 1000, "utf-8")
        config["agents"]["n4_token_limit"] = 40
        config["agents"]["n5_token_compression_ratio"] = 0.5
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
