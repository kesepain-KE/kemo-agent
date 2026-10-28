"""Whole-request summary budgeting and content-free failure classification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from run.context import estimate_messages_tokens, estimate_text_tokens


class SummaryBudgetError(RuntimeError):
    category = "summary_fixed_content_over_budget"


def failure_kind(error: BaseException) -> str:
    from run.conversation import is_context_length_exceeded

    if is_context_length_exceeded(error):
        return "input_limit"
    if getattr(error, "category", "") == "context_length_exceeded":
        return "input_limit"
    # A 413 is a transport-size signal here, not a declaration of model tokens.
    if getattr(error, "status_code", None) == 413:
        return "request_too_large"
    name = type(error).__name__
    if isinstance(error, SummaryBudgetError):
        return "fixed_content_over_budget"
    if "Cancelled" in name or str(error) == "cancelled":
        return "cancelled"
    if "Timeout" in name:
        return "timeout"
    if "Output" in name or "JSON" in name or "SummaryError" in name:
        return "invalid_summary"
    return "provider_error"


@dataclass
class SummaryBudget:
    input_limit: int
    output_limit: int
    system: str = ""
    max_bytes: int = 512_000

    def tokens(self, payload: dict[str, Any]) -> int:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return estimate_messages_tokens([
            {"role": "system", "content": self.system},
            {"role": "user", "content": text},
        ]) + 64  # Native item framing / compatibility mapping allowance.

    def fits(self, payload: dict[str, Any]) -> bool:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return (
            self.tokens(payload) <= self.input_limit
            and len(text.encode("utf-8")) + len(self.system.encode("utf-8")) <= self.max_bytes
        )

    def shrink(self, kind: str) -> None:
        if kind == "request_too_large":
            self.max_bytes //= 2
        else:
            self.input_limit //= 2


def summary_budget(runner: Any, name: str, chunk_limit: int, output_limit: int, cancel_event: Any) -> SummaryBudget:
    """Use the actual summary model and prompt, never the main injected bundle."""
    from run.agents import AgentRunner

    budget = SummaryBudget(max(256, chunk_limit), output_limit)
    if not isinstance(runner, AgentRunner):
        return budget  # Isolated test runners have no provider / prompt registry.
    from agents._runtime.resources import build_agent_prompt_bundle
    from run.agents import resolve_agent_provider_config
    from run.extensions import lookup_model_capabilities, declared_input_token_limit

    definition = runner.refresh_registry().get(name)
    bundle = build_agent_prompt_bundle(runner.root, runner.user, definition, runner.config)
    budget.system = bundle.text + "\n\n[output_schema]\n" + json.dumps(
        definition.output_schema, ensure_ascii=False, sort_keys=True,
    )
    runtime = resolve_agent_provider_config(runner.config, definition)
    capabilities = lookup_model_capabilities(
        runner.config, runtime, runner.provider_factory(runtime), cancel_event=cancel_event,
    ).capabilities
    declared = declared_input_token_limit(capabilities)
    if declared is not None:
        budget.output_limit = min(output_limit, max(1, declared // 4))
        budget.input_limit = min(budget.input_limit, declared - budget.output_limit)
    if capabilities is not None and capabilities.limits.max_output_tokens is not None:
        budget.output_limit = min(budget.output_limit, capabilities.limits.max_output_tokens)
    if budget.input_limit <= estimate_text_tokens(budget.system) + 128:
        raise SummaryBudgetError("摘要模型固定提示词及输出预留超过可用预算")
    return budget
