"""Regression coverage for the final Kemo 2.0 hardening rules."""

import pytest
from pydantic import ValidationError

from provider.protocol.errors import ProtocolValidationError
from provider.protocol.models import (
    KemoRequest,
    MessageItem,
    ModelCapabilities,
    PredictionConfig,
    ProviderState,
    ReasoningItem,
    StructuredOutputConfig,
    ToolCallItem,
    ToolDefinition,
    ToolResultItem,
    validate_structured_output_text,
)
from provider.protocol.streaming import ProviderStreamEvent
from provider.protocol.validation import validate_provider_options


def test_structured_schema_is_draft_2020_12_and_local_ref_only() -> None:
    config = StructuredOutputConfig(
        type="json_schema",
        schema_name="answer",
        schema={
            "type": "object",
            "$defs": {"answer": {"type": "string"}},
            "properties": {"value": {"$ref": "#/$defs/answer"}},
            "required": ["value"],
        },
        strict=True,
    )
    assert validate_structured_output_text('{"value":"ok"}', config) is None
    assert validate_structured_output_text('{"value":3}', config)
    with pytest.raises(ValueError, match="本地"):
        StructuredOutputConfig(
            type="json_schema",
            schema_name="bad",
            schema={
                "type": "object",
                "properties": {"value": {"$ref": "https://example.test/schema"}},
            },
        )


def test_provider_options_require_explicit_vendor_allowlist_and_map_target() -> None:
    profile = {
        "provider_options": {
            "vendor.openai.web_search_options": {"target": "web_search_options"}
        }
    }
    assert validate_provider_options(
        {"vendor.openai.web_search_options": {"search_context_size": "high"}},
        profile,
    ) == {"web_search_options": {"search_context_size": "high"}}
    with pytest.raises(ProtocolValidationError):
        validate_provider_options({"x_raw": True}, profile)
    with pytest.raises(ProtocolValidationError):
        validate_provider_options(
            {"vendor.openai.web_search_options": {"headers": {"Authorization": "x"}}},
            profile,
        )


def test_stream_event_rejects_irrelevant_specialized_fields() -> None:
    with pytest.raises(ValueError, match="未定义事件字段"):
        ProviderStreamEvent(
            type="response.created",
            sequence=0,
            request_id="req_stream_hardening",
            response_id="resp_stream_hardening",
            delta="not-allowed",
        )


def test_tool_schema_requires_object_root() -> None:
    with pytest.raises(ValueError, match="object"):
        ToolDefinition(name="bad", description="bad", parameters={"type": "array"})


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MessageItem.text("assistant", "body", item_id="item_bad"),
        lambda: ReasoningItem(id="reasoning_bad", content="thought"),
        lambda: ToolCallItem(
            id="item_bad",
            call_id="callid_valid",
            name="lookup",
        ),
        lambda: ToolResultItem(
            id="item_bad",
            call_id="callid_valid",
            name="lookup",
            content=[{"type": "text", "text": "result"}],
        ),
        lambda: ToolCallItem(
            id="call_valid",
            call_id="call_bad",
            name="lookup",
        ),
        lambda: ToolResultItem(
            id="result_valid",
            call_id="call_bad",
            name="lookup",
            content=[{"type": "text", "text": "result"}],
        ),
    ],
)
def test_item_and_tool_call_identifiers_require_kemo_v2_prefixes(factory) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_capability_gate_rejects_model_mismatch_and_unscoped_prediction() -> None:
    request = KemoRequest.create(
        model="demo-model",
        stream=False,
        prediction=PredictionConfig(content=[{"type": "text", "text": "cached"}]),
    )
    capabilities = ModelCapabilities(
        protocol_version="2.0",
        model="other-model",
        provider_id="demo",
        provider_model="other-model",
        supports_prediction=True,
        output_modalities=["text"],
    )
    from provider.protocol.validation import validate_request_against_capabilities
    with pytest.raises(Exception, match="能力声明"):
        validate_request_against_capabilities(request, capabilities)
    capabilities.model = request.model
    capabilities.provider_model = request.model
    with pytest.raises(Exception, match="显式声明"):
        validate_request_against_capabilities(request, capabilities)


def test_capability_gate_checks_provider_state_owner_and_expiry() -> None:
    request = KemoRequest.create(
        model="demo-model",
        stream=False,
        input=[
            ReasoningItem(
                id="rs_state_gate",
                provider_state=ProviderState(
                    kind="opaque", data="state", provider="other-provider", model="demo-model"
                ),
            )
        ],
    )
    capabilities = ModelCapabilities(
        protocol_version="2.0",
        model="demo-model",
        provider_id="demo-provider",
        provider_model="demo-model",
        reasoning={"supported": True, "persisted_state": True, "efforts": ["medium"], "returns": ["none", "auto"]},
    )
    from provider.protocol.validation import validate_request_against_capabilities
    with pytest.raises(Exception, match="跨 Provider"):
        validate_request_against_capabilities(request, capabilities)
