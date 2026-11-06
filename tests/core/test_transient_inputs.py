"""Native media is one-shot; ordinary user/guidance history is not."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil

import pytest

from events import RunEvent
from provider.protocol.models import ImageContent, MessageItem, text_from_content
from provider.schema import ChatResponse, ProviderError, ToolCall
from run.cards import CardStore
from run.conversation import GuidanceInput, GuidanceMailbox
from run.engine import iter_request_events
from run.extensions import describe_uploaded_asset
from tests.core.test_preview_runtime_safety import tool
from tests.media_input_support import PNG, MediaProvider, make_root, native_blocks


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
@pytest.mark.parametrize("delivery", ["initial", "guidance"])
@pytest.mark.parametrize("retry", ["none", "transport", "partial"])
def test_media_consumed_by_accepted_response_not_final_body(
    tmp_path, monkeypatch, provider_type, delivery, retry,
):
    make_root(tmp_path, provider_type=provider_type)
    tool(tmp_path, "echo")
    monkeypatch.setattr("run.tools.runtime.execute_tool", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr("provider.retry.RetryManager.delay", lambda *args, **kwargs: 0)
    path = tmp_path / "users/alice/file_upload/upload.png"
    path.parent.mkdir()
    path.write_bytes(PNG)
    descriptor = describe_uploaded_asset(tmp_path, "alice", {"path": str(path)})
    mailbox = GuidanceMailbox()
    revision = "UNIQUE GUIDANCE REVISION"
    guidance = GuidanceInput(id="revision", text=revision, uploaded_files=[descriptor])
    first = ChatResponse(text="", tool_calls=[ToolCall("first", "echo", {"step": 1})])
    second = ChatResponse(text="", tool_calls=[ToolCall("second", "echo", {"step": 2})])
    failure = [ProviderError("offline", category="connection_error", retryable=True)] if retry != "none" else []
    responses = ([*failure, first, second] if delivery == "initial"
                 else [first, *failure, second])

    class Provider(MediaProvider):
        def create(self, request):
            if delivery == "guidance" and not self.requests:
                assert mailbox.offer(guidance)[0]
            return super().create(request)

    provider = Provider([*responses, ChatResponse(text="finished")])
    if retry == "partial":
        from run.conversation.provider_events import provider_events

        def with_partial_output(provider, request, **kwargs):
            if provider.responses and isinstance(provider.responses[0], ProviderError):
                yield RunEvent(type="reasoning_delta", content="unfinished image analysis")
            yield from provider_events(provider, request, **kwargs)

        monkeypatch.setattr("run.conversation.provider_exchange.provider_events", with_partial_output)
    request = {
        "user": "alice", "source": "web", "session_id": "transient",
        "prompt": "initial instruction", "_guidance_queue": mailbox,
        "uploaded_files": [descriptor] if delivery == "initial" else [],
    }
    events = list(iter_request_events(request, root=tmp_path, provider_factory=lambda _: provider))
    assert events[-1].type == "done" and events[-1].metadata["status"] == "completed"
    counts = [len(native_blocks(sent)) for sent in provider.requests]
    expected = [1, 1, 0, 0] if retry != "none" else [1, 0, 0]
    if delivery == "guidance":
        expected = [0, 1, 1, 0] if retry != "none" else [0, 1, 0]
    assert counts == expected
    for sent in provider.requests:
        for item in sent.input:
            if any(getattr(block, "type", None) in {"image", "audio", "video", "file"}
                   for block in getattr(item, "content", [])):
                text = text_from_content(item.content)
                assert "本次模型请求已携带" in text
                assert "无需等待最终回复或下一条用户消息" in text
    if retry != "none":
        start = 0 if delivery == "initial" else 1
        before, after = provider.requests[start:start + 2]
        if retry == "transport":
            assert before.model_dump_json(warnings=False) == after.model_dump_json(warnings=False)
        else:
            assert before.request_id != after.request_id
            assert before.input == after.input
    assert len(provider.uploads) == (1 if provider_type == "kemo" else 0)
    if delivery == "guidance":
        for sent in provider.requests[1:]:
            text = "\n".join(text_from_content(item.content) for item in sent.input
                             if isinstance(item, MessageItem))
            assert text.count(revision) == 1
            # Asset references are durable metadata; native media is transient.
            assert descriptor["asset_id"] in sent.model_dump_json(warnings=False)
        applied = [event for event in events if event.type == "guidance_applied"]
        assert len(applied) == 1 and applied[0].metadata["id"] == guidance.id
    raw = CardStore(tmp_path, "alice").raw_page("web", "transient")
    assert all("inline_base64" not in card.raw_payload_json for card in raw)
    assert len([card for card in raw if card.raw_kind == "user_text"]) == (
        2 if delivery == "guidance" else 1
    )
    if delivery == "guidance":
        guidance_card = next(card for card in raw if card.raw_level == "guidance")
        assert guidance_card.raw_payload["text"] == revision
        assert descriptor["asset_id"] == guidance_card.raw_payload["attachments"][0]["asset_id"]


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
def test_inline_media_is_transient_and_never_persisted_as_base64(tmp_path, provider_type):
    make_root(tmp_path, provider_type=provider_type)
    tool(tmp_path, "echo")
    image = ImageContent(mime_type="image/png", source={
        "kind": "inline_base64", "data": base64.b64encode(PNG).decode("ascii"),
    })
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall("first", "echo", {"step": 1})]),
        ChatResponse(text="", tool_calls=[ToolCall("second", "echo", {"step": 2})]),
        ChatResponse(text="finished"),
    ])
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "inline", "prompt": "inspect",
         "content": [image.model_dump(mode="json", exclude_none=True)]},
        root=tmp_path, provider_factory=lambda _: provider,
    ))
    assert events[-1].type == "done"
    assert [len(native_blocks(sent)) for sent in provider.requests] == [1, 0, 0]
    raw = CardStore(tmp_path, "alice").raw_page("web", "inline")
    assert all("inline_base64" not in card.raw_payload_json for card in raw)


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
@pytest.mark.parametrize("delivery", ["initial", "guidance"])
def test_media_survives_tool_argument_correction_until_a_valid_response(
    tmp_path, monkeypatch, provider_type, delivery,
):
    make_root(tmp_path, provider_type=provider_type)
    tool(tmp_path, "echo")
    monkeypatch.setattr("run.tools.runtime.execute_tool", lambda *args, **kwargs: {"ok": True})
    path = tmp_path / "users/alice/file_upload/correction.png"
    path.parent.mkdir()
    path.write_bytes(PNG)
    descriptor = describe_uploaded_asset(tmp_path, "alice", {"path": str(path)})
    mailbox = GuidanceMailbox()
    responses = [
        ChatResponse(text="", tool_calls=[ToolCall("invalid", "unregistered_tool", {})]),
        ChatResponse(text="", tool_calls=[ToolCall("corrected", "echo", {})]),
        ChatResponse(text="finished"),
    ]
    if delivery == "guidance":
        responses.insert(0, ChatResponse(text="", tool_calls=[ToolCall("first", "echo", {})]))

    class Provider(MediaProvider):
        def create(self, request):
            if delivery == "guidance" and not self.requests:
                assert mailbox.offer(GuidanceInput(
                    id="during-tool-loop", text="inspect this image", uploaded_files=[descriptor],
                ))[0]
            return super().create(request)

    provider = Provider(responses)
    events = list(iter_request_events({
        "user": "alice", "source": "web", "session_id": "media-correction", "prompt": "inspect",
        "uploaded_files": [descriptor] if delivery == "initial" else [],
        "_guidance_queue": mailbox,
    }, root=tmp_path, provider_factory=lambda _: provider))
    assert events[-1].type == "done"
    assert events[-1].metadata["tool_argument_retries"] == 1
    assert [len(native_blocks(request)) for request in provider.requests] == (
        [1, 1, 0] if delivery == "initial" else [0, 1, 1, 0]
    )


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
def test_subagent_tool_loop_consumes_attached_media_before_final_body(tmp_path, provider_type):
    from run.agents import AgentRunner
    from tests.agents.test_general_worker import OUTPUT

    config, image = make_root(tmp_path, provider_type=provider_type)
    shutil.copytree(Path(__file__).resolve().parents[2] / "agents/general_worker",
                    tmp_path / "agents/general_worker")
    tool(tmp_path, "echo")
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall(
            "attach", "attach_media", {"paths": [str(image)]})]),
        ChatResponse(text="", tool_calls=[ToolCall("echo", "echo", {"step": 2})]),
        ChatResponse(text=json.dumps(OUTPUT)),
    ])
    result = AgentRunner(tmp_path, "alice", config=config, provider_factory=lambda _: provider).run(
        "general_worker", {"task": f"inspect {image}"}, source="web", session_id="child-media",
    )
    assert result.data == OUTPUT
    assert [len(native_blocks(sent)) for sent in provider.requests] == [0, 1, 0]
