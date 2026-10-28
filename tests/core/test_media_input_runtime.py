from __future__ import annotations

import json
import shutil

import pytest

from provider.protocol.models import ToolCallItem, ToolResultItem
from provider.schema import ChatResponse, ProviderError, ToolCall
from run.agents import AgentRunner
from run.engine import handle_request
from run.history import find_window, load_window
from tests.media_input_support import (
    PNG, PROJECT, MediaProvider, make_root, native_blocks,
)

SUMMARY = {
    "facts": [], "requirements": [], "decisions": [], "unfinished": [],
    "tool_results": [], "entities": [], "narrative": "completed",
}


def attach(path, call_id="callid_attach", note="check image"):
    return ToolCall(id=call_id, name="attach_media",
                    arguments={"paths": [str(path)], "note": note})


def assert_paired_before_media(request):
    calls = {item.call_id for item in request.input if isinstance(item, ToolCallItem)}
    results = {item.call_id for item in request.input if isinstance(item, ToolResultItem)}
    assert calls == results
    first_media = next(
        index for index, item in enumerate(request.input)
        if any(getattr(block, "type", None) == "image" for block in getattr(item, "content", []))
    )
    assert all(
        index < first_media for index, item in enumerate(request.input)
        if isinstance(item, ToolResultItem)
        and item.call_id in {"callid_attach", "callid_duplicate", "callid_second"}
    )


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
@pytest.mark.parametrize("retry", [False, True])
def test_main_next_request_pairing_persistence_retry_and_next_user_isolation(
    tmp_path, monkeypatch, provider_type, retry,
):
    _, image = make_root(tmp_path, provider_type=provider_type)
    responses = [
        ChatResponse(text="", tool_calls=[
            attach(image), attach(image, "callid_duplicate", "same media"),
        ]),
        *([ProviderError("offline", category="connection_error", retryable=True)] if retry else []),
        ChatResponse(text="", tool_calls=[attach(image, "callid_followup")]),
        ChatResponse(text="finished"),
    ]
    provider = MediaProvider(responses)
    monkeypatch.setattr("run.conversation.runtime.backoff_seconds", lambda *args, **kwargs: 0)
    request = {
        "user": "alice", "source": "web", "session_id": "media-session",
        "prompt": "inspect the downloaded file",
    }
    result = handle_request(request, root=tmp_path, provider_factory=lambda _: provider)
    assert result["text"] == "finished"
    assert not native_blocks(provider.requests[0])
    for sent in provider.requests[1:]:
        assert len(native_blocks(sent)) == 1
        assert_paired_before_media(sent)
    if provider_type == "kemo":
        assert len(provider.uploads) == 1
    path = find_window(tmp_path, "alice", "web", "media-session")
    assert path is not None
    saved = json.dumps(load_window(path), ensure_ascii=False)
    assert "inline_base64" not in saved and "data:image" not in saved
    assert '"media_inputs"' in saved
    handle_request({**request, "prompt": "next user round"},
                   root=tmp_path, provider_factory=lambda _: provider)
    assert not native_blocks(provider.requests[-1])
    handle_request({**request, "session_id": "separate", "prompt": "another session"},
                   root=tmp_path, provider_factory=lambda _: provider)
    assert not native_blocks(provider.requests[-1])


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
def test_explicitly_unsupported_route_is_tool_failure_not_silent_fallback(tmp_path, provider_type):
    _, image = make_root(tmp_path, provider_type=provider_type, route="dedicated")
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[attach(image)]), ChatResponse(text="cannot attach"),
    ])
    result = handle_request({
        "user": "alice", "source": "web", "session_id": "blocked", "prompt": "try media",
    }, root=tmp_path, provider_factory=lambda _: provider)
    assert result["text"] == "cannot attach"
    assert not native_blocks(provider.requests[-1])
    errors = [item for item in provider.requests[-1].input if isinstance(item, ToolResultItem)]
    assert errors and errors[0].content[0].model_dump()["data"]["ok"] is False
    assert not provider.uploads


def configure_agent(root, *, allowed=True):
    shutil.copytree(PROJECT / "agents/context_manage", root / "agents/context_manage")
    path = root / "agents/context_manage/agent-config.json"
    value = json.loads(path.read_text("utf-8"))
    value["tools"]["plugins"]["allow"] = ["attach_media"] if allowed else []
    path.write_text(json.dumps(value), "utf-8")


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
@pytest.mark.parametrize("retry", [False, True])
def test_subagent_native_media_override_retry_and_independent_run(
    tmp_path, monkeypatch, provider_type, retry,
):
    config, image = make_root(tmp_path, provider_type=provider_type)
    configure_agent(tmp_path)
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[attach(image), attach(image, "callid_second", "dedup")]),
        *([ProviderError("offline", category="connection_error", retryable=True)] if retry else []),
        ChatResponse(text=json.dumps(SUMMARY)),
        ChatResponse(text=json.dumps(SUMMARY)),
    ])
    monkeypatch.setattr("run.agents.runner._agent_retry_delay_seconds", lambda *args: 0)
    runner = AgentRunner(tmp_path, "alice", config=config, provider_factory=lambda _: provider)
    data = {"previous_summary": None, "rounds": [], "trigger": "manual"}
    result = runner.run("context_manage", data, task_id="task_media", model_override="other-vision")
    assert result.data == SUMMARY
    assert all(request.model == "other-vision" for request in provider.requests)
    assert not native_blocks(provider.requests[0])
    for request in provider.requests[1:]:
        assert len(native_blocks(request)) == 1
        assert_paired_before_media(request)
    assert result.metadata["media_inputs"][0]["status"] == "included"
    assert "inline_base64" not in json.dumps(result.metadata)
    if provider_type == "kemo":
        assert len(provider.uploads) == 1
    runner.run("context_manage", data, task_id="task_other")
    assert not native_blocks(provider.requests[-1])


def test_subagent_without_permission_has_no_tool_and_cannot_enqueue(tmp_path):
    config, image = make_root(tmp_path)
    configure_agent(tmp_path, allowed=False)
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[attach(image)]),
        ChatResponse(text=json.dumps(SUMMARY)),
    ])
    runner = AgentRunner(tmp_path, "alice", config=config, provider_factory=lambda _: provider)
    runner.run("context_manage", {"previous_summary": None, "rounds": [], "trigger": "manual"})
    assert all(tool.name != "attach_media" for tool in provider.requests[0].tools)
    assert not native_blocks(provider.requests[-1])
    results = [item for item in provider.requests[-1].input if isinstance(item, ToolResultItem)]
    assert results[0].is_error


def test_subagent_actual_override_capabilities_control_media(tmp_path):
    config, image = make_root(tmp_path, provider_type="kemo")
    configure_agent(tmp_path)
    class ModelSpecificProvider(MediaProvider):
        def capabilities(self, model, **kwargs):
            result = super().capabilities(model, **kwargs)
            return result.model_copy(update={
                "input_modalities": ["text"] if model == "text-only" else ["text", "image"],
            })
    provider = ModelSpecificProvider([
        ChatResponse(text="", tool_calls=[attach(image)]),
        ChatResponse(text=json.dumps(SUMMARY)),
    ])
    runner = AgentRunner(tmp_path, "alice", config=config, provider_factory=lambda _: provider)
    runner.run("context_manage", {"previous_summary": None, "rounds": [], "trigger": "manual"},
               model_override="text-only")
    assert not native_blocks(provider.requests[-1]) and not provider.uploads


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
def test_context_compression_preserves_native_media_and_tool_pairing(tmp_path, provider_type):
    _, image = make_root(tmp_path, provider_type=provider_type)
    configure_agent(tmp_path, allowed=False)
    config_path = tmp_path / "config/global_config.json"
    config = json.loads(config_path.read_text("utf-8"))
    config["agents"] = {
        "conserved_rounds": 3, "max_rounds": 80,
        "rounds_after_compression": 1, "token_limit": 120000,
    }
    config_path.write_text(json.dumps(config), "utf-8")
    class CompressionProvider(MediaProvider):
        def create(self, request):
            if request.metadata.get("source") == "subagent":
                self.requests.append(request.model_copy(deep=True))
                from provider.adapters.compat import chat_response_to_kemo
                return chat_response_to_kemo(ChatResponse(text=json.dumps(SUMMARY)), request)
            return super().create(request)
    provider = CompressionProvider([
        ChatResponse(text="old one"), ChatResponse(text="old two"),
        ChatResponse(text="old three"),
        ChatResponse(text="", tool_calls=[attach(image)]),
        ProviderError("context full", status_code=400, category="context_length_exceeded"),
        ChatResponse(text="after compression"),
    ])
    request = {
        "user": "alice", "source": "web", "session_id": "compressed", "prompt": "old",
        "_auto_retry_max_attempts": 1,
    }
    for i in range(3):
        handle_request({**request, "prompt": f"old-{i}"}, root=tmp_path, provider_factory=lambda _: provider)
    result = handle_request({**request, "prompt": "inspect"}, root=tmp_path, provider_factory=lambda _: provider)
    assert result["text"] == "after compression"
    media_requests = [request for request in provider.requests if native_blocks(request)]
    assert len(media_requests) == 2
    for sent in media_requests:
        assert len(native_blocks(sent)) == 1
        assert_paired_before_media(sent)
    if provider_type == "kemo":
        assert len(provider.uploads) == 1


def test_main_media_full_context_limit_stops_before_oversized_provider_request(tmp_path, monkeypatch):
    _, image = make_root(tmp_path, provider_type="kemo")
    from run.engine import iter_request_events
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[attach(image, note="x" * 2000)]),
        ChatResponse(text="must not reach here"),
    ], max_input_tokens=120000)
    # Input limit is large enough for the media alone, but not the whole prompt
    # plus schemas. A server usage observation then forces the host context guard.
    original = provider.create
    def with_large_usage(request):
        result = original(request)
        if len(provider.requests) == 1:
            from provider.protocol.models import Usage
            result = result.model_copy(update={"usage": Usage.model_validate({
                "input_tokens": 119999, "output_tokens": 1, "total_tokens": 120000,
                "measurement": {"mode": "provider", "exact": True},
            })})
        return result
    monkeypatch.setattr(provider, "create", with_large_usage)
    config_path = tmp_path / "config/global_config.json"
    config = json.loads(config_path.read_text("utf-8"))
    config["agents"] = {"token_limit": 120000}
    config_path.write_text(json.dumps(config), "utf-8")
    events = list(iter_request_events({
        "user": "alice", "source": "web", "session_id": "budget",
        "prompt": "inspect", "_auto_retry_max_attempts": 1,
    }, root=tmp_path, provider_factory=lambda _: provider))
    assert len(provider.requests) == 1
    assert any(
        event.type == "tool_call_result" and event.result.get("ok") is True
        for event in events
    )
    assert any(event.metadata.get("stop_reason") == "tool_context_limit" for event in events)


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
def test_initial_user_attachment_is_not_duplicated_by_attach_media(tmp_path, provider_type):
    make_root(tmp_path, provider_type=provider_type)
    from run.extensions import describe_uploaded_asset
    path = tmp_path / "users/alice/file_upload/upload.png"
    path.parent.mkdir()
    path.write_bytes(PNG)
    descriptor = describe_uploaded_asset(tmp_path, "alice", {"path": str(path)})
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall(
            id="callid_uploaded", name="attach_media",
            arguments={"asset_ids": [descriptor["asset_id"]]},
        )]),
        ChatResponse(text="already visible"),
    ])
    handle_request({
        "user": "alice", "source": "web", "session_id": "uploaded",
        "prompt": "inspect", "uploaded_files": [descriptor],
    }, root=tmp_path, provider_factory=lambda _: provider)
    assert all(len(native_blocks(sent)) == 1 for sent in provider.requests)
    if provider_type == "kemo":
        assert len(provider.uploads) == 1


def test_file_acquired_by_another_tool_can_attach_in_same_batch(tmp_path):
    make_root(tmp_path)
    plugin = tmp_path / "plugins/capture"
    plugin.mkdir()
    manifest = {
        "name": "capture", "description": "create test image",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "version": "1.0.0", "enabled": True, "entrypoint": "tool.py:run",
    }
    (plugin / "SKILL.md").write_text(
        "# capture\ncreate test image\n\n## Tool\n```json\n" + json.dumps(manifest) + "\n```",
        "utf-8",
    )
    (plugin / "tool.py").write_text(
        "from pathlib import Path\n"
        "def run(*, context):\n"
        "    path = Path(context['root']) / 'captured.png'\n"
        f"    path.write_bytes({PNG!r})\n"
        "    return {'path': str(path)}\n",
        "utf-8",
    )
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[
            ToolCall(id="callid_capture", name="capture", arguments={}),
            attach(tmp_path / "captured.png"),
        ]),
        ChatResponse(text="native result"),
    ])
    result = handle_request({
        "user": "alice", "source": "web", "session_id": "acquired",
        "prompt": "take screenshot and inspect",  # Does not grant a filesystem path.
    }, root=tmp_path, provider_factory=lambda _: provider)
    assert result["text"] == "native result"
    assert len(native_blocks(provider.requests[-1])) == 1
    assert_paired_before_media(provider.requests[-1])
