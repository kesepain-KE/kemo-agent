from __future__ import annotations

import json

import pytest

from provider.protocol.models import ToolCallItem, ToolResultItem
from provider.schema import ChatResponse, ProviderError, ToolCall
from run.engine import handle_request
from run.cards import CardStore
from tests.media_input_support import PNG, MediaProvider, make_root, native_blocks

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
    monkeypatch.setattr("provider.retry.RetryManager.delay", lambda *args, **kwargs: 0)
    request = {
        "user": "alice", "source": "web", "session_id": "media-session",
        "prompt": "inspect the downloaded file",
    }
    result = handle_request(request, root=tmp_path, provider_factory=lambda _: provider)
    assert result["text"] == "finished"
    assert not native_blocks(provider.requests[0])
    media_counts = [len(native_blocks(sent)) for sent in provider.requests]
    assert media_counts[1] == 1
    retry_tail = 1 if retry else 0
    assert all(count == 1 for count in media_counts[1:2 + retry_tail])
    # The second successful response explicitly asks to load the asset again.
    assert media_counts[2 + retry_tail:] == [1]
    for sent in provider.requests[1:2 + retry_tail]:
        assert_paired_before_media(sent)
    if provider_type == "kemo":
        assert len(provider.uploads) == 1
    store = CardStore(tmp_path, "alice")
    saved = json.dumps([card.raw_payload for card in store.raw_page("web", "media-session")],
                       ensure_ascii=False)
    assert "inline_base64" not in saved and "data:image" not in saved
    with store.db() as db:
        integration = db.execute(
            "SELECT record_json FROM card_integrations ORDER BY created_at LIMIT 1"
        ).fetchone()
    assert integration is not None and '"media_inputs"' in integration[0]
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
    assert errors and errors[0].content[0].model_dump()["data"]["result"]["ok"] is False
    assert not provider.uploads


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
def test_initial_user_attachment_can_be_explicitly_reloaded(tmp_path, provider_type):
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
    assert len(native_blocks(provider.requests[0])) == 1
    assert all(len(native_blocks(sent)) == 1 for sent in provider.requests[1:])
    if provider_type == "kemo":
        # The explicit tool load uses its own run-owned upload cache.
        assert len(provider.uploads) == 2
