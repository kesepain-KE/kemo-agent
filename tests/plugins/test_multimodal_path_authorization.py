"""R1 3.6: dedicated media paths share native attachment authorization."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from unittest.mock import Mock

import pytest

from plugins.multimodal import tool
from provider.protocol.models import ToolCallItem, ToolResultItem
from provider.schema import ChatResponse, ToolCall
from run.agents import AgentRunner
from run.engine import handle_request
from run.extensions import AttachmentError
from tests.media_input_support import PNG, PROJECT, MediaProvider, make_root


def setup_media(root, monkeypatch, *, provider_type="chat"):
    config, image = make_root(root, provider_type=provider_type)
    config["multimodal_models"] = {"vision": "dedicated-vision"}
    path = root / "config/global_config.yaml"
    path.write_text(json.dumps(config), encoding="utf-8")
    user_path = root / "users/alice/user_config.yaml"
    user_config = json.loads(user_path.read_text(encoding="utf-8"))
    user_config["multimodal_models"] = config["multimodal_models"]
    user_path.write_text(json.dumps(user_config), encoding="utf-8")
    dedicated = MediaProvider([ChatResponse(text="offline dedicated analysis")])
    factory = Mock(return_value=dedicated)
    monkeypatch.setattr(tool, "create_provider", factory)
    return config, image, dedicated, factory


def context(root, **extra):
    return {"root": str(root), "user": "alice", "source": "web",
            "session_id": "path-auth", "uploaded_files": [], **extra}


@pytest.mark.parametrize("directory", ["users/alice/download", "users/alice/file_upload"])
def test_own_managed_path_is_allowed_without_mention(tmp_path, monkeypatch, directory):
    _, _, dedicated, _ = setup_media(tmp_path, monkeypatch)
    image = tmp_path / directory / "image.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG)
    result = tool.run("analyze_image", paths=[str(image)], instruction="inspect",
                      context=context(tmp_path))
    assert result["ok"] is True and len(dedicated.requests) == 1


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
@pytest.mark.parametrize("source", ["unmentioned", "failed_result", "argument_only", "other_user"])
def test_untrusted_paths_rejected_before_provider_construction(tmp_path, monkeypatch, provider_type, source):
    _, _, dedicated, factory = setup_media(tmp_path, monkeypatch, provider_type=provider_type)
    image = tmp_path / ("users/bob/download/private.png" if source == "other_user" else "private.png")
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG)
    extra = {}
    if source == "failed_result":
        extra["tool_records"] = [{"result": {"ok": False, "result": {"path": str(image)}}}]
    elif source == "argument_only":
        extra["tool_records"] = [{"arguments": {"path": str(image)}, "result": {"ok": True, "result": {}}}]
    elif source == "other_user":
        extra["user_text"] = str(image)  # Mentioning another user never grants access.
    with pytest.raises(AttachmentError):
        tool.run("analyze_image", paths=[str(image)], instruction=f"inspect {image}",
                 context=context(tmp_path, **extra))
    factory.assert_not_called()
    assert not dedicated.requests and not dedicated.uploads


@pytest.mark.parametrize("authorization", ["user_text", "successful_tool"])
def test_outside_managed_directory_needs_actual_user_or_successful_tool_authority(tmp_path, monkeypatch, authorization):
    _, _, dedicated, _ = setup_media(tmp_path, monkeypatch)
    image = tmp_path / "captured.png"
    image.write_bytes(PNG)
    extra = (
        {"user_text": f"请查看 {image}"}
        if authorization == "user_text" else
        {"tool_records": [{"result": {"ok": True, "result": {"path": str(image)}}}]}
    )
    result = tool.run("analyze_image", paths=[str(image)], instruction="inspect",
                      context=context(tmp_path, **extra))
    assert result["ok"] is True and len(dedicated.requests) == 1


@pytest.mark.parametrize("directory", ["users/alice/download", "outside"])
def test_parent_junction_rejected_even_with_user_mention(tmp_path, monkeypatch, directory):
    _, _, _, factory = setup_media(tmp_path, monkeypatch)
    parent = tmp_path / directory / "junction"
    parent.mkdir(parents=True)
    image = parent / "screen.png"
    image.write_bytes(PNG)
    original = getattr(Path, "is_junction", lambda self: False)
    monkeypatch.setattr(Path, "is_junction", lambda self: self == parent or original(self), raising=False)
    with pytest.raises(AttachmentError, match="符号链接|目录联接"):
        tool.run("analyze_image", paths=[str(image)], instruction="inspect",
                 context=context(tmp_path, user_text=str(image)))
    factory.assert_not_called()


def vision_call(image):
    return ToolCall(id="callid_vision", name="multimodal", arguments={
        "action": "analyze_image", "paths": [str(image)], "instruction": "inspect",
    })


@pytest.mark.parametrize("caller", ["main", "subagent"])
@pytest.mark.parametrize("authorization", ["mentioned", "successful_tool", "failed_tool", "absent"])
def test_real_tool_context_uses_only_current_run_authorization(tmp_path, monkeypatch, caller, authorization):
    config, _, dedicated, factory = setup_media(tmp_path, monkeypatch)
    # Thread mode lets this offline test use the in-memory dedicated provider.
    shutil.copytree(PROJECT / "plugins/multimodal", tmp_path / "plugins/multimodal")
    manifest_path = tmp_path / "plugins/multimodal/plugin.yaml"
    import yaml
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["execution_mode"] = "thread"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    from run.tools import discover_tools
    registry = discover_tools(tmp_path, "alice")
    registry.tools["multimodal"]._callable = tool.run
    image = tmp_path / "captured.png"
    image.write_bytes(PNG)
    prompt = f"inspect {image}" if authorization == "mentioned" else "inspect the screenshot"
    responses = []
    if authorization in {"successful_tool", "failed_tool"}:
        from run.tools import ToolDefinition
        registry.tools["capture"] = ToolDefinition(
            name="capture", description="offline capture", input_schema={"type": "object"},
            version="1", enabled=True, source="test", directory=tmp_path,
            entrypoint="capture.py:run", execution_mode="thread",
            _callable=lambda: {"ok": authorization == "successful_tool", "path": str(image)},
        )
        responses.append(ChatResponse(text="", tool_calls=[
            ToolCall(id="callid_capture", name="capture", arguments={}),
        ]))
    responses.extend([
        ChatResponse(text="", tool_calls=[vision_call(image)]),
        ChatResponse(text=json.dumps({
            "status": "completed",
            "summary": "finished",
            "results": [],
            "verification": [],
            "remaining": [],
            "artifacts": [],
        }) if caller == "subagent" else "finished"),
    ])
    main = MediaProvider(responses)
    if caller == "main":
        handle_request({"user": "alice", "source": "web", "session_id": "path-auth", "prompt": prompt},
                       root=tmp_path, provider_factory=lambda _: main,
                       tool_registry_factory=lambda *_: registry)
    else:
        shutil.copytree(PROJECT / "agents/general_worker", tmp_path / "agents/general_worker")
        agent_config = tmp_path / "agents/general_worker/agent-config.json"
        value = json.loads(agent_config.read_text(encoding="utf-8"))
        value["tools"]["plugins"]["allow"] = ["multimodal", "capture"]
        agent_config.write_text(json.dumps(value), encoding="utf-8")
        monkeypatch.setattr("run.agents.runner.build_agent_tool_registry", lambda *_: registry)
        runner = AgentRunner(tmp_path, "alice", config=config, provider_factory=lambda _: main)
        runner.run(
            "general_worker",
            {"task": prompt},
            task_id="path-auth",
            source="web",
            session_id="path-auth",
        )
    vision_ids = {item.call_id for item in main.requests[-1].input
                  if isinstance(item, ToolCallItem) and item.name == "multimodal"}
    results = [item for item in main.requests[-1].input
               if isinstance(item, ToolResultItem) and item.call_id in vision_ids]
    assert len(results) == 1, [(item.type, getattr(item, "call_id", None)) for item in main.requests[-1].input]
    allowed = authorization in {"mentioned", "successful_tool"}
    assert len(dedicated.requests) == (1 if allowed else 0)
    assert factory.call_count == (1 if allowed else 0)
