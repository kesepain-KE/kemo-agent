from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agents._runtime.user_packages import UserAgentPackageError
from plugins.subagent_dispatch.tool import run as dispatch
from provider.adapters.compat import chat_response_to_kemo, kemo_request_to_chat
from provider.schema import ChatResponse, ToolCall, Usage
from run.agent_queue import AgentScheduler
from run.agent_runner import AgentRunError, AgentRunner
from run.agents import (
    AgentDisabledError,
    AgentError,
    AgentManifestError,
    discover_agents,
)


class ScriptedProvider:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        return self.responses.pop(0)

    def create(self, request):
        return chat_response_to_kemo(self.chat(kemo_request_to_chat(request)), request)


class SubAgentHotPlugTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path, dict]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for path in (
            root / "agents",
            root / "plugins",
            root / "shared_skills",
            root / "shared_knowledge",
            root / "global_knowledge",
            root / "global_expand",
            root / "shared_expand",
            root / "global_sense",
            root / "config",
            root / "users" / "alice" / "agents",
            root / "users" / "alice" / "user_skills",
            root / "users" / "alice" / "expand",
            root / "users" / "alice" / "knowledge",
            root / "users" / "bob" / "agents",
            root / "users" / "bob" / "user_skills",
            root / "users" / "bob" / "expand",
            root / "users" / "bob" / "knowledge",
        ):
            path.mkdir(parents=True, exist_ok=True)
        config = {
            "provider": {
                "type": "kemo",
                "base_url": "http://127.0.0.1:1/v1",
                "api_key": "test",
                "model": "mock",
                "stream": False,
            },
            "tools": {"timeout": 2},
        }
        (root / "config" / "global_config.json").write_text(json.dumps(config), "utf-8")
        for user in ("alice", "bob"):
            (root / "users" / user / "user_config.json").write_text("{}", "utf-8")
        return temporary, root, config

    def write_agent(
        self,
        root: Path,
        user: str,
        name: str,
        *,
        enabled: bool = True,
        exposure: str = "tool",
        tools: list[str] | None = None,
        user_skills: list[str] | None = None,
        execution: str = "sync",
    ) -> Path:
        directory = root / "users" / user / "agents" / name
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 2,
            "name": name,
            "version": "1.0.0",
            "description": f"{name} description",
            "enabled": enabled,
            "instruction": "AGENT.md",
            "executor": "builtin:llm",
            "config": "agent-config.json",
            "model_profile": "default",
            "timeout": 10,
            "execution": execution,
            "write_policy": "none",
            "input_schema": {"type": "object", "additionalProperties": True},
            "output_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        }
        capabilities = {
            "schema_version": 1,
            "exposure": {"mode": exposure, "allowed_callers": ["main_agent"]},
            "tools": {"plugins": {"allow": tools or []}, "max_iterations": 3},
            "prompt_sources": {
                "skills": {"shared": [], "user": user_skills or []},
                "expand": {"global": [], "shared": [], "user": []},
            },
            "knowledge": {
                "scopes": [],
                "index_enabled": False,
            },
            "context": {
                "inherit_main_history": False,
                "inherit_current_request": False,
            },
        }
        (directory / "agent.json").write_text(json.dumps(manifest), "utf-8")
        (directory / "agent-config.json").write_text(json.dumps(capabilities), "utf-8")
        (directory / "AGENT.md").write_text(
            f"# {name}\n{user} agent instruction", "utf-8"
        )
        return directory

    def write_plugin(self, root: Path, name: str) -> None:
        plugin = root / "plugins" / name
        plugin.mkdir()
        tool = {
            "name": name,
            "description": f"{name} value",
            "input_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "version": "1",
            "enabled": True,
            "entrypoint": "tool.py:run",
        }
        (plugin / "SKILL.md").write_text(
            f"# {name}\n{name}\n\n## Tool\n```json\n" + json.dumps(tool) + "\n```\n",
            "utf-8",
        )
        (plugin / "tool.py").write_text(
            "def run(value, *, context):\n    return {'value': value, 'user': context['user']}\n",
            "utf-8",
        )

    def test_runner_detects_add_disable_and_remove_without_restart(self) -> None:
        _, root, config = self.make_root()
        provider = ScriptedProvider(
            [ChatResponse(text='{"answer":"ok"}', model="mock", usage=Usage())]
        )
        runner = AgentRunner(
            root, "alice", config=config, provider_factory=lambda _: provider
        )
        self.assertEqual(runner.registry.agents, {})
        directory = self.write_agent(root, "alice", "custom")
        result = runner.run("custom", {"value": 1})
        self.assertEqual(result.data, {"answer": "ok"})
        manifest_path = directory / "agent.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["enabled"] = False
        manifest_path.write_text(json.dumps(manifest), "utf-8")
        with self.assertRaises(AgentDisabledError):
            runner.run("custom", {})
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
        with self.assertRaises(AgentError):
            runner.run("custom", {})

    def test_user_agents_are_isolated_and_python_files_are_allowed(self) -> None:
        _, root, _ = self.make_root()
        directory = self.write_agent(root, "alice", "private")
        self.assertIn("private", discover_agents(root, "alice").agents)
        self.assertNotIn("private", discover_agents(root, "bob").agents)
        (directory / "helper.py").write_text("VALUE = 'allowed'\n", "utf-8")
        definition = discover_agents(root, "alice").get("private")
        self.assertEqual(definition.executor, "builtin:llm")

    def test_compact_user_agent_auto_detects_and_runs_executor(self) -> None:
        _, root, config = self.make_root()
        dispatch(
            "create",
            definition={
                "name": "custom_executor",
                "description": "runs user Python",
                "instruction": "Return a structured answer.",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            },
            context={"root": str(root), "user": "alice"},
        )
        package = root / "users" / "alice" / "agents" / "custom_executor"
        (package / "executor.py").write_text(
            "from run.agent_runner import AgentRunResult\n\n"
            "def execute(context, input_data):\n"
            "    value = input_data.get('value', '')\n"
            "    return AgentRunResult(\n"
            "        agent=context.definition.name,\n"
            "        data={'answer': f'custom:{value}'},\n"
            "        raw_text='',\n"
            "        usage={'total_tokens': 0},\n"
            "        model='user-executor',\n"
            "        metadata={'custom_executor': True},\n"
            "    )\n",
            "utf-8",
        )

        definition = discover_agents(root, "alice").get("custom_executor")
        self.assertEqual(definition.source, "user")
        self.assertEqual(definition.executor, "executor.py:execute")
        result = AgentRunner(
            root,
            "alice",
            config=config,
            provider_factory=lambda _: self.fail("自定义 executor 不应调用 Provider"),
        ).run("custom_executor", {"value": "ok"})
        self.assertEqual(result.data, {"answer": "custom:ok"})
        self.assertEqual(result.model, "user-executor")
        self.assertTrue(result.metadata["custom_executor"])

    def test_schema_v2_user_agent_can_explicitly_select_executor(self) -> None:
        _, root, config = self.make_root()
        package = self.write_agent(root, "alice", "legacy_executor")
        manifest_path = package / "agent.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["executor"] = "executor.py:execute"
        manifest_path.write_text(json.dumps(manifest), "utf-8")
        (package / "executor.py").write_text(
            "from run.agent_runner import AgentRunResult\n\n"
            "def execute(context, input_data):\n"
            "    return AgentRunResult(\n"
            "        context.definition.name,\n"
            "        {'answer': 'schema-v2'},\n"
            "        '',\n"
            "        {},\n"
            "        'user-executor',\n"
            "    )\n",
            "utf-8",
        )

        definition = discover_agents(root, "alice").get("legacy_executor")
        self.assertEqual(definition.executor, "executor.py:execute")
        result = AgentRunner(
            root,
            "alice",
            config=config,
            provider_factory=lambda _: self.fail("自定义 executor 不应调用 Provider"),
        ).run("legacy_executor", {})
        self.assertEqual(result.data, {"answer": "schema-v2"})

    def test_user_executor_still_enforces_schema_and_package_boundary(self) -> None:
        _, root, _ = self.make_root()
        package = self.write_agent(root, "alice", "invalid_executor")
        manifest_path = package / "agent.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))

        manifest["executor"] = "../outside.py:execute"
        manifest_path.write_text(json.dumps(manifest), "utf-8")
        (package.parent / "outside.py").write_text(
            "def execute(context, input_data): pass\n", "utf-8"
        )
        with self.assertRaisesRegex(AgentManifestError, "同目录"):
            discover_agents(root, "alice")

        manifest["executor"] = "missing.py:execute"
        manifest_path.write_text(json.dumps(manifest), "utf-8")
        with self.assertRaisesRegex(AgentManifestError, "文件不存在"):
            discover_agents(root, "alice")

        manifest["schema_version"] = 1
        manifest["executor"] = "builtin:llm"
        manifest_path.write_text(json.dumps(manifest), "utf-8")
        with self.assertRaisesRegex(AgentManifestError, "只支持 schema_version=2"):
            discover_agents(root, "alice")

    def test_dispatch_lists_only_public_agents(self) -> None:
        _, root, _ = self.make_root()
        self.write_agent(root, "alice", "public")
        self.write_agent(root, "alice", "internal", exposure="internal")
        result = dispatch("list", context={"root": str(root), "user": "alice"})
        self.assertEqual([item["name"] for item in result["agents"]], ["public"])
        self.assertEqual(
            dispatch("list", context={"root": str(root), "user": "bob"})["agents"],
            [],
        )

    def test_builtin_agent_callers_match_documented_invocation_paths(self) -> None:
        root = Path(__file__).resolve().parents[2]
        registry = discover_agents(root)
        expected = {
            "self_improve": {"main_agent", "scheduler", "context_manage"},
            "memory_temporary_important": {"main_agent", "scheduler"},
            "task_plan": {"main_agent"},
            "time_plan": {"main_agent"},
        }
        for name, callers in expected.items():
            definition = registry.get(name)
            self.assertEqual(definition.capabilities.exposure, "tool")
            self.assertEqual(set(definition.capabilities.allowed_callers), callers)

        public_names = {item.name for item in registry.public_agents("main_agent")}
        self.assertEqual(public_names, set(expected))
        context_manage = registry.get("context_manage")
        self.assertEqual(context_manage.capabilities.exposure, "internal")
        self.assertEqual(set(context_manage.capabilities.allowed_callers), {"engine"})
        self.assertIn("manual_review", registry.get("self_improve").trigger_content)
        self.assertIn(
            "search_many tier=all",
            registry.get("self_improve").trigger_content,
        )
        self.assertIn(
            "2～4 个空格分隔的核心关键词",
            registry.get("self_improve").trigger_content,
        )
        self.assertIn(
            "单个公共词命中不得直接复用",
            registry.get("self_improve").trigger_content,
        )
        self.assertNotIn(
            "逐条通过 memory_manage 搜索匹配",
            registry.get("self_improve").trigger_content,
        )
        self.assertIn(
            "subagent_dispatch",
            registry.get("memory_temporary_important").trigger_content,
        )
        important_trigger = registry.get("memory_temporary_important").trigger_content
        self.assertIn("include_content=true", important_trigger)
        self.assertIn("page_char_limit=80000", important_trigger)
        self.assertIn("不得再对全部结果逐条 `get`", important_trigger)

    def test_dispatch_create_is_immediately_hot_plugged(self) -> None:
        _, root, _ = self.make_root()
        created = dispatch(
            "create",
            definition={
                "name": "created_agent",
                "description": "created from gateway",
                "instruction": "Return a structured answer.",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            },
            context={"root": str(root), "user": "alice"},
        )
        self.assertEqual(created["status"], "created")
        listed = dispatch("list", context={"root": str(root), "user": "alice"})
        self.assertEqual([item["name"] for item in listed["agents"]], ["created_agent"])
        package = root / "users" / "alice" / "agents" / "created_agent"
        self.assertEqual(
            set(json.loads((package / "agent.json").read_text("utf-8"))),
            {"name", "version", "description", "trigger"},
        )
        self.assertTrue((package / "trigger.md").is_file())
        self.assertIn("# 注册信息", (package / "trigger.md").read_text("utf-8"))
        self.assertEqual(
            dispatch("list", context={"root": str(root), "user": "bob"})["agents"],
            [],
        )

    def test_dispatch_create_rolls_back_invalid_package_atomically(self) -> None:
        _, root, _ = self.make_root()
        with self.assertRaisesRegex(UserAgentPackageError, "包校验失败"):
            dispatch(
                "create",
                definition={
                    "name": "broken_agent",
                    "description": "must be rolled back",
                    "instruction": "Return a structured answer.",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "agent_config": {
                        "schema_version": 1,
                        "tools": {"plugins": {"allow": []}, "max_iterations": 0},
                    },
                },
                context={"root": str(root), "user": "alice"},
            )
        agents_dir = root / "users" / "alice" / "agents"
        self.assertFalse((agents_dir / "broken_agent").exists())
        self.assertEqual(list(agents_dir.iterdir()), [])

    def test_dispatch_create_cannot_override_builtin_agent(self) -> None:
        _, root, _ = self.make_root()
        user_directory = self.write_agent(root, "alice", "reserved")
        user_directory.replace(root / "agents" / "reserved")
        with self.assertRaisesRegex(UserAgentPackageError, "不得覆盖内置名称"):
            dispatch(
                "create",
                definition={
                    "name": "reserved",
                    "description": "attempted override",
                    "instruction": "Return a structured answer.",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                },
                context={"root": str(root), "user": "alice"},
            )
        self.assertFalse((root / "users" / "alice" / "agents" / "reserved").exists())

    def test_new_runtime_does_not_inject_user_skills(self) -> None:
        _, root, config = self.make_root()
        for user in ("alice", "bob"):
            skill = root / "users" / user / "user_skills" / "private"
            skill.mkdir()
            (skill / "SKILL.md").write_text(f"# private\n{user.upper()}_ONLY", "utf-8")
            self.write_agent(root, user, f"{user}_agent", user_skills=["private"])
        alice_provider = ScriptedProvider(
            [ChatResponse(text='{"answer":"a"}', model="mock", usage=Usage())]
        )
        bob_provider = ScriptedProvider(
            [ChatResponse(text='{"answer":"b"}', model="mock", usage=Usage())]
        )
        AgentRunner(
            root, "alice", config=config, provider_factory=lambda _: alice_provider
        ).run("alice_agent", {})
        AgentRunner(
            root, "bob", config=config, provider_factory=lambda _: bob_provider
        ).run("bob_agent", {})
        alice_system = alice_provider.requests[0].messages[0]["content"]
        bob_system = bob_provider.requests[0].messages[0]["content"]
        self.assertNotIn("ALICE_ONLY", alice_system)
        self.assertNotIn("BOB_ONLY", alice_system)
        self.assertNotIn("BOB_ONLY", bob_system)
        self.assertNotIn("ALICE_ONLY", bob_system)

    def test_main_source_policy_does_not_restrict_subagent_capabilities(self) -> None:
        _, root, config = self.make_root()
        config.update(
            {
                "knowledge": {
                    "use_shared": False,
                    "use_global": False,
                },
                "skills": {
                    "shared_whitelist": ["main-denied"],
                },
                "expand": {
                    "global_whitelist": ["main-denied"],
                    "shared_whitelist": ["main-denied"],
                },
            }
        )

        skill = root / "shared_skills" / "child_skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("# child_skill\nCHILD_SKILL", "utf-8")
        (root / "shared_skills" / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    registry.add_skills('shared', Path(__file__).resolve().parent)\n",
            "utf-8",
        )

        expand = root / "global_expand" / "child_expand"
        expand.mkdir()
        (expand / "inject.md").write_text("CHILD_EXPAND", "utf-8")
        (root / "global_expand" / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    base = Path(__file__).resolve().parent\n"
            "    registry.add_expand('global', 'child_expand', base / 'child_expand' / 'inject.md')\n",
            "utf-8",
        )
        (root / "global_knowledge" / "index.md").write_text(
            "# Child Knowledge\nCHILD_KNOWLEDGE", "utf-8"
        )

        directory = self.write_agent(root, "alice", "isolated_policy")
        agent_config_path = directory / "agent-config.json"
        agent_config = json.loads(agent_config_path.read_text("utf-8"))
        agent_config["prompt_sources"]["skills"]["shared"] = ["child_skill"]
        agent_config["prompt_sources"]["expand"]["global"] = ["child_expand"]
        agent_config["knowledge"] = {
            "scopes": ["global"],
            "index_enabled": True,
        }
        agent_config_path.write_text(json.dumps(agent_config), "utf-8")

        provider = ScriptedProvider(
            [ChatResponse(text='{"answer":"isolated"}', model="mock", usage=Usage())]
        )
        AgentRunner(
            root,
            "alice",
            config=config,
            provider_factory=lambda _: provider,
        ).run("isolated_policy", {})

        system_prompt = provider.requests[0].messages[0]["content"]
        self.assertIn("CHILD_SKILL", system_prompt)
        self.assertNotIn("CHILD_EXPAND", system_prompt)
        self.assertIn("CHILD_KNOWLEDGE", system_prompt)

    def test_agent_tool_whitelist_drives_provider_tool_loop(self) -> None:
        _, root, config = self.make_root()
        self.write_plugin(root, "echo")
        self.write_plugin(root, "denied")
        self.write_agent(root, "alice", "tool_agent", tools=["echo"])
        provider = ScriptedProvider(
            [
                ChatResponse(
                    text="",
                    tool_calls=[ToolCall("call-1", "echo", {"value": "x"})],
                    usage=Usage(1, 1, 2),
                ),
                ChatResponse(
                    text='{"answer":"done"}', model="mock", usage=Usage(1, 1, 2)
                ),
            ]
        )
        result = AgentRunner(
            root, "alice", config=config, provider_factory=lambda _: provider
        ).run("tool_agent", {})
        self.assertEqual(result.data["answer"], "done")
        self.assertEqual(
            [item["function"]["name"] for item in provider.requests[0].tools],
            ["echo"],
        )
        self.assertEqual(provider.requests[1].messages[-1]["role"], "tool")
        self.assertEqual(result.metadata["tool_calls"][0]["status"], "completed")

    def test_global_tool_call_limit_is_a_hard_ceiling_for_subagents(self) -> None:
        _, root, config = self.make_root()
        config["tools"]["max_iterations"] = 1
        self.write_plugin(root, "echo")
        self.write_agent(root, "alice", "limited_agent", tools=["echo"])
        provider = ScriptedProvider(
            [
                ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall("call-1", "echo", {"value": "x"}),
                        ToolCall("call-2", "echo", {"value": "y"}),
                    ],
                    usage=Usage(),
                )
            ]
        )

        with self.assertRaisesRegex(AgentRunError, "最大工具调用次数 1"):
            AgentRunner(
                root,
                "alice",
                config=config,
                provider_factory=lambda _: provider,
            ).run("limited_agent", {})

    def test_agent_blocks_only_consecutive_identical_tool_arguments(self) -> None:
        _, root, config = self.make_root()
        config["tools"]["consecutive_identical_call_limit"] = 2
        self.write_plugin(root, "echo")
        directory = self.write_agent(root, "alice", "repeat_agent", tools=["echo"])
        agent_config_path = directory / "agent-config.json"
        agent_config = json.loads(agent_config_path.read_text("utf-8"))
        agent_config["tools"]["max_iterations"] = 6
        agent_config_path.write_text(json.dumps(agent_config), "utf-8")
        provider = ScriptedProvider(
            [
                ChatResponse(
                    text="", tool_calls=[ToolCall("same-1", "echo", {"value": "x"})]
                ),
                ChatResponse(
                    text="", tool_calls=[ToolCall("same-2", "echo", {"value": "x"})]
                ),
                ChatResponse(
                    text="", tool_calls=[ToolCall("same-3", "echo", {"value": "x"})]
                ),
                ChatResponse(
                    text="", tool_calls=[ToolCall("changed", "echo", {"value": "y"})]
                ),
                ChatResponse(text='{"answer":"done"}', model="mock", usage=Usage()),
            ]
        )
        result = AgentRunner(
            root,
            "alice",
            config=config,
            provider_factory=lambda _: provider,
        ).run("repeat_agent", {})
        calls = result.metadata["tool_calls"]
        self.assertEqual(
            [call["status"] for call in calls],
            ["completed", "completed", "identical_call_blocked", "completed"],
        )
        self.assertEqual(calls[2]["consecutive_identical_calls"], 3)
        self.assertEqual(calls[3]["consecutive_identical_calls"], 1)

    def test_background_scheduler_detects_hot_plugged_agent(self) -> None:
        _, root, config = self.make_root()
        provider = ScriptedProvider(
            [ChatResponse(text='{"answer":"queued"}', model="mock", usage=Usage())]
        )
        runner = AgentRunner(
            root, "alice", config=config, provider_factory=lambda _: provider
        )
        scheduler = AgentScheduler.from_runner(runner)
        try:
            self.write_agent(
                root,
                "alice",
                "queued_agent",
                execution="background_serial",
            )
            task_id = scheduler.submit("queued_agent", {})
            result = scheduler.wait(task_id, timeout=2)
        finally:
            scheduler.close()
        self.assertEqual(result.data, {"answer": "queued"})


if __name__ == "__main__":
    unittest.main()
