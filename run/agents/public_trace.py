"""Bounded public tool traces, distinct from private Provider diagnostics."""
from __future__ import annotations

import json
import re
from typing import Any

from provider.protocol.diagnostics import redact_diagnostic_text

_PRIVATE = {
    "reasoning", "reasoning_content", "thinking", "thinking_content", "private_reasoning",
    "system", "system_prompt", "system_message", "developer_message", "raw_response",
    "provider_response", "metadata", "arguments_raw",
}
_SECRET = re.compile(
    r"(?:^|_)(?:api_key|api_token|password|passwd|secret|credentials?|authorization|"
    r"cookies?|private_key|access_token|refresh_token|session_token|auth_token|id_token|token)$"
)
_SECRET_TEXT = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b"
)


def public_tool_data(value: Any) -> Any:
    """Keep useful call data without publishing secrets or unbounded output.

    max_tokens and ordinary prompt arguments are not private model reasoning.
    This projection never receives Provider input/history/system messages.
    """
    budget = [16000, 512]

    def visit(item: Any, depth: int = 0) -> Any:
        budget[1] -= 1
        if depth > 8 or min(budget) <= 0:
            return "[truncated]"
        if isinstance(item, dict):
            result = {}
            for key, child in list(item.items())[:80]:
                if min(budget) <= 0:
                    result["truncated"] = True
                    break
                name = str(key)[:160]
                normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower().replace("-", "_")
                if normalized in _PRIVATE:
                    continue
                budget[0] -= len(name)
                result[name] = "[redacted]" if _SECRET.search(normalized) else visit(child, depth + 1)
            if len(item) > 80:
                result["truncated"] = True
            return result
        if isinstance(item, (list, tuple)):
            result = []
            for child in item[:80]:
                if min(budget) <= 0:
                    break
                result.append(visit(child, depth + 1))
            if len(result) < len(item):
                result.append("[truncated]")
            return result
        if isinstance(item, str):
            limit = max(0, budget[0])
            # Tools may return JSON as a string; preserve its type while still
            # filtering sensitive nested fields, not just credential-shaped text.
            if len(item) <= limit and item.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(item)
                except (ValueError, RecursionError):
                    pass
                else:
                    if isinstance(parsed, (dict, list)):
                        return json.dumps(visit(parsed, depth + 1), ensure_ascii=False)
            text = _SECRET_TEXT.sub("[redacted]", redact_diagnostic_text(item[:limit]))
            budget[0] -= len(text)
            return text + ("…[truncated]" if len(item) > limit else "")
        if isinstance(item, (bool, int, float)) or item is None:
            return item
        return None

    return visit(value)
