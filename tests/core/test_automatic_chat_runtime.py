"""Use the real provider factory and real tool loop with an offline HTTP peer."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from run.agents import AgentRunner
from provider.request_scope import current_request_scope
from run.engine import iter_request_events
from run.history import find_window, load_window
from tests.media_input_support import make_root, PROJECT
from tests.provider.test_chat_profiles import response


def workspace(root: Path, model: str, stream: bool = False) -> dict[str, Any]:
    cfg, _ = make_root(root)
    cfg["provider"].update({"model": model, "stream": stream,
                            "base_url": "https://relay.test/proxy/api"})
    cfg["provider"].pop("input_modalities", None)
    for path in (root / "config/global_config.json", root / "users/alice/user_config.json"):
        path.write_text(json.dumps(cfg), encoding="utf-8")
    tool = root / "plugins/echo"
    tool.mkdir()
    manifest = {"name": "echo", "description": "Return the supplied value", "version": "1.0.0",
                "enabled": True, "entrypoint": "tool.py:run", "input_schema": {
                    "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}
    (tool / "SKILL.md").write_text(f"# echo\n\n## Tool\n```json\n{json.dumps(manifest)}\n```\n", encoding="utf-8")
    (tool / "tool.py").write_text("def run(value, *, context):\n    return {'value': value}\n", encoding="utf-8")
    return cfg


@pytest.mark.parametrize("model", ["generic-chat", "deepseek-flash"])
@pytest.mark.parametrize("stream", [False, True])
def test_default_factory_chat_tool_execution_and_history_without_capabilities(tmp_path: Path, model: str, stream: bool) -> None:
    workspace(tmp_path, model, stream)
    seen: list[dict[str, Any]] = []

    def endpoint(req: Any, **kwargs: Any):
        body = json.loads(req.data)
        seen.append(body)
        assert current_request_scope()[-3:] == ("alice", "web", "auto-chat")
        assert req.full_url == "https://relay.test/proxy/api/chat/completions"
        assert any(t["function"]["name"] == "echo" for t in body["tools"])
        if model == "deepseek-flash":
            assert body["thinking"]["type"] == "enabled" and body["reasoning_effort"] == "high"
        else:
            assert "reasoning_enabled" not in body and "reasoning_effort" not in body
        if len(seen) == 1:
            return response({"reasoning_content": "Need lookup" if model == "deepseek-flash" else "",
                             "tool_calls": [{"index": 0, "id": "first", "type": "function", "function": {
                                 "name": "echo", "arguments": '{"value":"checked"}'}}]}, stream=stream, tool=True)
        assert body["messages"][-1]["role"] == "tool"
        assert "checked" in body["messages"][-1]["content"]
        if model == "deepseek-flash":
            assert next(m for m in body["messages"] if m.get("tool_calls"))["reasoning_content"] == "Need lookup"
        return response({"content": "Finished"}, stream=stream)

    with patch("urllib.request.urlopen", side_effect=endpoint):
        events = list(iter_request_events({"user": "alice", "source": "web", "session_id": "auto-chat",
                                           "prompt": "Echo checked"}, root=tmp_path))
    assert len(seen) == 2
    assert any(event.type == "tool_call_result" for event in events)
    assert events[-1].type == "done" and events[-1].metadata.get("status", "completed") == "completed"
    path = find_window(tmp_path, "alice", "web", "auto-chat")
    assert path is not None
    assert load_window(path)["text"]["messages"][-1]["content"] == "Finished"


@pytest.mark.parametrize("model", ["deepseek-flash", "generic-cheap-model"])
def test_real_subagent_factory_resolves_its_model_without_manual_detection(tmp_path: Path, model: str) -> None:
    cfg = workspace(tmp_path, "deepseek-flash")
    shutil.copytree(PROJECT / "agents/general_worker", tmp_path / "agents/general_worker")
    seen: list[dict[str, Any]] = []
    final = {"status": "completed", "summary": "Checked", "results": ["done"],
             "verification": ["echo checked"], "remaining": [], "artifacts": []}

    def endpoint(req: Any, **kwargs: Any):
        body = json.loads(req.data)
        seen.append(body)
        assert current_request_scope()[-3:] == ("alice", "web", "child-auto")
        assert body["model"] == model
        if model == "generic-cheap-model":
            assert "reasoning_effort" not in body
        else:
            assert body["thinking"]["type"] == "enabled"
        if len(seen) == 1:
            return response({"reasoning_content": "Lookup" if model == "deepseek-flash" else "",
                             "tool_calls": [{"id": "child-call", "type": "function", "function": {
                                 "name": "echo", "arguments": '{"value":"ok"}'}}]}, stream=False, tool=True)
        return response({"content": json.dumps(final)}, stream=False)

    with patch("urllib.request.urlopen", side_effect=endpoint):
        result = AgentRunner(tmp_path, "alice", config=cfg).run(
            "general_worker", {"task": "Call echo and report"}, model_override=model,
            source="web", session_id="child-auto")
    assert result.data == final and len(seen) == 2
    assert "capabilities" not in cfg["provider"]


@pytest.mark.parametrize("model", ["deepseek-flash", "generic-chat"])
@pytest.mark.parametrize("agent", ["history_summary", "self_improve"])
def test_saved_conversation_agents_use_real_chat_structured_tool(
    tmp_path: Path, model: str, agent: str,
) -> None:
    cfg = workspace(tmp_path, model, stream=True)
    shutil.copytree(PROJECT / "agents" / agent, tmp_path / "agents" / agent)
    if agent == "self_improve":
        for plugin in ("memory_manage", "skill_creater"):
            shutil.copytree(PROJECT / "plugins" / plugin, tmp_path / "plugins" / plugin)
    rounds = [{"round": 1, "user": "Please remember the project uses Python.",
               "assistant": "Noted."}]
    payload = (
        {"trigger": "session_closed", "session_id": "saved-chat", "target_round": 1, "rounds": rounds}
        if agent == "history_summary" else
        {"trigger": "context_compression", "rounds": rounds}
    )
    final = (
        {"title": "Saved Python project",
         "summary": "The user stated that the project uses Python and the assistant acknowledged it."}
        if agent == "history_summary" else {"candidates": []}
    )
    seen: list[dict[str, Any]] = []

    def endpoint(req: Any, **kwargs: Any):
        body = json.loads(req.data)
        seen.append(body)
        assert current_request_scope()[-3:] == ("alice", "web", "saved-chat")
        assert req.full_url == "https://relay.test/proxy/api/chat/completions"
        assert body["model"] == model
        tools = [tool["function"]["name"] for tool in body["tools"]]
        assert "submit_structured_output" in tools
        # Generic Chat does not advertise strict JSON-schema responses. These
        # maintenance agents must use ordinary function calling instead.
        assert "response_format" not in body
        return response({"tool_calls": [{
            "id": "maintenance-result", "type": "function",
            "function": {"name": "submit_structured_output", "arguments": json.dumps(final)},
        }]}, stream=False, tool=True)

    with patch("urllib.request.urlopen", side_effect=endpoint):
        result = AgentRunner(tmp_path, "alice", config=cfg).run(
            agent, payload, structured_output_tool=True, source="web", session_id="saved-chat",
        )
    assert result.data == final
    assert len(seen) == 1
