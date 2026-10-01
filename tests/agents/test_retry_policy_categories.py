from __future__ import annotations

from run.agents.retry_policy import _agent_tool_failure_is_retryable


def test_tool_failure_retryability_uses_only_transient_categories_by_default() -> None:
    assert _agent_tool_failure_is_retryable(
        {"error": {"category": "connection_error"}},
        "failed",
    )
    assert _agent_tool_failure_is_retryable(
        {"error": {"category": "upstream_error"}},
        "failed",
    )
    assert not _agent_tool_failure_is_retryable(
        {"error": {"category": "validation_error"}},
        "failed",
    )


def test_explicit_retryable_flag_overrides_category_default() -> None:
    assert _agent_tool_failure_is_retryable(
        {"error": {"category": "validation_error", "retryable": True}},
        "failed",
    )
    assert not _agent_tool_failure_is_retryable(
        {"error": {"category": "connection_error", "retryable": False}},
        "failed",
    )
