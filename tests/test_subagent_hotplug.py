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
from run.agent_runner import AgentRunner
from run.agents import AgentDisabledError, AgentError, AgentManifestError, discover_agents


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
            "context": {"inherit_main_history": False, "inherit_current_request": False},
        }
        (directory / "agent.json").write_text(json.dumps(manifest), "utf-8")
        (directory / "agent-config.json").write_text(json.dumps(capabilities), "utf-8")
        (directory / "AGENT.md").write_text(f"# {name}\n{user} agent instruction", "utf-8")
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
        runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
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

    def test_user_agents_are_isolated_and_python_is_rejected(self) -> None:
        _, root, _ = self.make_root()
        directory = self.write_agent(root, "alice", "private")
        self.assertIn("private", discover_agents(root, "alice").agents)
        self.assertNotIn("private", discover_agents(root, "bob").agents)
        (directory / "executor.py").write_text("raise RuntimeError('must not execute')", "utf-8")
        with self.assertRaisesRegex(AgentManifestError, "不得包含 Python"):
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
        AgentRunner(root, "alice", config=config, provider_factory=lambda _: alice_provider).run(
            "alice_agent", {}
        )
        AgentRunner(root, "bob", config=config, provider_factory=lambda _: bob_provider).run(
            "bob_agent", {}
        )
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
                ChatResponse(text='{"answer":"done"}', model="mock", usage=Usage(1, 1, 2)),
            ]
        )
        result = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
            "tool_agent", {}
        )
        self.assertEqual(result.data["answer"], "done")
        self.assertEqual(
            [item["function"]["name"] for item in provider.requests[0].tools],
            ["echo"],
        )
        self.assertEqual(provider.requests[1].messages[-1]["role"], "tool")
        self.assertEqual(result.metadata["tool_calls"][0]["status"], "completed")

    def test_background_scheduler_detects_hot_plugged_agent(self) -> None:
        _, root, config = self.make_root()
        provider = ScriptedProvider(
            [ChatResponse(text='{"answer":"queued"}', model="mock", usage=Usage())]
        )
        runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
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
