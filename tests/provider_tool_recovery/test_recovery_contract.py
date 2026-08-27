from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from provider.protocol.enums import ResponseStatus
from provider.protocol.models import (
    KemoResponse,
    Measurement,
    ModelCapabilities,
    ToolCallItem,
    Usage,
)
from provider.schema import ToolCall
from run.agents import AgentRunError, AgentRunner
from run.tools import (
    response_invalid_tool_arguments_error,
    validate_tool_call_batch,
)


EXPECTED_SUMMARY = {
    "title": "工具参数恢复成功",
    "summary": "子代理重新生成了完整的结构化工具参数，并成功通过输出结构校验后提交历史摘要结果。",
}


def _usage() -> Usage:
    return Usage(
        input_tokens=2,
        output_tokens=1,
        total_tokens=3,
        measurement=Measurement(mode="provider", exact=True),
    )


class RecoveryProvider:
    def __init__(self, responses: list[KemoResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    def capabilities(self, model: str, *, capabilities_url: str | None = None):
        del capabilities_url
        return ModelCapabilities.model_validate(
            {
                "model": model,
                "task": "llm",
                "reasoning": {"supported": False, "efforts": [], "summary": False},
            }
        )

    def create(self, request):
        self.requests.append(request)
        return self.responses.pop(0).model_copy(
            update={"request_id": request.request_id, "model": request.model}
        )


def _response_with_tool(
    *,
    arguments: dict,
    parse_error: dict | None = None,
    arguments_raw: str | None = None,
) -> KemoResponse:
    return KemoResponse(
        request_id="placeholder",
        status=ResponseStatus.COMPLETED,
        model="mock",
        output=[
            ToolCallItem(
                id="tool-item",
                call_id="tool-call",
                name="submit_structured_output",
                arguments=arguments,
                arguments_raw=arguments_raw,
                parse_error=parse_error,
            )
        ],
        usage=_usage(),
    )


def _runner(provider: RecoveryProvider, *, retries: int = 2) -> AgentRunner:
    root = Path(__file__).resolve().parents[2]
    config = {
        "provider": {
            "type": "kemo",
            "base_url": "http://127.0.0.1:1",
            "api_key_env": "TEST_AGENT_KEY",
            "model": "mock",
        },
        "tools": {
            "enabled": True,
            "timeout": 2,
            "max_iterations": 4,
            "invalid_tool_arguments_retries": retries,
        },
    }
    return AgentRunner(
        root,
        "kesepain",
        config=config,
        provider_factory=lambda _: provider,
    )


def _history_summary_input() -> dict:
    return {
        "trigger": "session_closed",
        "session_id": "conv_provider_recovery",
        "target_round": 1,
        "previous_summary": None,
        "rounds": [{"round": 1, "user": "问题", "assistant": "回答"}],
    }


def test_native_parse_error_is_normalized_before_tool_execution() -> None:
    raw_arguments = '{"api_key":"sk-provider-secret-value'
    response = _response_with_tool(
        arguments={},
        arguments_raw=raw_arguments,
        parse_error={"message": "Unterminated sk-provider-secret-value"},
    )

    error = response_invalid_tool_arguments_error(response)

    assert error is not None
    assert error["stop_reason"] == "invalid_tool_arguments"
    assert error["tool_name"] == "submit_structured_output"
    assert "arguments_raw" not in error
    assert error["arguments_diagnostic"] == {
        "available": True,
        "length": len(raw_arguments),
        "content_omitted": True,
        "json_root_expected": "object",
    }
    assert error["parse_error"]["message"] == "工具参数 JSON 解析失败"
    serialized = json.dumps(error, ensure_ascii=False)
    assert "sk-provider-secret-value" not in serialized


def test_missing_arguments_diagnostic_does_not_report_synthetic_empty_object() -> None:
    call = ToolCall(
        id="tool-item",
        name="file",
        arguments={},
        parse_error={"kind": "missing_arguments"},
    )
    error = validate_tool_call_batch(
        [call],
        {
            "file": {
                "type": "object",
                "properties": {"action": {"type": "string"}},
                "required": ["action"],
            }
        },
    )

    assert error is not None
    diagnostic = error["invalid_tool_calls"][0]["arguments_diagnostic"]
    assert diagnostic["available"] is False
    assert diagnostic["length"] == 0


def test_batch_validation_does_not_recursively_serialize_deep_arguments() -> None:
    arguments = {}
    for _ in range(3000):
        arguments = {"nested": arguments}
    call = ToolCall(
        id="tool-item",
        name="file",
        arguments=arguments,
    )

    assert validate_tool_call_batch([call], {}) is None


def test_batch_parse_error_kind_is_allowlisted() -> None:
    call = ToolCall(
        id="tool-item",
        name="file",
        arguments={},
        parse_error={"kind": "password=diagnostic-secret"},
    )
    error = validate_tool_call_batch([call], {"file": {"type": "object"}})

    assert error is not None
    assert error["invalid_tool_calls"][0]["parse_error"]["kind"] == "invalid_json"
    assert "diagnostic-secret" not in str(error)


def test_compatibility_incomplete_details_names_subagent_dispatch() -> None:
    response = KemoResponse(
        request_id="placeholder",
        status=ResponseStatus.INCOMPLETE,
        model="mock",
        output=[],
        incomplete_details={
            "reason": "invalid_tool_arguments",
            "debug": {"authorization": "Bearer gateway-secret"},
            "invalid_tool_calls": [
                {
                    "name": "subagent_dispatch",
                    "call_id": "call-subagent",
                    "arguments_raw": '{"password":"gateway-secret',
                    "parse_error": {"message": "gateway-secret"},
                }
            ],
        },
        usage=_usage(),
    )

    error = response_invalid_tool_arguments_error(response)

    assert error is not None
    assert error["stop_reason"] == "invalid_tool_arguments"
    assert error["tool_name"] == "subagent_dispatch"
    assert "subagent_dispatch" in error["message"]
    serialized = json.dumps(error, ensure_ascii=False)
    assert "gateway-secret" not in serialized
    assert "arguments_raw" not in serialized
    assert "debug" not in error["incomplete_details"]


def test_malformed_tool_identifier_cannot_bypass_diagnostic_redaction() -> None:
    response = KemoResponse(
        request_id="placeholder",
        status=ResponseStatus.REQUIRES_ACTION,
        model="mock",
        output=[
            ToolCallItem(
                id="tool-item",
                call_id="call\nBearer identifier-secret",
                name="lookup\npassword=identifier-secret",
                arguments={},
                arguments_raw="{",
                parse_error={"message": "invalid"},
            )
        ],
        usage=_usage(),
    )

    error = response_invalid_tool_arguments_error(response)

    assert error is not None
    serialized = json.dumps(error, ensure_ascii=False)
    assert "identifier-secret" not in serialized
    assert error["tool_name"] == "unknown_tool"
    assert error["call_id"] == ""


def test_subagent_retries_malformed_structured_tool_arguments() -> None:
    provider = RecoveryProvider(
        [
            _response_with_tool(
                arguments={},
                arguments_raw="{",
                parse_error={"message": "invalid"},
            ),
            _response_with_tool(arguments=EXPECTED_SUMMARY),
        ]
    )

    with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
        result = _runner(provider).run(
            "history_summary",
            _history_summary_input(),
            structured_output_tool=True,
            max_tokens=512,
        )

    assert result.data == EXPECTED_SUMMARY
    assert result.metadata["tool_argument_retries"] == 1
    assert len(provider.requests) == 2
    assert provider.requests[0].request_id != provider.requests[1].request_id
    assert provider.requests[1].attempt == 2
    assert provider.requests[1].metadata["tool_argument_retry"] == 1
    assert "provider_tool_argument_repair" in provider.requests[1].system_prompt


def test_subagent_stops_at_configured_malformed_argument_retry_limit() -> None:
    provider = RecoveryProvider(
        [
            _response_with_tool(
                arguments={},
                arguments_raw="{",
                parse_error={"message": "invalid"},
            ),
            _response_with_tool(
                arguments={},
                arguments_raw="{",
                parse_error={"message": "invalid again"},
            ),
        ]
    )

    with patch.dict(os.environ, {"TEST_AGENT_KEY": "secret"}, clear=False):
        with pytest.raises(AgentRunError, match="已重试 1/1 次"):
            _runner(provider, retries=1).run(
                "history_summary",
                _history_summary_input(),
                structured_output_tool=True,
                max_tokens=512,
            )

    assert len(provider.requests) == 2
