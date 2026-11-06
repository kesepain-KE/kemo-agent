"""Gateway usage takes precedence; DeepSeek counts fill individual missing fields."""
from __future__ import annotations

from collections.abc import Callable
from typing import overload

from provider.protocol.enums import MeasurementMode
from provider.protocol.models import (
    KemoRequest, KemoResponse, KemoResponseBatch, Measurement, Usage,
)
from provider.tokenizer import count_request, count_text, count_value


TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens")


def reported_token_fields(usage: Usage) -> set[str]:
    """A supplied zero is evidence; an explicitly estimated value is not."""
    measurement = usage.measurement
    present = {name for name in TOKEN_FIELDS if getattr(usage, name) is not None}
    exact = set(measurement.exact_fields)
    estimated = set(measurement.estimated_fields)
    if measurement.mode == "estimated":
        present &= exact
    elif measurement.mode == "mixed" and not estimated:
        present &= exact
    return present - estimated


def merge_usage_snapshots(current: Usage, previous: Usage | None) -> Usage:
    """Merge cumulative SSE snapshots by field; never sum them."""
    if previous is None:
        return current
    current_reported, previous_reported = reported_token_fields(current), reported_token_fields(previous)
    updates = {}
    for field in Usage.model_fields:
        if field == "measurement":
            continue
        value, prior = getattr(current, field), getattr(previous, field)
        if value is None or isinstance(value, (dict, list)) and not value:
            updates[field] = prior
        if field in previous_reported and field not in current_reported:
            updates[field] = prior
    exact = current_reported | previous_reported
    if current.total_tokens is None and any(
        field in current_reported and getattr(current, field) != getattr(previous, field)
        for field in ("input_tokens", "output_tokens")
    ):
        updates["total_tokens"] = None
        exact.discard("total_tokens")
    estimated = (
        set(previous.measurement.estimated_fields) | set(current.measurement.estimated_fields)
    ) - exact
    mode = str(current.measurement.mode if current_reported else previous.measurement.mode)
    if estimated:
        mode = "mixed" if exact else "estimated"
    return current.model_copy(update={
        **updates,
        "measurement": Measurement(
            mode=MeasurementMode(mode), exact=not estimated and set(TOKEN_FIELDS) <= exact,
            exact_fields=sorted(exact), estimated_fields=sorted(estimated),
        ),
    })


def fill_usage(
    usage: Usage, *, input_tokens: Callable[[], int], output_tokens: Callable[[], int],
) -> Usage:
    """Normalize field provenance without adding a second copy of any count."""
    exact = reported_token_fields(usage)
    estimated = set(usage.measurement.estimated_fields)
    exact_all = set(usage.measurement.exact_fields) - estimated
    updates = {}
    for field, counter in (("input_tokens", input_tokens), ("output_tokens", output_tokens)):
        if field not in exact:
            updates[field] = counter()
            estimated.add(field)
    if "total_tokens" not in exact:
        input_count = updates.get("input_tokens", usage.input_tokens)
        output_count = updates.get("output_tokens", usage.output_tokens)
        assert input_count is not None and output_count is not None
        updates["total_tokens"] = input_count + output_count
        # The sum of two real reported components remains exact.
        (exact if {"input_tokens", "output_tokens"} <= exact else estimated).add("total_tokens")
    exact_all = (exact_all - set(TOKEN_FIELDS)) | exact
    estimated -= exact_all
    mode = str(usage.measurement.mode)
    if estimated:
        mode = "mixed" if exact_all else "estimated"
    elif mode in {"unknown", "mixed", "estimated"}:
        mode = "provider"
    measurement = Measurement(
        mode=MeasurementMode(mode), exact=not estimated,
        exact_fields=sorted(exact_all), estimated_fields=sorted(estimated),
    )
    if not updates and measurement == usage.measurement:
        return usage
    return usage.model_copy(update={**updates, "measurement": measurement})


def count_output_tokens(output, *, reasoning_tokens: int | None = None) -> int:
    """Count generated content, excluding random IDs, timestamps and opaque state."""
    total, has_reasoning = 0, False
    for item in output:
        if item.type == "reasoning":
            total += count_text(item.content or item.summary or "")
            has_reasoning = has_reasoning or bool(item.content or item.summary)
        elif item.type == "tool_call":
            total += count_text(item.name)
            total += count_value(item.arguments_raw if item.arguments_raw is not None else item.arguments)
        elif item.type == "message":
            total += count_text(item.refusal or "")
            for block in item.content:
                if block.type in {"text", "refusal"}:
                    total += count_text(block.text)
                elif block.type == "json":
                    total += count_value(block.data)
                elif block.type in {"image", "audio", "video", "file"}:
                    total += 1024
    if not has_reasoning and reasoning_tokens is not None:
        total += reasoning_tokens
    return total


@overload
def complete_usage(request: KemoRequest, response: KemoResponse, *, previous: Usage | None = None) -> KemoResponse: ...


@overload
def complete_usage(request: KemoRequest, response: KemoResponseBatch, *, previous: Usage | None = None) -> KemoResponseBatch: ...


def complete_usage(
    request: KemoRequest, response: KemoResponse | KemoResponseBatch, *, previous: Usage | None = None,
) -> KemoResponse | KemoResponseBatch:
    output = (
        [item for choice in response.responses for item in choice.output]
        if isinstance(response, KemoResponseBatch) else response.output
    )
    observed_usage = merge_usage_snapshots(response.usage, previous)
    usage = fill_usage(
        observed_usage,
        input_tokens=lambda: count_request(request.system_prompt, request.input, request.tools),
        output_tokens=lambda: count_output_tokens(output, reasoning_tokens=observed_usage.reasoning_tokens),
    )
    return response if usage is response.usage else response.model_copy(update={"usage": usage})
