"""The diagnostic preview and context window describe the same runtime input."""
import pytest

from run.cards import CardStore, estimate_tokens, prepare_request
from run.config import build_prompt_bundle, load_config, read_dynamic_tail
from run.context import estimate_text_tokens
from run.context.service import context_status
from run.tools import ToolRegistry, apply_runtime_tool_policy, discover_tools
from tests.cards.test_runtime import root
from tests.core.test_preview_runtime_safety import tool
from web.service import WebRunService


@pytest.mark.parametrize("tools_enabled", [True, False])
def test_prompt_preview_matches_request_barrier_and_context_window(root, tools_enabled):
    import json
    path = root / "config/global_config.yaml"
    config = json.loads(path.read_text("utf-8"))
    (root / "plugins").mkdir()
    tool(root, "visible_plugin_identifier")
    tool(root, "excluded_plugin_identifier")
    config["tools"] = {"enabled": tools_enabled}
    path.write_text(json.dumps(config), "utf-8")
    user_path = root / "users/alice/user_config.yaml"
    user_config = json.loads(user_path.read_text("utf-8"))
    user_config["plugins"] = {"whitelist": ["visible_plugin_identifier"]}
    user_path.write_text(json.dumps(user_config), "utf-8")
    (root / "agents.md").write_text("中文系统提示词。" * 500 + "ASCII prompt " * 100, "utf-8")
    config = load_config("alice", root)
    registry = (apply_runtime_tool_policy(discover_tools(root, "alice"), config)
                if tools_enabled else ToolRegistry({}))
    assert set(registry.tools) == ({"visible_plugin_identifier"} if tools_enabled else set())
    bundle = build_prompt_bundle(root, "alice", config, plugin_manifests=registry.plugin_manifests,
                                 source="web", session_id="s1")
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    barrier = prepare_request(
        store, "web", "s1", system_prompt=bundle.text,
        dynamic_tail=lambda: read_dynamic_tail(root, "alice", config, source="web", session_id="s1"),
        tools=[tool.model_dump(mode="json", exclude_none=True) for tool in registry.protocol_tools()],
        input_limit=999936,
    )
    service = WebRunService(root)
    diagnostic = service.runtime_status("alice", session_id="s1", sections=["summary", "prompt"])
    prompt = diagnostic["prompt"]
    context = diagnostic["context"]["context_snapshot"]
    overview = service.overview("alice", session_id="s1")["context_window"]["tokens"]
    assert prompt["content"] == bundle.text
    assert "excluded_plugin_identifier" not in prompt["content"]
    assert ("visible_plugin_identifier" in prompt["content"]) is tools_enabled
    assert prompt["estimated_tokens"] == context["system_prompt_tokens"] == (
        overview["system_prompt_tokens"]) == barrier.occupancy.fixed_tokens
    assert prompt["estimated_tokens"] == estimate_text_tokens(bundle.text)
    assert context["total_tokens"] == overview["total_tokens"] == barrier.occupancy.total
    assert prompt["measurement"] == context["measurement"] == "local_estimate"
    for section, component in zip(bundle.sections, prompt["components"], strict=True):
        assert component["tokens"] == (estimate_tokens(section.content) if section.content else 0)
    assert context_status(
        {"user": "alice", "source": "web", "session_id": "s1"}, root=root,
    )["context"]["fixed_tokens"] == prompt["estimated_tokens"]
