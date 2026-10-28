"""Chat's local image limit must not relax the native Kemo protocol."""

import base64

import pytest
from pydantic import ValidationError

from events import RunEvent
from provider.adapters.chat_bridge import ChatBridgeProvider
from provider.adapters.compat import chat_request_to_kemo, kemo_request_to_chat
from provider.adapters.image_content import CHAT_IMAGE_MAX_BYTES, ChatImageContent
from provider.adapters.image_content import validate_chat_request
from provider.protocol.errors import CapabilityError, ProtocolValidationError
from provider.protocol.models import ImageContent, KemoRequest, MAX_INLINE_IMAGE_BYTES, ReasoningItem
from provider.protocol.validation import validate_request
from provider.schema import ChatRequest, ChatResponse


def image_block(size):
    return {
        "type": "image",
        "mime_type": "image/png",
        "source": {"kind": "inline_base64", "data": base64.b64encode(b"x" * size).decode("ascii")},
        "detail": "high",
    }


@pytest.mark.parametrize("size", [0, MAX_INLINE_IMAGE_BYTES + 1])
def test_native_image_limit_is_unchanged(size):
    with pytest.raises(ValidationError, match="inline_base64" if size == 0 else "1..1048576"):
        ImageContent.model_validate(image_block(size))


def test_native_image_accepts_exact_limit():
    assert ImageContent.model_validate(image_block(MAX_INLINE_IMAGE_BYTES)).detail == "high"


def test_native_boundary_keeps_summary_only_reasoning_valid():
    request = KemoRequest.create(
        model="vision", input=[ReasoningItem(id="rs_summary", summary="summary only")],
    )
    assert validate_request(request) is request


@pytest.mark.parametrize("size", [MAX_INLINE_IMAGE_BYTES + 1, CHAT_IMAGE_MAX_BYTES])
def test_chat_image_accepts_original_bytes_up_to_twenty_mib(size):
    block = ChatImageContent.model_validate(image_block(size))
    assert block.source is not None and block.source.data is not None
    assert len(base64.b64decode(block.source.data)) == size
    assert "_max_inline_image_bytes" not in block.model_dump()


@pytest.mark.parametrize("size", [0, CHAT_IMAGE_MAX_BYTES + 1])
def test_chat_image_rejects_empty_and_over_limit(size):
    with pytest.raises(ValidationError, match="inline_base64" if size == 0 else "1..20971520"):
        ChatImageContent.model_validate(image_block(size))


@pytest.mark.parametrize("image_type", [ImageContent, ChatImageContent])
def test_both_transports_still_reject_invalid_base64(image_type):
    block = image_block(1)
    block["source"]["data"] = "not:base64!"
    with pytest.raises(ValidationError, match="有效 Base64"):
        image_type.model_validate(block)


@pytest.mark.parametrize("representation", ["image", "image_url", "input_image", "native_message"])
def test_chat_mapping_retains_large_original_and_high_detail(representation):
    block = image_block(MAX_INLINE_IMAGE_BYTES + 1)
    encoded = block["source"]["data"]
    if representation in {"image_url", "input_image"}:
        content = [{"type": representation, "image_url": {
            "url": f"data:image/png;base64,{encoded}", "detail": "high",
        }}]
    else:
        content = [block]
    message = {"role": "user", "content": content}
    if representation == "native_message":
        message["_kemo_message"] = {"id": "msg_original", "role": "user", "content": [block]}
    request = chat_request_to_kemo(
        ChatRequest(model="vision", messages=[message]), transport="chat",
    )
    validate_chat_request(request)
    mapped = kemo_request_to_chat(request)
    assert mapped.messages[0]["content"][0]["image_url"] == {
        "url": f"data:image/png;base64,{encoded}", "detail": "high",
    }
    # An internal Chat instance cannot leak through the native provider boundary.
    with pytest.raises(ProtocolValidationError, match="Asset"):
        validate_request(request)
    # Serialized requests are still subject to the unmodified native wire model.
    with pytest.raises(ValidationError, match="1048576"):
        KemoRequest.model_validate(request.model_dump(mode="json"))


def test_default_chat_to_kemo_mapping_remains_strict():
    wire = ChatRequest(model="vision", messages=[{
        "role": "user", "content": [image_block(MAX_INLINE_IMAGE_BYTES + 1)],
    }])
    with pytest.raises(CapabilityError):
        chat_request_to_kemo(wire)


@pytest.mark.parametrize("stream", [False, True])
def test_real_chat_bridge_accepts_large_image_without_network(monkeypatch, stream):
    request = chat_request_to_kemo(ChatRequest(model="vision", messages=[{
        "role": "user", "content": [image_block(MAX_INLINE_IMAGE_BYTES + 1)],
    }]), transport="chat")
    provider = ChatBridgeProvider({"base_url": "http://127.0.0.1:1/v1", "api_key": "test", "model": "vision",
                                   "capabilities": {"input_modalities": ["text", "image"], "streaming": True}})
    sent = []

    def chat(wire):
        sent.append(wire)
        return ChatResponse(text="image received")

    def chat_stream(wire):
        sent.append(wire)
        yield RunEvent(type="text_delta", content="image received")
        yield RunEvent(type="done", metadata={"finish_reason": "stop"})

    if stream:
        monkeypatch.setattr(provider._transport, "chat_stream", chat_stream)
        assert list(provider.stream(request))[-1].type == "response.completed"
    else:
        monkeypatch.setattr(provider._transport, "chat", chat)
        assert provider.create(request).status == "completed"
    assert len(sent) == 1
    assert sent[0].messages[0]["content"][0]["type"] == "image_url"
