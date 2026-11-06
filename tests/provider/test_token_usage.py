from __future__ import annotations

import pytest

from provider.protocol.enums import MeasurementMode
from provider.protocol.models import KemoRequest, KemoResponse, KemoResponseBatch, MessageItem, ReasoningItem, ToolCallItem, Usage
from provider.token_usage import complete_usage, merge_usage_snapshots, reported_token_fields
from provider.tokenizer import count_text, count_value, count_request


def request() -> KemoRequest:
    return KemoRequest.create(
        model="model-a",
        system_prompt="system",
        input=[MessageItem.text("user", "hello")],
        tools=[],
        stream=False,
    )


def test_missing_usage_uses_the_shared_deepseek_counter_and_marks_estimates():
    wire_request = request()
    response = KemoResponse(
        request_id=wire_request.request_id,
        model=wire_request.model,
        status="completed",
        output=[MessageItem.text("assistant", "world")],
    )

    completed = complete_usage(wire_request, response)

    assert completed.usage.input_tokens == count_value({
        "system_prompt": "system",
        "input": [wire_request.input[0].model_dump(mode="json", exclude_none=True)],
        "tools": [],
    })
    assert completed.usage.output_tokens == count_text("world")
    assert completed.usage.total_tokens == (
        completed.usage.input_tokens + completed.usage.output_tokens
    )
    assert completed.usage.measurement.mode == MeasurementMode.ESTIMATED
    assert completed.usage.measurement.estimated_fields == [
        "input_tokens", "output_tokens", "total_tokens"
    ]


def test_gateway_exact_fields_are_preserved_and_only_missing_fields_are_filled():
    wire_request = request()
    response = KemoResponse(
        request_id=wire_request.request_id,
        model=wire_request.model,
        status="completed",
        output=[MessageItem.text("assistant", "world")],
        usage={
            "input_tokens": 17,
            "measurement": {
                "mode": "gateway",
                "exact": False,
                "exact_fields": ["input_tokens"],
            },
        },
    )

    completed = complete_usage(wire_request, response)

    assert completed.usage.input_tokens == 17
    assert completed.usage.output_tokens == count_text("world")
    assert completed.usage.total_tokens == (
        completed.usage.input_tokens + completed.usage.output_tokens
    )
    assert completed.usage.measurement.mode == MeasurementMode.MIXED
    assert completed.usage.measurement.exact_fields == ["input_tokens"]
    assert completed.usage.measurement.estimated_fields == ["output_tokens", "total_tokens"]


def test_complete_gateway_usage_is_returned_without_reinterpretation():
    wire_request = request()
    response = KemoResponse(
        request_id=wire_request.request_id,
        model=wire_request.model,
        status="completed",
        output=[MessageItem.text("assistant", "world")],
        usage={
            "input_tokens": 17,
            "output_tokens": 9,
            "total_tokens": 26,
            "measurement": {
                "mode": "gateway",
                "exact": True,
                "exact_fields": ["input_tokens", "output_tokens", "total_tokens"],
            },
        },
    )

    assert complete_usage(wire_request, response).usage.model_dump() == response.usage.model_dump()


def test_exact_gateway_measurement_without_field_list_preserves_reported_values():
    wire_request = request()
    response = KemoResponse(
        request_id=wire_request.request_id,
        model=wire_request.model,
        status="completed",
        output=[MessageItem.text("assistant", "world")],
        usage={
            "input_tokens": 17,
            "output_tokens": 9,
            "total_tokens": 26,
            "measurement": {"mode": "gateway", "exact": True},
        },
    )

    completed = complete_usage(wire_request, response)

    assert (completed.usage.input_tokens, completed.usage.output_tokens, completed.usage.total_tokens) == (
        17, 9, 26,
    )


@pytest.mark.parametrize("raw", [
    {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    {"input_tokens": 17, "output_tokens": 9, "total_tokens": 26},
    {"input_tokens": 17, "output_tokens": 9},
])
def test_reported_values_without_measurement_are_preserved_without_local_counting(raw, monkeypatch):
    def unexpected_count(*args, **kwargs):
        pytest.fail("Complete reported usage must not invoke the local tokenizer")

    monkeypatch.setattr("provider.token_usage.count_request", unexpected_count)
    monkeypatch.setattr("provider.token_usage.count_output_tokens", unexpected_count)
    wire_request = request()
    response = KemoResponse(
        request_id=wire_request.request_id, model=wire_request.model,
        status="completed", usage=raw, output=[MessageItem.text("assistant", "world")],
    )
    completed = complete_usage(wire_request, response)
    assert completed.usage.input_tokens == raw["input_tokens"]
    assert completed.usage.output_tokens == raw["output_tokens"]
    assert completed.usage.total_tokens == raw.get("total_tokens", raw["input_tokens"] + raw["output_tokens"])
    assert completed.usage.measurement.exact


def test_explicit_estimates_are_replaced_and_never_become_calibration_evidence():
    wire_request = request()
    response = KemoResponse(
        request_id=wire_request.request_id, model=wire_request.model, status="completed",
        output=[MessageItem.text("assistant", "world")],
        usage={
            "input_tokens": 999999, "output_tokens": 999999, "total_tokens": 1999998,
            "measurement": {"mode": "estimated"},
        },
    )
    completed = complete_usage(wire_request, response)
    assert completed.usage.input_tokens == count_request("system", wire_request.input, [])
    assert completed.usage.output_tokens == count_text("world")
    assert not reported_token_fields(completed.usage)
    assert complete_usage(wire_request, completed).usage == completed.usage


def test_partial_estimates_keep_exact_input_including_zero_and_preserve_cache():
    wire_request = request()
    response = KemoResponse(
        request_id=wire_request.request_id, model=wire_request.model, status="completed",
        output=[MessageItem.text("assistant", "world")],
        usage={
            "input_tokens": 0, "output_tokens": 9999, "cached_input_tokens": 0,
            "measurement": {
                "mode": "mixed", "exact_fields": ["input_tokens"],
                "estimated_fields": ["output_tokens"],
            },
        },
    )
    completed = complete_usage(wire_request, response)
    assert completed.usage.input_tokens == completed.usage.cached_input_tokens == 0
    assert completed.usage.output_tokens == count_text("world")
    assert reported_token_fields(completed.usage) == {"input_tokens"}


def test_output_counts_reasoning_and_tools_without_charging_random_protocol_ids():
    wire_request = request()
    output = [
        MessageItem.text("assistant", "world"),
        ReasoningItem(id="rs_a", content="check"),
        ToolCallItem(id="call_a", call_id="callid_a", name="lookup", arguments={"q": "hello"}),
    ]
    response = KemoResponse(
        request_id=wire_request.request_id, model=wire_request.model, status="requires_action",
        output=output,
    )
    first = complete_usage(wire_request, response).usage
    response = response.model_copy(update={
        "output": [output[0].model_copy(update={"id": "msg_" + "z" * 100}), *output[1:]],
    })
    assert complete_usage(wire_request, response).usage.output_tokens == first.output_tokens
    assert first.output_tokens == (
        count_text("world") + count_text("check") + count_text("lookup") + count_value({"q": "hello"})
    )


def test_batch_fallback_counts_the_input_once_and_all_generated_choices():
    wire_request = request()
    responses = [
        KemoResponse(
            request_id=wire_request.request_id, model=wire_request.model, status="completed",
            choice_index=index, choice_count=2, output=[MessageItem.text("assistant", text)],
        ) for index, text in enumerate(("hello", "world"))
    ]
    response = KemoResponseBatch(
        request_id=wire_request.request_id, model=wire_request.model, responses=responses,
    )
    completed = complete_usage(wire_request, response)
    assert completed.usage.input_tokens == count_request("system", wire_request.input, [])
    assert completed.usage.output_tokens == count_text("hello") + count_text("world")


def test_aggregate_measurement_tracks_estimated_fields_from_every_request():
    from run.infra import merge_usage_dict, new_usage_total

    total = new_usage_total()
    merge_usage_dict(total, {
        "input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
        "measurement": {"mode": "mixed", "exact_fields": ["input_tokens"],
                        "estimated_fields": ["output_tokens", "total_tokens"]},
    })
    merge_usage_dict(total, {
        "input_tokens": 20, "output_tokens": 3, "total_tokens": 23,
        "measurement": {"mode": "mixed", "exact_fields": ["output_tokens"],
                        "estimated_fields": ["input_tokens", "total_tokens"]},
    })
    assert total["total_tokens"] == 35
    assert total["input_tokens"] == 30
    assert total["measurement"]["exact_fields"] == []
    assert set(total["measurement"]["estimated_fields"]) == {
        "input_tokens", "output_tokens", "total_tokens",
    }


def test_multiple_reported_sources_do_not_turn_exact_aggregate_into_local_estimate():
    from run.infra import merge_usage_dict, new_usage_total, usage_from_dict

    total = new_usage_total()
    for mode in ("gateway", "provider"):
        merge_usage_dict(total, {
            "input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
            "measurement": {"mode": mode, "exact": True,
                            "exact_fields": ["input_tokens", "output_tokens", "total_tokens"]},
        })
    assert total["measurement"]["mode"] == "mixed"
    assert total["measurement"]["exact"]
    assert not total["estimated"]
    assert not usage_from_dict(total).estimated


def test_updated_stream_components_invalidate_old_total_instead_of_reusing_stale_snapshot():
    wire_request = request()
    previous = Usage(input_tokens=17, output_tokens=2, total_tokens=19)
    current = merge_usage_snapshots(Usage(output_tokens=5), previous)
    response = KemoResponse(
        request_id=wire_request.request_id, model=wire_request.model, status="completed",
        output=[MessageItem.text("assistant", "world")], usage=current,
    )
    completed = complete_usage(wire_request, response)
    assert completed.usage.input_tokens == 17
    assert completed.usage.output_tokens == 5
    assert completed.usage.total_tokens == 22
    assert completed.usage.measurement.exact
