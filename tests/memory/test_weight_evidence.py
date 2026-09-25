"""Host evidence, reference binding, replay and UI reporting regressions."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.self_improve.executor import execute
from plugins.memory_manage.tool import run as memory_tool
from run.agents import AgentRunResult
from run.memory import MemoryStore
from run.memory.analysis import (
    extract_round_memory, memory_batch_operation_id, memory_round_data, persist_round_memory_analysis,
)
from run.memory.evidence import bind_reference, quoted_rounds, search_receipts, trusted_dates
from run.memory.pipeline import memory_round_payload
from web.services.memory import MemoryServiceMixin


NOW = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)  # Shanghai Sep 25
BODY = "用户喜欢简洁回答。"


@pytest.fixture
def store(tmp_path):
    (tmp_path / "users" / "alice").mkdir(parents=True)
    (tmp_path / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "global_config.json").write_text("{}", "utf-8")
    return MemoryStore(tmp_path, "alice", {})


def candidate(**changes):
    return {"action": "create", "filename": "style.md", "content": BODY,
            "durable": True, "evidence": "我喜欢简洁回答", "evidence_rounds": [1], **changes}


def rounds():
    return [{"round": n, "committed_at": f"2026-09-{19+n}T20:00:00+08:00",
             "messages": [{"role": "user", "content": "我喜欢简洁回答"}]}
            for n in range(1, 5)]


def seed(store):
    store.upsert_candidates([candidate()], source={"evidence_dates": {"1": "2026-09-20"}}, now=NOW)


def receipts():
    return {"seven_days:style.md": {"memory_ref": "seven_days:style.md",
                                    "filename": "style.md", "content": BODY}}


def model_result(candidates, hits=None):
    metadata = {"tool_calls": [{"name": "memory_manage", "result": {"ok": True, "result": {
        "action": "search_many", "results": [{"matches": list((hits or {}).values())}]
    }}}]}
    return AgentRunResult(agent="self_improve", data={"candidates": candidates},
                          raw_text="", usage={}, model="test", metadata=metadata)


def run_executor(store, candidates, hits=None, input_rounds=None):
    context = SimpleNamespace(runner=SimpleNamespace(root=store.root, user="alice", config={}),
                              run_model=lambda _: model_result(candidates, hits))
    return execute(context, {"trigger": "context_compression", "rounds": input_rounds or rounds()})


def test_delayed_four_days_create_zero_then_three_and_replay(store):
    source = {"evidence_dates": trusted_dates(rounds())}
    result = store.upsert_candidates([candidate(evidence_rounds=[1, 2, 3, 4])],
                                     source=source, now=NOW, operation_id="four-days")
    assert len(result["weight_events"]) == 3
    assert result["weighted"] == ["style.md"]
    assert store.get_entry("seven_days", "style.md")["weight"] == 3
    assert store.upsert_candidates([], source=source, now=NOW, operation_id="four-days")["replayed"]
    again = bind_reference(candidate(action="reinforce", evidence_rounds=[1, 2, 3, 4]), receipts(), store)
    replay = store.upsert_candidates([again], source=source, now=NOW)
    assert replay["weighted"] == []
    assert replay["daily_locked"] == ["style.md"]
    assert store.get_entry("seven_days", "style.md")["weight"] == 3


def test_union_duplicate_candidates_preserves_distinct_evidence_days(store):
    analysis = {"status": "completed", "source": {"evidence_dates": trusted_dates(rounds())},
                "candidates": [candidate(evidence_rounds=[n]) for n in (1, 2, 3, 4)]}
    result = persist_round_memory_analysis(root=store.root, user="alice", config={}, analysis=analysis)
    assert result["status"] == "completed"
    assert result["persisted_candidate_count"] == 1
    assert store.get_entry("seven_days", "style.md")["weight"] == 3


def test_compat_compression_uses_same_evidence_union_and_replay(store):
    from run.memory.pipeline import extract_compressed_round_memory

    runner = SimpleNamespace(run=lambda *args, **kwargs: model_result([
        candidate(evidence_rounds=[n]) for n in (1, 2, 3, 4)]))
    result = extract_compressed_round_memory(root=store.root, user="alice", config={}, rounds=rounds(),
                                            trigger="manual", agent_runner=runner, session_id="compat")
    assert len(result.metadata["memory_update"]["weight_events"]) == 3
    again = extract_compressed_round_memory(root=store.root, user="alice", config={}, rounds=rounds(),
                                           trigger="manual", agent_runner=runner, session_id="compat")
    assert again.metadata["memory_update"]["replayed"] is True
    assert store.get_entry("seven_days", "style.md")["weight"] == 3


def test_extraction_through_executor_tool_receipt_and_transaction(store, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr("run.memory.utc_now", lambda: clock[0])

    class Runner:
        root, user, config = store.root, "alice", {}

        def run(self, name, input_data, **kwargs):
            def model(model_input):
                search = memory_tool("search_many", "all", queries=[{"title": "style"}],
                                     context={"root": str(store.root), "user": "alice", "agent": "self_improve"})
                matches = search["results"][0]["matches"]
                data = candidate(action="upsert", evidence_rounds=[model_input["rounds"][0]["round"]])
                return model_result([data], {hit["memory_ref"]: hit for hit in matches})
            return execute(SimpleNamespace(runner=self, run_model=model), input_data)

    def extract(number):
        return extract_round_memory(root=store.root, user="alice", config={}, round_number=number,
                                    prompt="我喜欢简洁回答", text="ok", reasoning="", tool_records=[],
                                    committed_at=clock[0].isoformat(), agent_runner=Runner(), cancel_event=None,
                                    agent_source="web", session_id="real-chain")

    assert extract(1)["persisted"]["created"] == ["style.md"]
    # The second model decision now sees an existing row, but the same operation
    # still replays creation rather than turning it into a reinforcement.
    assert extract(1)["persisted"]["replayed"] is True
    assert store.get_entry("seven_days", "style.md")["weight"] == 0
    clock[0] += timedelta(days=1)
    assert extract(2)["persisted"]["weighted"] == ["style.md"]
    assert store.get_entry("seven_days", "style.md")["weight"] == 1


def test_reinforce_binds_full_receipt_and_cannot_rewrite(store):
    seed(store)
    result = run_executor(store, [candidate(action=" REINFORCE ", content="malicious replacement",
                                          expected_content_hash="forged", evidence_dates=["2000-01-01"])], receipts())
    bound = result.data["candidates"][0]
    assert bound["content"] == BODY
    assert bound["expected_content_hash"] != "forged"
    assert "evidence_dates" not in bound
    outcome = store.upsert_candidates([bound], source={"evidence_dates": {"1": "2026-09-21"}}, now=NOW)
    assert outcome["weighted"] == ["style.md"]
    assert outcome["content_updated"] == []
    assert store.get_entry("seven_days", "style.md")["content"] == BODY


@pytest.mark.parametrize("changes,reason", [
    ({"action": "reinforce"}, "existing_memory_requires_search"),
    ({"memory_ref": "seven_days:fake.md"}, "unverified_memory_ref"),
    ({"filename": "other", "memory_ref": "seven_days:style.md"}, "reference_filename_mismatch"),
    ({"action": "create", "memory_ref": "seven_days:style.md"}, "create_conflicts_with_existing"),
])
def test_unverified_targets_rejected(store, changes, reason):
    seed(store)
    result = run_executor(store, [candidate(**changes)], None if "memory_ref" not in changes else receipts())
    assert result.data["candidates"] == []
    assert result.metadata["candidate_filter"]["reasons"][reason] == 1


@pytest.mark.parametrize("stale_kind", ["content", "tier"])
def test_concurrent_revision_or_promotion_rejects_stale_receipt(store, stale_kind):
    seed(store)
    bound = bind_reference(candidate(action="revise", content="新版偏好"), receipts(), store)
    if stale_kind == "content":
        store.edit_fragment("seven_days", "style.md", "已由用户修改", now=NOW)
    else:
        store._promote_location(store.locate("style.md"), "one_month", NOW)
    result = store.upsert_candidates([bound], source={"evidence_dates": {"1": "2026-09-22"}}, now=NOW)
    assert result["rejected"] == 1
    assert result["weighted"] == []


def test_out_of_order_days_do_not_regress_latest_date(store):
    seed(store)
    bound = bind_reference(candidate(action="reinforce"), receipts(), store)
    for day in ("2026-09-24", "2026-09-21"):
        store.upsert_candidates([bound], source={"evidence_dates": {"1": day}}, now=NOW)
    item = store.get_entry("seven_days", "style.md")
    assert item["last_weight_date"] == "2026-09-24"
    assert item["weight"] == 2


def test_pre_promotion_evidence_cannot_reenter_new_stage(store):
    seed(store)
    store._promote_location(store.locate("style.md"), "one_month", NOW)
    result = store.upsert_candidates([candidate(action="reinforce", memory_ref="one_month:style.md")],
                                    source={"evidence_dates": {"1": "2026-09-24"}}, now=NOW)
    assert result["weight_skipped"][0]["reason"] == "outside_tier_evidence_window"
    assert store.get_entry("one_month", "style.md")["weight"] == 0


@pytest.mark.parametrize("stamp", [None, "2026-09-20T12:00:00", "invalid", "2999-01-01T00:00:00Z"])
def test_missing_naive_invalid_future_times_never_fabricate_today(store, stamp):
    data = [{**rounds()[0], "committed_at": stamp}]
    mapping = trusted_dates(data)
    assert mapping == {}
    result = store.upsert_candidates([candidate()], source={"evidence_dates": mapping}, now=NOW)
    assert result["weight_skipped"] == [{"filename": "style.md", "reason": "missing_evidence_date"}]
    assert store.get_entry("seven_days", "style.md")["last_weight_date"] is None


def test_quote_must_be_single_user_message_and_correct_round():
    data = [{"round": 1, "messages": [{"role": "user", "content": "我喜欢"},
                                       {"role": "user", "content": "简洁回答"}]}]
    assert quoted_rounds(candidate(evidence="我喜欢 简洁回答"), data) == []
    assert quoted_rounds(candidate(evidence_rounds=[99]), rounds()) == []
    assert quoted_rounds(candidate(evidence_rounds=[True]), rounds()) == []
    assert quoted_rounds(candidate(), rounds()) == [1]
    c = candidate(); c.pop("evidence_rounds")
    assert quoted_rounds(c, rounds()) == [1, 2, 3, 4]


def test_partial_missing_dates_are_reported_without_dropping_valid_evidence(store):
    seed(store)
    bound = bind_reference(candidate(action="reinforce", evidence_rounds=[1, 2]), receipts(), store)
    result = store.upsert_candidates([bound], source={"evidence_dates": {"1": "2026-09-21"}}, now=NOW)
    assert result["weight_skipped"] == [{"filename": "style.md", "reason": "missing_evidence_date"}]
    assert result["weight_events"] == [{"filename": "style.md", "evidence_date": "2026-09-21"}]


def test_malformed_search_receipt_does_not_authorize_target():
    assert search_receipts({"tool_calls": [None, {"name": "memory_manage", "result": {
        "ok": True, "result": {"action": "search_many", "results": [None, {"matches": [None, {}]}]}
    }}]}) == {}


def test_real_search_tool_forces_full_content_but_keeps_readonly(store):
    seed(store)
    context = {"root": str(store.root), "user": "alice", "agent": "self_improve"}
    result = memory_tool("search_many", "all", queries=[{"title": "style"}], context=context)
    assert result["include_content"] is True
    assert result["results"][0]["matches"][0]["content"] == BODY
    with pytest.raises(PermissionError):
        memory_tool("edit", "seven_days", filename="style.md", content="change", context=context)
    assert store.get_entry("seven_days", "style.md")["weight"] == 0


def test_operation_identity_ignores_assistant_and_tool_serialization():
    a = rounds()
    b = [{**item, "tools": [{"name": "other"}], "messages": [*item["messages"],
          {"role": "assistant", "content": "Different serialized response"}]} for item in a]
    identity = lambda data: memory_batch_operation_id("alice", "web", "s", 1, 4, data)
    assert identity(a) == identity(b)
    b[0] = {**b[0], "messages": [{"role": "user", "content": "changed evidence"}]}
    assert identity(a) != identity(b)


def test_archived_round_timestamp_survives_payload_rebuild():
    window = {"text": {"messages": rounds()[0]["messages"]},
              "data": {"round_metrics": [{"round": 1, "committed_at": rounds()[0]["committed_at"]}]}}
    payload = memory_round_payload(window, 1)
    rebuilt = memory_round_data(round_number=1, **payload)
    assert trusted_dates([rebuilt]) == {"1": "2026-09-20"}
    window["data"]["round_metrics"] = []
    assert "committed_at" not in memory_round_payload(window, 1)


def test_runtime_maintenance_includes_weight_only_update(store, monkeypatch):
    from web.services.runtime_status_aggregate import runtime_status

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    seed_time = NOW - timedelta(days=2)
    store.upsert_candidates([candidate()], now=seed_time)
    bound = bind_reference(candidate(action="reinforce"), receipts(), store)
    # Applied today, but backed by yesterday's conversation.
    store.upsert_candidates([bound], source={"evidence_dates": {"1": "2026-09-24"}}, now=NOW)
    monkeypatch.setattr("web.services.runtime_status.datetime", FixedDateTime)
    service = SimpleNamespace(
        root=store.root, require_user=lambda value: value, require_source=lambda value: value,
        settings=lambda _: {"provider": {}, "limits": {}},
        _system_cron_status=lambda *args, **kwargs: {"executions": [], "tracking": "execution_log"},
        tasks=lambda *args, **kwargs: {"plans": [], "cron_tasks": [], "summary": {}},
    )
    result = runtime_status(service, "alice", sections="maintenance")
    assert result["memory"]["updated_today"] == 1
    assert result["memory"]["updates"][0]["weight_increments"] == 1
    assert store.get_entry("seven_days", "style.md")["content_updated_at"] == seed_time.isoformat()


def test_manual_review_existing_target_also_requires_searched_receipt(store):
    seed(store)
    context = SimpleNamespace(runner=SimpleNamespace(root=store.root, user="alice", config={}),
                              run_model=lambda _: model_result([candidate(action="upsert", content="new")]))
    result = execute(context, {"trigger": "manual_review", "request": "整理表达偏好"})
    assert result.metadata["memory_update"]["rejected"] == 1
    assert store.get_entry("seven_days", "style.md")["content"] == BODY


def test_valid_revision_changes_content_and_legacy_upsert_binds_reference(store):
    seed(store)
    bound = bind_reference(candidate(action="upsert", content="偏好短句"), receipts(), store)
    assert bound["action"] == "revise"
    result = store.upsert_candidates([bound], source={"evidence_dates": {"1": "2026-09-21"}}, now=NOW)
    assert result["content_updated"] == ["style.md"]
    assert result["weighted"] == ["style.md"]
    assert store.get_entry("seven_days", "style.md")["content"] == "偏好短句"


def test_shanghai_creation_lock_is_not_weight_and_pure_weight_activity_is_visible(store, monkeypatch):
    mapping = {"evidence_dates": {"1": "2026-09-25"}}
    store.upsert_candidates([candidate()], source=mapping, now=NOW)
    status = store.weight_status("seven_days", "style.md", now=NOW)
    assert status["weight_day"] == "2026-09-25"  # UTC still Sep 24
    assert status["weight_locked_today"] is True
    assert status["weighted_today"] is False
    assert store.weight_activity(NOW - timedelta(hours=1), NOW + timedelta(hours=1)) == {}
    monkeypatch.setattr("run.memory.utc_now", lambda: NOW)
    service = MemoryServiceMixin()
    service.root = store.root
    service.require_user = lambda user: user
    assert service.memory_item("alice", "seven_days", "style.md")["weight_locked_today"] is True
    next_day = NOW + timedelta(days=1)
    bound = bind_reference(candidate(action="reinforce"), receipts(), store)
    store.upsert_candidates([bound], source={"evidence_dates": {"1": "2026-09-26"}}, now=next_day)
    activity = store.weight_activity(next_day - timedelta(hours=1), next_day + timedelta(hours=1))
    assert activity["style.md"]["increments"] == 1
    assert store.weight_status("seven_days", "style.md", now=next_day)["weighted_today"] is True
    assert store.get_entry("seven_days", "style.md")["content_updated_at"] == NOW.isoformat()
