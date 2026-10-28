"""Large images remain native, lossless and isolated across main/agent calls."""

import base64
import hashlib
import json
import random
import shutil

import pytest
from PIL import Image
from pydantic import ValidationError

from provider.adapters.chat_bridge import ChatBridgeProvider
from provider.adapters.compat import kemo_request_to_chat
from provider.protocol.models import ImageContent, MAX_INLINE_IMAGE_BYTES, ToolResultItem
from provider.schema import ChatResponse, ProviderError, ToolCall
from run.agents import AgentRunner
from run.engine import handle_request
from run.extensions import AttachmentError, describe_local_asset
from run.history import find_window, load_window
from tests.media_input_support import PROJECT, MediaProvider, enqueue, make_host, make_root, native_blocks


def large_image(root, provider_type="chat"):
    config, path = make_root(root, provider_type=provider_type)
    pixels = random.Random(17).randbytes(640 * 640 * 3)
    Image.frombytes("RGB", (640, 640), pixels).save(path)
    assert MAX_INLINE_IMAGE_BYTES < path.stat().st_size < 20 * MAX_INLINE_IMAGE_BYTES
    return config, path


def attach(path):
    return ChatResponse(text="", tool_calls=[ToolCall(
        id="callid_large", name="attach_media", arguments={"paths": [str(path)], "detail": "high"},
    )])


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
@pytest.mark.parametrize("retry", [False, True])
def test_large_tool_image_reaches_next_request_and_retry_losslessly(tmp_path, monkeypatch, provider_type, retry):
    _, path = large_image(tmp_path, provider_type)
    provider = MediaProvider([
        attach(path),
        *([ProviderError("offline", category="connection_error", retryable=True)] if retry else []),
        ChatResponse(text="done"),
    ])
    monkeypatch.setattr("run.conversation.runtime.backoff_seconds", lambda *args, **kwargs: 0)
    result = handle_request({
        "user": "alice", "source": "web", "session_id": "large", "prompt": "inspect image",
    }, root=tmp_path, provider_factory=lambda _: provider)
    assert result["text"] == "done"
    assert not native_blocks(provider.requests[0])
    for request in provider.requests[1:]:
        blocks = native_blocks(request)
        assert len(blocks) == 1 and blocks[0].detail == "high"
        assert any(isinstance(item, ToolResultItem) for item in request.input)
        if provider_type == "chat":
            assert blocks[0].source is not None
            decoded = base64.b64decode(blocks[0].source.data)
            assert decoded == path.read_bytes()
            assert hashlib.sha256(decoded).hexdigest() == blocks[0].checksum_sha256
            assert kemo_request_to_chat(request).messages[-1]["content"][-1]["type"] == "image_url"
        else:
            assert blocks[0].source is None and blocks[0].asset_id.startswith("asset_remote_")
    assert len(provider.uploads) == (1 if provider_type == "kemo" else 0)
    window = find_window(tmp_path, "alice", "web", "large")
    assert window is not None
    saved = json.dumps(load_window(window))
    assert '"status": "included"' in saved
    assert "inline_base64" not in saved and "data:image" not in saved


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
def test_large_subagent_image_is_projected_with_correct_transport(tmp_path, provider_type):
    config, path = large_image(tmp_path, provider_type)
    shutil.copytree(PROJECT / "agents/context_manage", tmp_path / "agents/context_manage")
    agent_config_path = tmp_path / "agents/context_manage/agent-config.json"
    agent_config = json.loads(agent_config_path.read_text("utf-8"))
    agent_config["tools"]["plugins"]["allow"] = ["attach_media"]
    agent_config_path.write_text(json.dumps(agent_config), "utf-8")
    summary = {"facts": [], "requirements": [], "decisions": [], "unfinished": [],
               "tool_results": [], "entities": [], "narrative": "done"}
    provider = MediaProvider([attach(path), ChatResponse(text=json.dumps(summary))])
    result = AgentRunner(tmp_path, "alice", config=config, provider_factory=lambda _: provider).run(
        "context_manage", {"previous_summary": None, "rounds": [], "trigger": "manual"},
        task_id="task_large",
    )
    assert result.data == summary
    assert len(native_blocks(provider.requests[-1])) == 1
    assert result.metadata["media_inputs"][0]["status"] == "included"
    assert path.stat().st_size > MAX_INLINE_IMAGE_BYTES


@pytest.mark.parametrize("delivery", ["upload", "tool"])
def test_large_image_works_through_real_chat_bridge(tmp_path, monkeypatch, delivery):
    config, path = large_image(tmp_path)
    config["provider"]["capabilities"] = {"input_modalities": ["text", "image"],
        "tools": {"function_calling": True, "parallel_calls": True}}
    provider = ChatBridgeProvider(config["provider"])
    sent = []

    def chat(wire):
        sent.append(wire)
        if delivery == "tool" and len(sent) == 1:
            return attach(path)
        return ChatResponse(text="seen")

    monkeypatch.setattr(provider._transport, "chat", chat)
    descriptor = describe_local_asset(tmp_path, "alice", {"path": str(path)})
    result = handle_request({
        "user": "alice", "source": "web", "session_id": "direct", "prompt": "inspect",
        "uploaded_files": [descriptor] if delivery == "upload" else [],
    }, root=tmp_path, provider_factory=lambda _: provider)
    assert result["text"] == "seen"
    images = [block for message in sent[-1].messages if isinstance(message["content"], list)
              for block in message["content"] if block.get("type") == "image_url"]
    assert len(images) == 1
    assert base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1]) == path.read_bytes()


def test_image_validation_failure_is_sanitized_and_records_failed_status(tmp_path, monkeypatch):
    config, path = make_root(tmp_path)
    host = make_host(tmp_path, config)
    enqueue(host, {"paths": [str(path)]})
    invalid = {"source": {"kind": "inline_base64", "data": "private-base64-marker"}}
    with pytest.raises(ValidationError) as caught:
        ImageContent.model_validate(invalid)

    def broken_content(*args, **kwargs):
        raise caught.value

    monkeypatch.setattr(host.queue.transport, "content", broken_content)
    with pytest.raises(AttachmentError, match="媒体请求内容校验失败") as failure:
        host.refresh_messages([])
    assert "private-base64-marker" not in str(failure.value)
    assert host.audit[0]["status"] == "failed"
    assert "private-base64-marker" not in json.dumps(host.audit)


def test_changed_capability_failure_updates_queued_media_status(tmp_path):
    config, path = make_root(tmp_path)
    host = make_host(tmp_path, config)
    enqueue(host, {"paths": [str(path)]})
    host.config["multimodal_routing"]["vision"] = "dedicated"
    with pytest.raises(AttachmentError, match="视觉路由"):
        host.refresh_messages([])
    assert host.audit[0]["status"] == "failed"
