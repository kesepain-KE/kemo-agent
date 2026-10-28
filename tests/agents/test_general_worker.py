"""General worker permissions and fake-provider integration; no live models."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agents._runtime.resources import (
    build_agent_prompt_bundle,
    build_agent_tool_registry,
    effective_knowledge_scopes,
)
from plugins.expand_call.tool import run as expand_call
from plugins.subagent_dispatch.tool import run as dispatch
from provider.schema import ChatResponse, ToolCall
from run.agents import (
    AgentError, AgentInputError, AgentManifestError, AgentRunner, AgentScheduler, discover_agents,
)
from run.agents import ExternalAgentError, validate_json_schema
from tests.agents.test_subagent_hotplug import ScriptedProvider


REPOSITORY = Path(__file__).resolve().parents[2]
OUTPUT = {
    "status": "completed", "summary": "done", "results": ["processed"],
    "verification": ["checked"], "remaining": [], "artifacts": [],
}


@pytest.fixture
def workspace(tmp_path):
    for name in (
        "agents", "plugins", "config", "shared_skills", "global_knowledge",
        "shared_knowledge", "global_expand", "shared_expand", "global_sense",
    ):
        (tmp_path / name).mkdir()
    for user in ("alice", "bob"):
        for name in ("agents", "user_skills", "expand", "knowledge", "download"):
            (tmp_path / "users" / user / name).mkdir(parents=True)
        (tmp_path / "users" / user / "user_config.json").write_text("{}", "utf-8")
    shutil.copytree(REPOSITORY / "agents/general_worker", tmp_path / "agents/general_worker")
    config = {
        "provider": {
            "type": "kemo", "base_url": "http://127.0.0.1:1",
            "api_key": "fake", "model": "mock", "stream": False,
        },
        "tools": {"timeout": 3},
    }
    (tmp_path / "config/global_config.json").write_text(json.dumps(config), "utf-8")
    (tmp_path / "users/alice/user_config.json").write_text(json.dumps(config), "utf-8")
    (tmp_path / "shared_skills/register.py").write_text(
        "from pathlib import Path\n"
        "def register(registry):\n"
        "    registry.add_skills('shared', Path(__file__).resolve().parent)\n", "utf-8",
    )
    return tmp_path, config


def plugin(root, name, *, enabled=True):
    directory = root / "plugins" / name
    directory.mkdir()
    tool = {
        "name": name, "description": name, "version": "1",
        "enabled": enabled, "entrypoint": "tool.py:run",
        "input_schema": {"type": "object", "additionalProperties": False},
    }
    (directory / "SKILL.md").write_text(
        f"# {name}\n{name}\n\n## Tool\n```json\n{json.dumps(tool)}\n```\n", "utf-8"
    )
    (directory / "tool.py").write_text("def run(*, context): return {'ok': True}\n", "utf-8")


def skill(path, marker):
    path.mkdir()
    (path / "SKILL.md").write_text(f"# {marker}\n{marker}\n", "utf-8")


def test_discovery_defaults_and_strict_input(workspace):
    root, _ = workspace
    definition = discover_agents(root, "alice").get("general_worker")
    assert definition in discover_agents(root, "alice").public_agents("main_agent")
    assert definition.model_profile == "default"
    assert definition.executor == "builtin:llm"
    assert definition.execution == "sync"
    assert definition.capabilities.max_tool_iterations == 80
    assert not definition.capabilities.inherit_main_history
    assert not definition.capabilities.inherit_current_request
    validate_json_schema({"task": "read files"}, definition.input_schema)
    validate_json_schema(OUTPUT, definition.output_schema)
    for payload in ({}, {"task": ""}, {"task": "   "}, {"task": "a", "allowed_tools": ["*"]}):
        with pytest.raises(AgentInputError):
            validate_json_schema(payload, definition.input_schema)


def test_wildcard_plugins_follow_user_policy_disable_and_hotplug(workspace):
    root, config = workspace
    for name in ("first", "second", "subagent_dispatch"):
        plugin(root, name)
    plugin(root, "disabled", enabled=False)
    definition = discover_agents(root).get("general_worker")
    assert set(build_agent_tool_registry(root, "alice", definition, config).tools) == {"first", "second"}
    config["plugins"] = {"whitelist": ["first", "subagent_dispatch"]}
    assert set(build_agent_tool_registry(root, "alice", definition, config).tools) == {"first"}
    plugin(root, "new_plugin")
    config["plugins"] = {"whitelist": []}
    assert set(build_agent_tool_registry(root, "alice", definition, config).tools) == {
        "first", "second", "new_plugin",
    }


def test_skills_paths_user_isolation_and_knowledge_policy(workspace):
    root, config = workspace
    skill(root / "shared_skills/allowed", "SHARED_ALLOWED")
    skill(root / "shared_skills/denied", "SHARED_DENIED")
    skill(root / "users/alice/user_skills/private", "ALICE_PRIVATE")
    skill(root / "users/bob/user_skills/private", "BOB_PRIVATE")
    for scope, directory in (
        ("global", root / "global_knowledge"), ("shared", root / "shared_knowledge"),
        ("user", root / "users/alice/knowledge"),
    ):
        (directory / "index.md").write_text(f"# {scope.upper()}_KNOWLEDGE\n", "utf-8")
    config["skills"] = {"shared_whitelist": ["allowed"]}
    definition = discover_agents(root).get("general_worker")
    bundle = build_agent_prompt_bundle(root, "alice", definition, config)
    assert "SHARED_ALLOWED" in bundle.text
    assert "SHARED_DENIED" not in bundle.text
    assert "ALICE_PRIVATE" in bundle.text
    assert "BOB_PRIVATE" not in bundle.text
    assert "users/alice/user_skills/private/SKILL.md" in bundle.text
    assert "shared_skills/allowed/SKILL.md" in bundle.text
    assert set(bundle.diagnostics["knowledge_scopes"]) == {"global", "shared", "user"}
    config["knowledge"] = {"use_global": False, "use_shared": False}
    restricted = build_agent_prompt_bundle(root, "alice", definition, config)
    assert restricted.diagnostics["knowledge_scopes"] == ["user"]
    assert effective_knowledge_scopes(definition, config) == ("user",)
    assert "GLOBAL_KNOWLEDGE" not in restricted.text
    assert "SHARED_KNOWLEDGE" not in restricted.text
    assert "USER_KNOWLEDGE" in restricted.text


def test_no_expand_or_perception_injection_even_when_main_enables_it(workspace, monkeypatch):
    root, config = workspace
    from run.config import PromptSourceRegistry

    def forbidden(*args, **kwargs):
        pytest.fail("Subagent must not select Expand or perception injection")

    monkeypatch.setattr(PromptSourceRegistry, "select_expand", forbidden)
    monkeypatch.setattr(PromptSourceRegistry, "select_perception", forbidden)
    config["expand"] = {"prompt_injection": True, "realtime_injection": True}
    config["perception"] = {"prompt_injection": True, "realtime_injection": True}
    bundle = build_agent_prompt_bundle(
        root, "alice", discover_agents(root).get("general_worker"), config,
    )
    assert not {"expand", "expand_data", "perception"} & set(bundle.section_order)


def test_string_pattern_validation_reports_bad_input_and_bad_schema():
    validate_json_schema("a", {"type": "string", "pattern": "\\S"})
    with pytest.raises(AgentInputError, match="不符合 pattern"):
        validate_json_schema(" ", {"type": "string", "pattern": "\\S"})
    with pytest.raises(AgentInputError, match="Schema pattern 无效"):
        validate_json_schema("a", {"type": "string", "pattern": "["})


@pytest.mark.parametrize("action", ["list", "call", "create", "status", "cancel"])
def test_dispatch_refuses_trusted_child_context_before_any_side_effect(action):
    with pytest.raises(AgentError, match="不能创建、调度或管理"):
        dispatch(action, context={"caller": "subagent"})


def test_expand_allows_normal_command_but_rejects_declared_agent_bridge(workspace, monkeypatch):
    root, _ = workspace
    directory = root / "users/alice/expand/demo"
    directory.mkdir()
    (directory / "expand.json").write_text(json.dumps({
        "name": "demo", "explain": "demo", "open_input": False,
        "input_data": "input_data.md", "input_health": "正常", "start_update": "data_update.py",
        "open_control": True, "start_expand": "start_expand.py", "start_control": "expand_control.md",
    }), "utf-8")
    (directory / "expand_control.md").write_text("normal and agent_call", "utf-8")
    (directory / "start_expand.py").write_text("def execute(command, params): return {}\n", "utf-8")
    path = directory / "agent_bridge.json"
    path.write_text(json.dumps({"schema_version": 1, "agents": [{
        "name": "remote", "description": "remote", "command": "agent_call",
    }]}), "utf-8")
    calls = []

    def invoke(**kwargs):
        calls.append(kwargs)
        return {"result": {"ok": True}}

    monkeypatch.setattr("plugins.expand_call.tool.invoke_expand", invoke)
    context = {"root": str(root), "user": "alice", "caller": "subagent", "tool_timeout": 3}
    assert expand_call("user", "demo", "normal", context=context)["result"]["ok"]
    with pytest.raises(ExternalAgentError, match="不能通过拓展调度"):
        expand_call("user", "demo", "agent_call", context=context)
    assert len(calls) == 1
    # Main agent remains authorized to use an external binding.
    expand_call("user", "demo", "agent_call", context={**context, "caller": "main_agent"})
    assert len(calls) == 2
    path.write_text("not json", "utf-8")
    with pytest.raises(ExternalAgentError, match="JSON"):
        expand_call("user", "demo", "agent_call", context=context)
    assert len(calls) == 2


def test_real_file_tool_roundtrip_and_no_history_inheritance(workspace):
    root, config = workspace
    shutil.copytree(REPOSITORY / "plugins/file", root / "plugins/file")
    target = root / "users/alice/download/report.txt"
    provider = ScriptedProvider([
        ChatResponse(text="", tool_calls=[
            ToolCall("write", "file", {"action": "write", "path": str(target), "content": "verified"}),
        ]),
        ChatResponse(text="", tool_calls=[
            ToolCall("read", "file", {"action": "read", "path": str(target)}),
        ]),
        ChatResponse(text=json.dumps({
            **OUTPUT, "artifacts": [{
                "scope": "download", "relative_path": "report.txt", "name": "report.txt",
            }],
        }), model="mock"),
    ])
    result = AgentRunner(
        root, "alice", config=config, provider_factory=lambda _: provider,
    ).run("general_worker", {"task": "Write and read report", "context": "explicit-only"})
    assert target.read_text("utf-8") == "verified"
    assert result.data["artifacts"][0]["relative_path"] == "report.txt"
    assert [item["status"] for item in result.metadata["tool_calls"]] == ["completed", "completed"]
    assert len(provider.requests) == 3
    assert "explicit-only" in provider.requests[0].messages[-1]["content"]


def test_background_dispatch_accepts_sync_worker_and_returns_result(workspace, monkeypatch):
    root, config = workspace
    provider = ScriptedProvider([ChatResponse(text=json.dumps(OUTPUT), model="mock")])
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    scheduler = AgentScheduler(runner)
    monkeypatch.setattr("plugins.subagent_dispatch.tool.get_agent_scheduler", lambda *a, **kw: scheduler)
    context = {"root": str(root), "user": "alice", "source": "web", "session_id": "test"}
    try:
        response = dispatch(
            "call", agent="general_worker", wait=False, input={"task": "check"}, context=context,
        )
        assert response["status"] == "queued"
        result = scheduler.wait(response["task_id"], timeout=5, source="web", session_id="test")
        assert result.data == OUTPUT
        snapshot = scheduler.get(response["task_id"], source="web", session_id="test")
        assert snapshot["status"] == "completed"
    finally:
        scheduler.close()


@pytest.mark.parametrize("field", ["user_knowledge", "global_knowledge", "shared_knowledge"])
def test_new_boolean_permissions_reject_non_boolean(workspace, field):
    root, _ = workspace
    path = root / "agents/general_worker/agent-config.json"
    raw = json.loads(path.read_text("utf-8"))
    raw[field] = "true"
    path.write_text(json.dumps(raw), "utf-8")
    with pytest.raises(AgentManifestError, match="布尔值"):
        discover_agents(root)
