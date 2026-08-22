from __future__ import annotations

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
from run.agents import AgentRunError, AgentRunner
from run.tools import (
    response_invalid_tool_arguments_error,
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
    response = _response_with_tool(
        arguments={},
        arguments_raw='{"title":"unfinished',
        parse_error={"message": "Unterminated string"},
    )

    error = response_invalid_tool_arguments_error(response)

    assert error is not None
    assert error["stop_reason"] == "invalid_tool_arguments"
    assert error["tool_name"] == "submit_structured_output"
    assert error["arguments_raw"] == '{"title":"unfinished'


def test_compatibility_incomplete_details_names_subagent_dispatch() -> None:
    response = KemoResponse(
        request_id="placeholder",
        status=ResponseStatus.INCOMPLETE,
        model="mock",
        output=[],
        incomplete_details={
            "reason": "invalid_tool_arguments",
            "invalid_tool_calls": [
                {
                    "name": "subagent_dispatch",
                    "call_id": "call-subagent",
                    "arguments_raw": "{",
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
