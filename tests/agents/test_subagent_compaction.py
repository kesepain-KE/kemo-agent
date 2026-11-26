"""Exercise the child capacity gate against real CardStream and a local Provider."""
from __future__ import annotations

import base64
import json
import shutil
import threading

import pytest

from provider.adapters.compat import kemo_request_to_chat
from provider.protocol.enums import ErrorCode, ResponseStatus
from provider.protocol.models import (
    Item, KemoResponse, MessageItem, ModelCapabilities, ProviderState, ReasoningItem, ToolCallItem,
    ToolResultItem, UnifiedError, Usage,
)
from provider.schema import ProviderError
from run.agents import AgentCancelledError, AgentProviderError, AgentRunner, AgentScheduler, TaskCoordinator
from run.agents.card_stream import SOURCE, agent_store
from run.cards import CardMeter, CardStore, IntegrationStore, estimate_request_tokens
from run.cards.meter import request_calibration_key
from run.conversation.provider_exchange import ProviderResponseError
from tests.agents.test_general_worker import OUTPUT, REPOSITORY, workspace  # noqa: F401
from tests.agents.test_subagent_continuation import call, write_plugin
from tests.media_input_support import PNG, native_blocks


LIMIT = 5200
TASK = "agent-task-" + "8" * 32
MARKER = "child-only-verified-result-a38b"
SUMMARY = {"checkpoint": MARKER}


def tokens(request):
    return estimate_request_tokens(request.system_prompt, request.input, request.tools)


def raw_rows(store):
    return tuple((c.card_id, c.seq, c.integration_id, c.raw_kind, c.raw_payload_json, c.raw_tokens)
                 for c in store.raw_page(SOURCE, store.path.parent.name))


def paired(request):
    calls = [i.call_id for i in request.input if isinstance(i, ToolCallItem)]
    results = [i.call_id for i in request.input if isinstance(i, ToolResultItem)]
    assert calls == results


def round_output(i, size):
    return [MessageItem.text("assistant", MARKER + " " + "verified result " * size,
                             phase="commentary"), call(value=f"effect-{i}", index=i)]


class SmallProvider:
    def __init__(self, store, steps, *, summarize=None, limit=LIMIT):
        self.store, self.steps, self.limit = store, iter(steps), limit
        self.summarize = summarize or (lambda _: SUMMARY)
        self.business, self.summaries = [], []
        self.before, self.after = None, None

    def capabilities(self, model, **kwargs):
        return ModelCapabilities.model_validate({
            "protocol_version": "2.0", "model": model, "provider_id": "child-compaction-fixture",
            "provider_model": model, "input_modalities": ["text", "image"],
            "limits": {"max_input_tokens": self.limit, "context_window": self.limit + 2048,
                       "max_output_tokens": 1024},
        })

    def capture(self):
        return self.store.snapshot(SOURCE, self.store.path.parent.name), raw_rows(self.store)

    def create(self, request):
        assert tokens(request) <= self.limit
        summary = request.tool_choice.mode == "none"
        if summary:
            assert not any(isinstance(item, ToolCallItem) for item in request.input)
            if self.before is None:
                self.before = self.capture()
            self.summaries.append(request)
            value = self.summarize(request)
        else:
            assert request.metadata["capability"] == "conversation"
            task_id = request.metadata["task_id"]
            if task_id != self.store.path.parent.name:
                self.store = agent_store(self.store.root, self.store.user, task_id)
            paired(request)
            if self.summaries and self.after is None:
                self.after = self.capture()
            self.business.append(request)
            value = next(self.steps)
            if callable(value):
                value = value(request)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, KemoResponse):
            return value
        output: list[Item]
        if isinstance(value, list):
            output = value
        else:
            text = json.dumps(value) if isinstance(value, dict) else value
            assert isinstance(text, str)
            output = [MessageItem.text("assistant", text)]
        return KemoResponse(request_id=request.request_id, model=request.model, status=ResponseStatus.COMPLETED,
                            output=output, usage=Usage(input_tokens=tokens(request),
                                                       output_tokens=17 if summary else 3))


@pytest.fixture
def scenario(workspace):
    root, config = workspace
    write_plugin(root)
    # Each fake gateway advertises its own limits under a distinct cache identity.
    config["provider"]["base_url"] = f"http://127.0.0.1:1/child-compaction/{root.name}"
    config["agents"] = {"token_limit": LIMIT + 2048, "output_reserve": 256}
    config["provider_runtime"] = {"max_retries": 0}
    return root, config, agent_store(root, "alice", TASK)


def run_child(scenario, provider, **kwargs):
    root, config, _ = scenario
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    return runner.run("general_worker", {"task": "Verify the results."}, task_id=TASK,
                      source="web", session_id="scope", timeout=30, **kwargs)


def test_compaction_reduces_the_actual_child_request_without_touching_raw_or_memory(scenario):
    from plugins.memory_manage.tool import run as memory_manage
    from run.history import claim_pending_memory

    root, config, store = scenario
    config["memory"] = {"enabled": True}
    (root / "config/global_config.yaml").write_text(json.dumps(config), encoding="utf-8")
    parent = CardStore(root, "alice")
    IntegrationStore(parent, "web", "scope", "main").start()
    parent.append("web", "scope", "main", "user_text", {"text": "Parent request"})
    memory_context = {"root": str(root), "user": "alice"}
    memory_manage("add", "seven_days", filename="fixture.md", content="Parent memory stays visible.",
                  memory_type="fact", context=memory_context)
    provider = SmallProvider(store, [*(round_output(i, 85) for i in range(1, 4)),
                                     round_output(4, 2350), OUTPUT])

    result = run_child(scenario, provider)

    assert result.data == OUTPUT
    assert provider.before is not None and provider.after is not None
    before, raw_before = provider.before
    after, raw_after = provider.after
    assert before.view_epoch == 0 and after.view_epoch == 1
    assert len(before.cards) >= 10 and len(after.cards) < len(before.cards)
    assert after.history_tokens < before.history_tokens and raw_after == raw_before
    assert before.generation == after.generation
    assert after.cards[0].view_kind == "summary" and after.cards[0].view_payload == SUMMARY
    assert len(provider.business) == 5 and provider.summaries
    next_request = provider.business[-1]
    assert any("rolling_summary" in item.model_dump_json() and MARKER in item.model_dump_json()
               for item in next_request.input)
    assert tokens(next_request) <= LIMIT
    assert (root / "effects.txt").read_text().splitlines() == [f"effect-{i}" for i in range(1, 5)]
    assert result.usage["provider_request_count"] == len(provider.business) + len(provider.summaries)
    assert result.usage["compression_provider_request_count"] == len(provider.summaries)
    assert result.usage["compression_output_tokens"] == 17 * len(provider.summaries)
    assert result.usage["output_tokens"] == 3 * len(provider.business) + 17 * len(provider.summaries)
    assert result.usage["input_tokens"] == sum(map(tokens, [*provider.summaries, *provider.business]))

    listed = memory_manage("list", "all", include_content=True, context=memory_context)
    assert "Parent memory stays visible" in json.dumps(listed) and MARKER not in json.dumps(listed)
    assert [card.raw_payload["text"] for card in parent.raw_page("web", "scope")] == ["Parent request"]
    assert claim_pending_memory(root, "alice", source=SOURCE, session_id=TASK) is None
    print(f"epoch={before.view_epoch}->{after.view_epoch}, cards={len(before.cards)}->{len(after.cards)}, "
          f"history_tokens={before.history_tokens}->{after.history_tokens}, "
          f"RAW={len(raw_before)}->{len(raw_after)}, request={tokens(next_request)}/{LIMIT}, "
          f"summary_requests={len(provider.summaries)}")


def test_compaction_retires_reasoning_with_its_tool_round(scenario):
    root, config, store = scenario

    def turn(i):
        summary = f"private-reasoning-{i}"
        state = ProviderState(kind="opaque", provider="fixture", model=config["provider"]["model"],
                              data=f"opaque-step-{i}")
        return [ReasoningItem(id="rs_reused", summary=summary, provider_state=state),
                *round_output(i, 2350 if i == 4 else 85)]

    provider = SmallProvider(store, [*(turn(i) for i in range(1, 9)), OUTPUT])
    assert run_child(scenario, provider).data == OUTPUT
    assert provider.before is not None and provider.after is not None
    before, raw_before = provider.before
    after, raw_after = provider.after
    assert before.view_epoch == 0 and after.view_epoch == 1
    assert raw_before == raw_after
    assert [card.view_kind for card in after.cards] == ["summary", "tool_call", "tool_result"]

    counts = []
    for request in provider.business:
        ids = [item.arguments["value"].removeprefix("effect-") for item in request.input
               if isinstance(item, ToolCallItem)]
        reasoning = [item for item in request.input if isinstance(item, ReasoningItem)]
        assert [item.summary for item in reasoning] == [f"private-reasoning-{i}" for i in ids]
        states = []
        for item in reasoning:
            state = item.provider_state
            assert state is not None
            states.append(state.data)
        assert states == [f"opaque-step-{i}" for i in ids]
        messages = kemo_request_to_chat(request, deepseek=True).messages
        assert [msg["reasoning_content"] for msg in messages if msg.get("tool_calls")] == [
            f"private-reasoning-{i}" for i in ids
        ]
        counts.append(len(reasoning))
    assert counts == [0, 1, 2, 3, 1, 2, 3, 4, 5]
    compacted = provider.business[4].input
    at = next(i for i, item in enumerate(compacted) if isinstance(item, ReasoningItem))
    assert isinstance(compacted[at + 1], ToolCallItem)
    assert compacted[at + 1].arguments == {"value": "effect-4"}
    for marker in ("private-reasoning-", "opaque-step-"):
        assert all(marker not in request.model_dump_json() for request in provider.summaries)
        assert all(marker not in card[4] for card in raw_rows(store))
    assert (root / "effects.txt").read_text().splitlines() == [f"effect-{i}" for i in range(1, 9)]
    print(f"reasoning_items={counts}")


def test_failed_preventative_summary_keeps_reasoning_for_the_original_request(scenario):
    root, _, store = scenario
    reasoning = ReasoningItem(id="rs_fixture", content="reasoning kept after rejected summary")
    provider = SmallProvider(store, [[reasoning, *round_output(1, 1625)], OUTPUT],
                             summarize=lambda _: "not a structured checkpoint")

    assert run_child(scenario, provider).data == OUTPUT
    assert provider.before is not None and provider.after == provider.before
    assert len(provider.business) == 2
    assert LIMIT * .8 < tokens(provider.business[-1]) <= LIMIT
    replayed = [item for item in provider.business[-1].input if isinstance(item, ReasoningItem)]
    assert [item.content for item in replayed] == [reasoning.content]
    assert (root / "effects.txt").read_text().splitlines() == ["effect-1"]


@pytest.mark.parametrize("candidate", ["not a structured checkpoint", {"checkpoint": "large " * 9000}])
def test_mandatory_compaction_rejects_invalid_or_oversized_checkpoint(scenario, candidate):
    root, _, store = scenario
    provider = SmallProvider(store, [round_output(1, 3100)], summarize=lambda _: candidate)

    with pytest.raises(AgentProviderError) as caught:
        run_child(scenario, provider)

    assert caught.value.code == "summary_input_budget_exceeded"
    assert provider.before is not None and provider.capture() == provider.before
    assert len(provider.business) == len(provider.summaries) == 1
    assert caught.value.partial_result.usage["compression_output_tokens"] == 17
    assert (root / "effects.txt").read_text().splitlines() == ["effect-1"]


@pytest.mark.parametrize("failure", ["network", "network_terminal", "cancelled"])
def test_mandatory_compaction_preserves_network_and_cancellation_errors(scenario, failure):
    root, _, store = scenario
    cancelled = threading.Event()

    def summarize(request):
        if failure == "cancelled":
            cancelled.set()
            return SUMMARY
        if failure == "network":
            raise ConnectionError("fixture network unavailable")
        return KemoResponse(request_id=request.request_id, status=ResponseStatus.FAILED, model=request.model,
                            error=UnifiedError(code=ErrorCode.PROVIDER_UNAVAILABLE, type="network_error",
                                               message="fixture network unavailable", retryable=True))

    provider = SmallProvider(store, [round_output(1, 3100)], summarize=summarize)
    expected = {"network": ConnectionError, "network_terminal": ProviderResponseError,
                "cancelled": AgentCancelledError}
    with pytest.raises(expected[failure]) as caught:
        run_child(scenario, provider, cancel_event=cancelled)

    assert "summary_input_budget_exceeded" != getattr(caught.value, "code", None)
    if failure != "cancelled":
        assert "network" in str(caught.value).lower()
    assert provider.before is not None and provider.capture() == provider.before
    assert len(provider.business) == 1 and 1 <= len(provider.summaries) <= 6
    assert (root / "effects.txt").read_text().splitlines() == ["effect-1"]


@pytest.mark.parametrize("failure", ["overflow", "overflow_terminal", "http_413", "unrelated_400"])
def test_only_explicit_provider_context_overflow_compacts_and_retries(scenario, failure):
    root, _, store = scenario

    def reject(request):
        if failure == "overflow_terminal":
            return KemoResponse(request_id=request.request_id, status=ResponseStatus.FAILED, model=request.model,
                                error=UnifiedError(code=ErrorCode.PROVIDER_BAD_RESPONSE, type="context_length_exceeded",
                                                   message="Maximum context length exceeded", provider_status=400))
        status = 413 if failure == "http_413" else 400
        message = "Malformed request" if failure == "unrelated_400" else "Maximum context length exceeded"
        raise ProviderError(message, status_code=status)

    provider = SmallProvider(store, [round_output(1, 820), reject, OUTPUT])
    if failure.startswith("overflow"):
        result = run_child(scenario, provider)
        assert result.data == OUTPUT and provider.summaries
        assert len(provider.business) == 3 and provider.before is not None and provider.after is not None
        rejected, recovered = provider.business[1:]
        assert recovered.metadata["iteration"] == rejected.metadata["iteration"]
        assert "rolling_summary" in recovered.model_dump_json()
        assert tokens(recovered) <= int(tokens(rejected) * .7)
        assert provider.before[1] == provider.after[1]
    else:
        with pytest.raises(ProviderError) as caught:
            run_child(scenario, provider)
        assert caught.value.status_code == (413 if failure == "http_413" else 400)
        assert not provider.summaries and len(provider.business) == 2
    assert (root / "effects.txt").read_text().splitlines() == ["effect-1"]


def test_compaction_does_not_send_or_persist_native_media_in_its_summary(scenario):
    root, config, store = scenario
    shutil.copytree(REPOSITORY / "plugins/attach_media", root / "plugins/attach_media")
    config["provider"].update(type="chat", input_modalities=["text", "image"])
    image = root / "users/alice/download/screen.png"
    image.write_bytes(PNG)
    attach = ToolCallItem(id="call_attach", call_id="callid_attach", name="attach_media",
                          arguments={"paths": [str(image)]})
    provider = SmallProvider(store, [[round_output(1, 2900)[0], attach], round_output(2, 20), OUTPUT])

    assert run_child(scenario, provider).data == OUTPUT
    assert provider.summaries and provider.after is not None and provider.after[0].view_epoch == 1
    assert [len(native_blocks(request)) for request in provider.business] == [0, 1, 0]
    encoded = base64.b64encode(PNG).decode("ascii")
    assert all(not native_blocks(request) and encoded not in request.model_dump_json()
               for request in provider.summaries)
    assert all(encoded not in card[4] for card in raw_rows(store))
    media_request = provider.business[1]
    meter = CardMeter.for_model("chat", media_request.model)
    with store.db() as db:
        key = db.execute("SELECT meter_key FROM card_meter_observations WHERE request_id=?",
                         (media_request.request_id,)).fetchone()[0]
    assert key == request_calibration_key(meter.key, meter.calibration_scope, media_request.input)
    assert key != request_calibration_key(meter.key, meter.calibration_scope, ())


def test_compacted_child_resumes_in_the_same_store_with_new_model_calibration(scenario):
    root, config, unused = scenario
    parent_model, child_a, child_b = "parent-model", "child-model-a", "child-model-b"
    config["provider"]["model"] = parent_model
    parent_meter, meter_a, meter_b = (CardMeter.for_model("kemo", model)
                                     for model in (parent_model, child_a, child_b))
    owner = TaskCoordinator(root, "alice", "web", "scope", "parent", {"prompt": "work"})
    ownership = owner.ownership("dispatch")
    calibrated = []
    stopped = [call(value="must-not-start", index=2)]

    def resume_reply(request):
        assert request.model == child_b and "rolling_summary" in request.model_dump_json()
        calibrated.append((first_store.calibrated_tokens(1000), second_store.calibrated_tokens(1000)))
        return OUTPUT

    class MeteredProvider(SmallProvider):
        def create(self, request):
            response = super().create(request)
            if request.model == child_a and request.tool_choice.mode != "none":
                return response.model_copy(update={"usage": Usage(input_tokens=tokens(request) * 2,
                                                                   output_tokens=3)})
            return response

    provider = MeteredProvider(unused, [round_output(1, 2350), stopped,
                                       {**OUTPUT, "status": "partial"}, resume_reply])
    scheduler = AgentScheduler.from_runner(AgentRunner(root, "alice", config=config,
                                                         provider_factory=lambda _: provider))
    try:
        task_id = scheduler.submit("general_worker", {"task": "work"}, source="web", session_id="scope",
                                   ownership=ownership, allow_sync=True, timeout=30, config=config,
                                   model_override=child_a, max_tool_calls=1)
        scheduler.wait(task_id, 40, source="web", session_id="scope")
        assert owner.ledger.get(task_id, "web", "scope")["status"] == "tool_limit"
        first_store = agent_store(root, "alice", task_id, meter=meter_a)
        second_store = agent_store(root, "alice", task_id, meter=meter_b)
        old_view = first_store.snapshot(SOURCE, task_id)
        old_raw = raw_rows(first_store)
        assert old_view.view_epoch > 0 and old_view.cards[0].view_payload == SUMMARY
        assert first_store.path == second_store.path
        assert parent_meter.key == meter_a.key == meter_b.key == old_view.meter_key
        assert len({parent_meter.calibration_scope, meter_a.calibration_scope, meter_b.calibration_scope}) == 3
        new_config = {**config, "provider": {**config["provider"], "model": child_b}}
        scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership,
                         config=new_config, timeout=30, max_tool_calls=0)
        assert scheduler.wait(task_id, 40, source="web", session_id="scope").data == OUTPUT
        new_view = second_store.snapshot(SOURCE, task_id)
        assert new_view.generation == old_view.generation and new_view.view_epoch == old_view.view_epoch
        assert new_view.cards[0] == old_view.cards[0]
        assert raw_rows(second_store)[:len(old_raw)] == old_raw
        assert calibrated == [(2000, 1000)]
        with second_store.db() as db:
            keys = {row[0] for row in db.execute("SELECT meter_key FROM card_meter_observations")}
        assert keys == {request_calibration_key(meter.key, meter.calibration_scope, ())
                        for meter in (meter_a, meter_b)}
        assert (root / "effects.txt").read_text().splitlines() == ["effect-1"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)
