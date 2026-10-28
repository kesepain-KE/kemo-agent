import inspect

import pytest

from events import RunEvent
from provider.adapters.chat_bridge import ChatBridgeProvider
from provider.adapters.compat import chat_request_to_kemo, chat_response_to_kemo, chat_stream_to_protocol, kemo_request_to_chat
from provider.adapters.gateway import KemoGatewayAdapter
from provider.chat_compat import CHAT_REASONING_PLACEHOLDER
from provider.protocol.errors import CapabilityError, ProtocolValidationError
from provider.protocol.models import EmbeddingRequest, RerankRequest, KemoRequest, ReasoningItem
from provider.protocol.validation import validate_provider_options, validate_request
from provider.schema import ChatRequest, ChatResponse, ProviderError


def test_native_validator_has_no_adapter_bypass():
    assert "allow_chat_images" not in inspect.signature(validate_request).parameters


def test_profileless_bridge_allows_basic_tools_but_labels_assumption():
    request = chat_request_to_kemo(ChatRequest(model="unknown", messages=[], tools=[
        {"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}
    ]))
    bridge = ChatBridgeProvider({"base_url": "http://127.0.0.1:1", "model": "unknown", "api_key": "offline"})
    bridge.validate(request)
    declared = bridge.capabilities("unknown")
    assert declared.extensions["capability_source"] == "compat-default"
    assert declared.extensions["capability_verification"] == "assumed"
    assert not declared.reasoning.supported and not declared.tools.multimodal_results


@pytest.mark.parametrize("target", ["messages", "system", "input", "instructions", "model", "unknown_option"])
def test_vendor_mapping_cannot_replace_formal_or_unknown_fields(target):
    with pytest.raises(ProtocolValidationError):
        validate_provider_options({"vendor.test.override": ["injected"]},
                                  {"provider_option_map": {"vendor.test.override": target}})


def test_chat_dto_has_final_payload_protection():
    with pytest.raises(ProviderError):
        ChatRequest(model="x", messages=[], extra={"messages": []}).to_payload()


@pytest.mark.parametrize("stream", [False, True])
def test_synthetic_reasoning_stays_out_of_protocol_content(stream):
    request = KemoRequest.create(model="x", input=[])
    if stream:
        response = list(chat_stream_to_protocol([
            RunEvent(type="reasoning_delta", content=CHAT_REASONING_PLACEHOLDER, metadata={"synthetic": True}),
            RunEvent(type="text_delta", content="OK"),
            RunEvent(type="done", metadata={"reasoning_synthetic": True}),
        ], request))[-1].response
    else:
        response = chat_response_to_kemo(ChatResponse(text="OK", reasoning=CHAT_REASONING_PLACEHOLDER,
                                                    reasoning_synthetic=True), request)
    assert response is not None
    assert not any(isinstance(item, ReasoningItem) and item.content for item in response.output)
    assert CHAT_REASONING_PLACEHOLDER not in response.model_dump_json()


@pytest.mark.parametrize("options", [
    {"tool_choice": "required"}, {"response_format": {"type": "json_object"}},
    {"n": 100}, {"logprobs": True}, {"top_p": 0.5}, {"stop": ["secret"]},
    {"reasoning_enabled": True}, {"__kemo_adapter_hint": "not-authorized"},
    {"vendor.test.api_key": "do-not-forward"},
    {"vendor.test.search": {"headers": {"authorization": "do-not-forward"}}},
])
def test_every_transport_rejects_option_side_channel_before_http(options):
    config = {"base_url": "http://127.0.0.1:1", "api_key": "offline", "model": "unknown"}
    request = KemoRequest.create(model="unknown", input=[], provider_options=options)
    for adapter in (ChatBridgeProvider(config), KemoGatewayAdapter(config)):
        with pytest.raises(ProtocolValidationError):
            adapter.validate(request)
    with pytest.raises(ProtocolValidationError):
        kemo_request_to_chat(request)


def test_whitelisted_vendor_option_and_formal_generation_remain_distinct():
    profile = {"provider_option_map": {"vendor.test.minimum": "min_p"}}
    options = {"vendor.test.minimum": 0.1}
    request = KemoRequest.create(
        model="unknown", input=[], stream=False, provider_options=options,
        generation={"top_p": 0.8, "n": 2},
    )
    wire = kemo_request_to_chat(request, provider_profile=profile).to_payload()
    assert wire["min_p"] == 0.1
    assert wire["top_p"] == 0.8 and wire["n"] == 2
    native = KemoGatewayAdapter({
        "base_url": "http://127.0.0.1:1", "api_key": "offline",
        "model": "unknown", "capabilities": profile,
    })
    native.validate(request)
    assert request.provider_options == options  # Native names are not flattened.


def test_vendor_prefix_without_profile_is_not_authorization():
    native = KemoGatewayAdapter({"base_url": "http://127.0.0.1:1", "api_key": "offline", "model": "unknown"})
    with pytest.raises(ProtocolValidationError, match="白名单"):
        native.validate(KemoRequest.create(model="unknown", provider_options={"vendor.test.minimum": 0.1}))


@pytest.mark.parametrize("key", ["unknown_field", "__kemo_adapter_hint", "AUTHORIZATION"])
def test_chat_dto_does_not_forward_unknown_or_private_extras(key):
    with pytest.raises(ProviderError):
        ChatRequest(model="x", messages=[], extra={key: "not-wire"}).to_payload()


@pytest.mark.parametrize("kind", ["embedding", "rerank"])
def test_native_vector_endpoints_share_the_option_boundary(kind):
    native = KemoGatewayAdapter({"base_url": "http://127.0.0.1:1", "api_key": "offline", "model": "unknown"})
    common = {"protocol_version": "2.0", "request_id": "req_test",
              "model": "unknown", "provider_options": {"headers": {"authorization": "not-wire"}}}
    with pytest.raises(ProtocolValidationError):
        if kind == "embedding":
            native.embeddings(EmbeddingRequest.model_validate({
                **common, "input_type": "document", "inputs": [{"id": "one", "text": "hello"}],
            }))
        else:
            native.rerank(RerankRequest.model_validate({
                **common, "query": "hello", "documents": [{"id": "one", "text": "world"}],
            }))
