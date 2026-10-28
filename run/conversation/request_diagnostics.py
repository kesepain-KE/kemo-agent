"""Request sizing guards and content-free fingerprints before Provider mapping."""

from __future__ import annotations


def project_context_input(local_tokens, provider_tokens, previous_local_tokens):
    if provider_tokens is not None and previous_local_tokens is not None:
        delta = local_tokens - previous_local_tokens
        return delta, max(0, provider_tokens + delta), "provider_plus_increment"
    return None, local_tokens, "local_estimate"


def record_context_checkpoint(stats, usage, local_tokens, provider, cache, previous):
    from run.context import make_checkpoint
    if usage is None or usage.estimated or usage.prompt_tokens <= 0:
        return previous
    stats["usage_checkpoint"] = make_checkpoint(usage.prompt_tokens, int(local_tokens), provider, cache)
    return usage.prompt_tokens, int(local_tokens)


def request_output_limit(runtime_provider, context_policy):
    configured = runtime_provider.get("max_tokens")
    return min(context_policy.output_reserve, max(1, int(configured))) if configured is not None else None


def tool_context_guard(*, measurement, provider_input_tokens, previous_local_tokens,
                       current_local_tokens, incremental_tokens, projected_tokens,
                       token_limit, configured_token_limit, provider_declared_input_limit,
                       iteration, latest_tools):
    """One public projection for a tool-context safety stop."""
    return {
        "measurement": measurement, "provider_input_tokens": provider_input_tokens,
        "previous_local_tokens": previous_local_tokens, "current_local_tokens": current_local_tokens,
        "incremental_tokens": incremental_tokens, "projected_tokens": projected_tokens,
        "token_limit": token_limit, "configured_token_limit": configured_token_limit,
        "provider_declared_input_limit": provider_declared_input_limit,
        "effective_token_limit": token_limit, "iteration": iteration, "latest_tools": latest_tools,
    }

import hashlib
import json
from typing import Any

from provider.protocol.models import KemoRequest
from run.config import PromptBundle


def _encoded(value: Any) -> bytes:
    # Insertion order matters inside JSON tool results and arguments.
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def provider_request_diagnostic(
    request: KemoRequest,
    bundle: PromptBundle,
    *,
    iteration: int,
    attempt: int,
) -> dict[str, Any]:
    """Retain hashes/lengths, never prompts, tool data, credentials or state."""
    wire = request.model_dump(mode="json", exclude_none=True)
    system = request.system_prompt.encode("utf-8")
    tools = _encoded(wire.get("tools", []))
    controls = {
        key: value
        for key, value in wire.items()
        if key not in {
            "request_id", "parent_request_id", "attempt", "metadata", "extensions",
            "input", "system_prompt", "tools",
        }
    }
    prefix = hashlib.sha256()
    prefix.update(_encoded({"system_prompt": request.system_prompt, "tools": wire.get("tools", [])}))
    inputs: list[dict[str, Any]] = []
    for index, item in enumerate(wire.get("input", [])):
        # These envelope fields are regenerated or enriched when history is
        # saved; they are not model text. Call IDs and actual content remain.
        value = {
            key: data
            for key, data in item.items()
            if key not in {"id", "status", "metadata", "extensions", "phase"}
        }
        # Live tool messages default to false while durable records explicitly
        # flag failures. Providers derive their text from content, not this flag.
        if value.get("type") == "tool_result":
            value.pop("is_error", None)
        encoded = _encoded(value)
        prefix.update(len(encoded).to_bytes(8, "big"))
        prefix.update(encoded)
        inputs.append({
            "index": index,
            "type": str(item.get("type") or ""),
            "bytes": len(encoded),
            "sha256": _digest(encoded),
            "prefix_sha256": prefix.hexdigest(),
        })
    return {
        "schema_version": 1,
        "scope": "agent_before_provider_mapping",
        "request_id": request.request_id,
        "iteration": iteration,
        "attempt": attempt,
        "system_prompt_sha256": _digest(system),
        "system_prompt_bytes": len(system),
        "tools_sha256": _digest(tools),
        "controls_sha256": _digest(_encoded(controls)),
        "input_prefix_sha256": prefix.hexdigest(),
        "inputs": inputs,
        "sections": {
            section.name: {
                "sha256": _digest(section.content.encode("utf-8")),
                "bytes": len(section.content.encode("utf-8")),
            }
            for section in bundle.sections
        },
    }


def record_provider_request_diagnostic(
    state: Any, request: KemoRequest, bundle: PromptBundle, iteration: int, attempt: int
) -> None:
    diagnostic = provider_request_diagnostic(
        request, bundle, iteration=iteration, attempt=attempt
    )
    state.provider_request_diagnostics.append(diagnostic)
    request.metadata["prompt_hash"] = diagnostic["system_prompt_sha256"]
