from __future__ import annotations

import pytest

from run.infra.process_execution import _safe_error
from run.infra.runtime_diagnostics import diagnostic_text
from run.tools import ToolResultTooLargeError


@pytest.mark.parametrize("project", [_safe_error])
@pytest.mark.parametrize("message,secret", [
    ("request failed: https://api.example.com/v1/chat?api_key=sk-abc123", "sk-abc123"),
    ("request failed: api key: sk-xyz", "sk-xyz"),
    ("request failed: api-key=sk-hyphen", "sk-hyphen"),
    ("request failed: api_key: sk-underscore", "sk-underscore"),
])
def test_tool_errors_redact_before_exposing_to_model(project, message, secret):
    result = project(ValueError(message))
    assert secret not in repr(result)
    assert "request failed" in result["message"]
    assert result["exception_type"] == "ValueError"


@pytest.mark.parametrize("project", [_safe_error])
def test_tool_error_path_and_url_omit_queries_and_keep_classification(project):
    error = FileNotFoundError("missing local file")
    setattr(error, "path", "/tmp/report.txt?private-path-parameter")
    setattr(error, "url", "https://example.com/report?private-url-parameter")
    setattr(error, "category", "upstream_error")
    setattr(error, "status_code", 503)
    setattr(error, "retryable", True)
    result = project(error)
    assert result["path"] == "/tmp/report.txt"
    assert result["url"] == "https://example.com/report"
    assert result["category"] == "upstream_error"
    assert result["status_code"] == 503
    assert result["retryable"] is True
    assert result["message"] == "missing local file"


def test_diagnostic_text_keeps_normal_error_readable():
    assert diagnostic_text("FileNotFoundError: report.txt does not exist") == (
        "FileNotFoundError: report.txt does not exist"
    )


def test_large_tool_result_error_also_strips_path_query():
    error = ToolResultTooLargeError(
        "file", {"action": "read", "path": "/tmp/report?private-parameter"},
        result_chars=101, limit_chars=100,
    )
    payload = _safe_error(error)
    assert payload["path"] == "/tmp/report"
    assert payload["category"] == "result_too_large"
    assert payload["retryable"] is False
