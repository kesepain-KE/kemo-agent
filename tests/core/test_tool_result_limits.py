"""Shared model-view limits, with real temporary cards and result artifacts."""
from __future__ import annotations

import json
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from provider.protocol.models import JsonContent, MessageItem, TextContent, ToolCallItem, ToolResultItem
from provider.tokenizer import TOKENIZER_ID
from run.cards import CardMeter, CardStore, IntegrationStore, protocol_items
from run.cards.barrier import prepare_request
from run.tools import MAX_TOOL_RESULT_CHARS, limit_tool_result_items
from tests.type_assertions import require_present


@pytest.mark.parametrize("result,status", [
    ({"ok": True, "matches": [{"content": "界\\\"\n" * 20_000}]}, "completed"),
    ({"ok": False, "error": {"details": ["检索失败" * 40_000]}}, "failed"),
    ({"content": "missing-success-contract" * 10_000}, "failed"),
])
def test_tool_cards_limit_nested_results_without_losing_raw_or_native_linkage(tmp_path, result, status):
    (tmp_path / "users/alice").mkdir(parents=True)
    store = CardStore(tmp_path, "alice")
    integration = IntegrationStore(store, "web", "s1", "i")
    integration.start()
    original = {"name": "custom_search", "arguments": {"path": "large-directory"}}
    execution, _ = integration.register_tool("callid_search", original)
    payload = {"ok": result.get("ok") is True, "result": result}
    card = integration.tool_result(execution["execution_id"], payload, status=status, duration_ms=1)
    assert card.raw_payload["result"] == payload
    assert card.raw_payload["original_call"] == original
    assert card.view_tokens < card.raw_tokens
    view = require_present(card.view_payload)
    assert len(json.dumps(view, ensure_ascii=False)) <= MAX_TOOL_RESULT_CHARS
    receipt = view["result"]
    assert receipt["ok"] is payload["ok"]
    assert receipt["content_omitted"] and receipt["execution_completed"]
    assert json.loads((tmp_path / receipt["artifact"]["path"]).read_text("utf-8")) == payload
    items = protocol_items(store.snapshot("web", "s1").cards)
    assert isinstance(items[0], ToolCallItem)
    assert isinstance(items[-1], ToolResultItem)
    assert items[-1].is_error is (status == "failed")
    assert items[-1].call_id == items[0].call_id
    assert isinstance(items[-1].content[0], JsonContent)
    assert items[-1].content[0].data == view
    # A detached/absorbed call uses the same bounded VIEW as historical data.
    detached = protocol_items((card,))[0]
    assert isinstance(detached, MessageItem)
    assert isinstance(detached.content[0], TextContent)
    assert "detached_tool_result" in detached.content[0].text
    assert len(detached.content[0].text) < MAX_TOOL_RESULT_CHARS
    artifacts = list((tmp_path / "users/alice/download/tool_results").iterdir())
    assert integration.tool_result(
        execution["execution_id"], payload, status=status, duration_ms=1,
    ) == card
    assert list((tmp_path / "users/alice/download/tool_results").iterdir()) == artifacts


def test_huge_original_call_diagnostics_cannot_bypass_result_card_budget(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    store = CardStore(tmp_path, "alice")
    integration = IntegrationStore(store, "web", "s1", "i")
    integration.start()
    original = {
        "name": "write", "arguments": {"content": "input" * 30_000},
        "arguments_raw": "diagnostic" * 30_000,
    }
    execution, _ = integration.register_tool("callid_write", original)
    card = integration.tool_result(execution["execution_id"], {"ok": True}, status="completed", duration_ms=1)
    assert card.raw_payload["original_call"] == original
    view = require_present(card.view_payload)
    assert view["original_call"]["arguments_omitted"]
    assert len(json.dumps(view, ensure_ascii=False)) <= MAX_TOOL_RESULT_CHARS
    items = protocol_items(store.snapshot("web", "s1").cards)
    assert isinstance(items[0], ToolCallItem)
    assert isinstance(items[-1], ToolResultItem)
    assert items[-1].call_id == items[0].call_id
    assert not items[-1].is_error


def test_oversized_running_notice_does_not_claim_execution_completed(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    store = CardStore(tmp_path, "alice")
    integration = IntegrationStore(store, "web", "s1", "i")
    integration.start()
    execution, _ = integration.register_tool("callid_wait", {"name": "wait", "arguments": {}})
    card = require_present(integration.publish_running_notice(
        execution["execution_id"], result={"ok": True, "partial": "pending" * 30_000},
    ))
    view = require_present(card.view_payload)
    assert view["execution"]["execution_live"] is True
    assert view["result"]["execution_completed"] is False
    assert view["result"]["content_omitted"]
    assert integration.pending()[0] == 1


@pytest.mark.parametrize("value", [
    {"ok": False, "error": "failed", "output": "x" * 150_000},
    {"output": "x" * 150_000},
])
def test_late_child_oversized_failure_is_not_promoted_to_success(tmp_path, value):
    from run.agents.loop_safepoints import drain_tool

    (tmp_path / "users/alice").mkdir(parents=True)
    future = Future()
    future.set_result(value)
    payload, status = require_present(drain_tool(
        SimpleNamespace(completion_future=future), tool_name="external", arguments={},
        root=tmp_path, user="alice",
    ))
    assert payload["ok"] is False and status == "failed"
    receipt = payload["result"]
    assert receipt["content_omitted"] and receipt["ok"] is False
    assert json.loads((tmp_path / receipt["artifact"]["path"]).read_text("utf-8")) == value


def test_export_failure_keeps_executed_result_and_explicit_raw_reference(tmp_path, monkeypatch):
    (tmp_path / "users/alice").mkdir(parents=True)
    store = CardStore(tmp_path, "alice")
    store.ensure_session("web", "s1")

    def unavailable(*args):
        raise OSError("download directory is not writable")

    monkeypatch.setattr("run.tools.result_limits.save_result_artifact", unavailable)
    payload = {
        "original_call": {"name": "write", "arguments": {}},
        "execution": {"status": "completed", "execution_live": False},
        "result": {"ok": True, "data": "x" * 150_000},
    }
    card = store.append("web", "s1", "i", "tool_result", payload, tool_call_id="callid_write")
    assert card.raw_payload == payload
    view = require_present(card.view_payload)
    assert view["execution"]["status"] == "completed"
    receipt = view["result"]
    assert receipt["ok"] is True
    assert receipt["raw_card_id"] == card.card_id
    assert receipt["artifact_error"] == "OSError"
    assert "artifact" not in receipt
    assert len(json.dumps(view, ensure_ascii=False)) <= MAX_TOOL_RESULT_CHARS


def test_next_request_repairs_legacy_unbounded_view_and_remeters_only_once(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    legacy = CardStore(tmp_path, "alice", meter=CardMeter(key=f"cardstream-{TOKENIZER_ID}"))
    legacy.ensure_session("web", "s1")
    card = legacy.append("web", "s1", "i", "tool_result", {
        "original_call": {"name": "search", "arguments": {}},
        "execution": {"status": "completed"},
        "result": {"ok": True, "content": "legacy" * 200_000},
    }, tool_call_id="callid_search")
    # Reproduce the pre-fix representation without changing immutable RAW.
    with legacy.db(write=True) as db:
        db.execute("UPDATE context_cards SET view_payload=raw_payload,view_tokens=raw_tokens")
        db.execute("UPDATE card_sessions SET active_tokens=?", (card.raw_tokens,))
    store = CardStore(tmp_path, "alice")
    prepared = prepare_request(
        store, "web", "s1", system_prompt="", dynamic_tail=lambda: {}, tools=[], input_limit=50_000,
    )
    repaired = prepared.snapshot.history.cards[0]
    assert prepared.snapshot.history.meter_key == store.meter.key
    assert repaired.raw_payload_json == card.raw_payload_json
    assert repaired.raw_tokens == card.raw_tokens
    assert require_present(repaired.view_payload)["result"]["content_omitted"]
    assert prepared.occupancy.history_tokens == repaired.view_tokens < card.raw_tokens
    assert prepared.wire_tokens <= 50_000
    artifacts = list((tmp_path / "users/alice/download/tool_results").iterdir())
    assert store.remeter("web", "s1") == prepared.snapshot.history
    assert list((tmp_path / "users/alice/download/tool_results").iterdir()) == artifacts


def test_restored_child_json_and_text_results_use_the_same_final_boundary(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    nested = {"ok": True, "result": {"data": "界\\\n" * 50_000}}
    text = "failure-output" * 20_000
    items = [
        ToolResultItem(id="result_json", call_id="callid_a", name="search",
                       content=[JsonContent(data=nested)]),
        ToolResultItem(id="result_text", call_id="callid_b", name="external", is_error=True,
                       content=[TextContent(text=text)]),
    ]
    context = {"root": str(tmp_path), "user": "alice"}
    limit_tool_result_items(items, context)
    assert items[0].call_id == "callid_a" and not items[0].is_error
    assert items[1].call_id == "callid_b" and items[1].is_error
    for item in items:
        assert isinstance(item.content[0], JsonContent)
        receipt = item.content[0].data
        assert len(json.dumps(receipt, ensure_ascii=False)) <= MAX_TOOL_RESULT_CHARS
        original = json.loads((tmp_path / receipt["artifact"]["path"]).read_text("utf-8"))
        if item.name == "search":
            assert original == nested
        else:
            assert original["content"][0]["text"] == text
    assert isinstance(items[1].content[0], JsonContent)
    assert items[1].content[0].data["ok"] is False
    artifacts = list((tmp_path / "users/alice/download/tool_results").iterdir())
    limit_tool_result_items(items, context)
    assert list((tmp_path / "users/alice/download/tool_results").iterdir()) == artifacts
