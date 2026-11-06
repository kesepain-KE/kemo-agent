"""Background metadata and memory operate on bounded immutable RAW sequences."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from provider.factory import ProviderCongestionError
from run.agents import AgentOutputError
from run.agents.contracts import validate_json_schema
from run.cards import CardStore
from run.history import (close_session, find_record, mutate_registry_record,
                         queue_memory_extraction, queue_summary)
from run.memory import MemoryStore, normalize_memory_filename
from run.scheduler import MaintenanceScheduler
from run.scheduler.maintenance import (
    HISTORY_SUMMARY_MAX_OUTPUT_TOKENS,
)
from tests.card_history_support import seed_history
from tests.support.memory_db import update_fragment_metadata


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.yaml").write_text(
        json.dumps({"memory": {"extraction_mode": "compression_only", "extraction_input_tokens": 12000}}))
    (tmp_path / "users/alice").mkdir(parents=True)
    (tmp_path / "users/alice/user_config.yaml").write_text('{}')
    return tmp_path


def seed(root, text="事实正文", count=1):
    return seed_history(root, "alice", "web", "s1", [message for n in range(count) for message in (
        {"role": "user", "content": text}, {"role": "assistant", "content": f"回答 {n}"})])


def queue(root):
    close_session(root, "alice", "web", "s1")
    queue_summary(root, "alice", "web", "s1")


def response(title="历史简介", summary="可读摘要"):
    return SimpleNamespace(data={"title": title, "summary": summary})


def record(root):
    return find_record(root, "alice", "web", "s1")


def clear_backoff(root, kind="summary"):
    mutate_registry_record(root, "alice", "web", "s1", lambda r: {**r, f"{kind}_retry_at": ""})


def test_closed_session_summary_is_background_metadata_and_preserves_raw(root):
    history = seed(root)
    before = [card.raw_payload_json for card in history.cards]
    queue(root)
    input_schema = json.loads(
        (Path(__file__).resolve().parents[2] / "agents/history_summary/schema.json").read_text()
    )["input_schema"]
    def summarize(name, data, **kwargs):
        assert name == "history_summary"
        validate_json_schema(data, input_schema)
        return response()
    with patch("run.scheduler.maintenance.AgentRunner") as runner:
        runner.return_value.run.side_effect = summarize
        result = MaintenanceScheduler(root).scan_once()
    assert result["alice"]["history_summary"]["processed"][0]["status"] == "completed"
    assert record(root)["summary_status"] == "completed"
    assert record(root)["summary_completed_seq"] == 2
    assert [card.raw_payload_json for card in history.cards] == before
    assert runner.return_value.run.call_args.kwargs["max_tokens"] == HISTORY_SUMMARY_MAX_OUTPUT_TOKENS
    assert runner.return_value.run.call_args.kwargs["structured_output_tool"]


def test_summary_combines_all_short_user_and_final_text_in_one_model_call(root):
    seed(root, count=20)
    queue(root)

    def summarize(name, data, **kwargs):
        assert name == "history_summary"
        return response()

    with patch("run.scheduler.maintenance.AgentRunner") as runner:
        runner.return_value.run.side_effect = summarize
        result = MaintenanceScheduler(root).process_next_summary("alice")

    assert not result["failed"]
    records = runner.return_value.run.call_args.args[1]["records"]
    assert len(records) == 40
    assert [item["role"] for item in records[:4]] == ["user", "assistant", "user", "assistant"]
    assert runner.return_value.run.call_count == 1


def test_summary_token_budget_keeps_latest_text_and_clips_its_oldest_part(root, monkeypatch):
    monkeypatch.setattr(
        "run.scheduler.maintenance.HISTORY_SUMMARY_INPUT_TOKEN_BUDGET", 256
    )
    history = seed(root, text="较早的用户正文" * 1000)
    latest = "最新最终答复" * 3000
    latest_card = history.store.append(
        "web", "s1", history.cards[0].integration_id, "agent_text",
        {"text": latest}, level="final",
    )
    queue(root)
    with patch("run.scheduler.maintenance.AgentRunner") as runner:
        runner.return_value.run.return_value = response()
        result = MaintenanceScheduler(root).process_next_summary("alice")

    assert not result["failed"]
    summary_input = runner.return_value.run.call_args.args[1]
    records = summary_input["records"]
    assert len(records) == 1
    assert records[0]["seq"] == latest_card.seq
    assert records[0]["role"] == "assistant"
    assert records[0]["text"] == latest[-len(records[0]["text"]):]
    assert len(records[0]["text"]) < len(latest)
    assert result["processed"][0]["input_tokens"] <= 256
    assert runner.return_value.run.call_count == 1


def test_summary_uses_only_initial_user_and_final_assistant_text(root):
    history = seed(root, text="原始用户正文")
    integration_id = history.cards[0].integration_id
    history.store.append("web", "s1", integration_id, "user_text",
                         {"text": "后续引导不属于对话正文"}, level="guidance")
    history.store.append("web", "s1", integration_id, "agent_text",
                         {"text": "智能体临时正文不属于最终交付"}, level="interim")
    queue(root)
    seen = []

    def summarize(_name, data, **_kwargs):
        seen.extend(data["records"])
        return response()

    with patch("run.scheduler.maintenance.AgentRunner") as runner:
        runner.return_value.run.side_effect = summarize
        result = MaintenanceScheduler(root).process_next_summary("alice")

    assert not result["failed"]
    assert [(item["role"], item["text"]) for item in seen] == [
        ("user", "原始用户正文"),
        ("assistant", "回答 0"),
    ]


@pytest.mark.parametrize("output,expected", [
    ("第一行普通文本\n第二行仍不是 JSON", "第一行普通文本 第二行仍不是 JSON"),
    ("api_key: test-secret-value", "[输出包含疑似敏感内容，已隐藏]"),
])
def test_failed_summary_preserves_only_safe_diagnostics(root, output, expected):
    seed(root)
    queue(root)
    with patch("run.scheduler.maintenance.AgentRunner") as runner:
        runner.return_value.run.side_effect = AgentOutputError("格式错误", raw_text=output)
        result = MaintenanceScheduler(root).process_next_summary("alice")
    assert result["failed"][0]["error"]["raw_output_preview"] == expected
    assert record(root)["summary_last_error"]["raw_output_preview"] == expected
    assert record(root)["summary_status"] == "failed" and record(root)["summary_retry_at"]


def test_summary_retry_rebuilds_one_bounded_latest_snapshot(root, monkeypatch):
    monkeypatch.setattr(
        "run.scheduler.maintenance.HISTORY_SUMMARY_INPUT_TOKEN_BUDGET", 256
    )
    seed(root, text="较早内容" * 2000)
    queue(root)
    scheduler = MaintenanceScheduler(root, summary_retry_delays=(1,))
    with patch("run.scheduler.maintenance.AgentRunner") as runner:
        runner.return_value.run.side_effect = AgentOutputError("摘要暂时失败")
        assert scheduler.process_next_summary("alice")["failed"]
        first_input = runner.return_value.run.call_args.args[1]
    assert not record(root).get("summary_checkpoint")
    clear_backoff(root)
    with patch("run.scheduler.maintenance.AgentRunner") as runner:
        runner.return_value.run.return_value = response()
        result = scheduler.process_next_summary("alice")
        assert result["processed"]
        assert runner.return_value.run.call_count == 1
        assert runner.return_value.run.call_args.args[1] == first_input
        assert result["processed"][0]["input_tokens"] <= 256


def test_summary_congestion_defers_without_consuming_attempt(root):
    seed(root)
    queue(root)
    with patch("run.scheduler.maintenance.AgentRunner") as runner:
        runner.return_value.run.side_effect = ProviderCongestionError("繁忙")
        result = MaintenanceScheduler(root).process_next_summary("alice")
    assert result["failed"][0]["deferred"]
    assert record(root)["summary_attempt_count"] == 0
    assert record(root)["summary_status"] == "queued"


def test_summary_respects_configured_attempt_ceiling(root):
    seed(root)
    queue(root)
    scheduler = MaintenanceScheduler(root, summary_max_attempts=1)
    with patch("run.scheduler.maintenance.AgentRunner") as runner:
        runner.return_value.run.side_effect = AgentOutputError("failure")
        assert scheduler.process_next_summary("alice")["failed"]
        assert record(root)["summary_max_attempts"] == 1
        clear_backoff(root)
        assert scheduler.process_next_summary("alice")["claimed"] == 0
    assert runner.return_value.run.call_count == 1


def test_compression_only_does_not_claim_unqueued_raw(root):
    seed(root)
    result = MaintenanceScheduler(root).scan_once()
    assert result["alice"]["memory_recovery"]["claimed"] == 0
    assert record(root)["memory_processed_seq"] == 0


def analysis(**kwargs):
    return {"status": "completed", "candidate_count": 0, "candidates": [], "source": kwargs.get("source", {})}


def test_explicit_memory_queue_drains_narrative_batch_without_raw_count_split(root):
    seed(root, count=3)
    queue_memory_extraction(root, "alice", "web", "s1", target_seq=4, reason="manual_compression")
    scheduler = MaintenanceScheduler(root)
    with patch("run.memory.analysis.analyze_memory_batch_resilient", side_effect=analysis) as analyze:
        first = scheduler.scan_once()
        assert record(root)["memory_processed_seq"] == 4 and record(root)["memory_status"] == "pending"
        second = scheduler.scan_once()
        third = scheduler.scan_once()
    assert first["alice"]["memory_recovery"]["claimed"] == 4
    assert second["alice"]["memory_recovery"]["claimed"] == 0
    assert third["alice"]["memory_recovery"]["claimed"] == 0
    assert analyze.call_count == 1
    assert record(root)["memory_processed_seq"] == 4 and record(root)["memory_status"] == "pending"


@pytest.mark.parametrize("flag", ["capacity_blocked", "work_pending"])
def test_memory_pending_work_defers_without_moving_cursor(root, flag):
    seed(root)
    queue_memory_extraction(root, "alice", "web", "s1")
    failure = {"status": "failed", "error": {flag: True, "message": "wait"}}
    with patch("run.memory.analysis.analyze_memory_batch_resilient", return_value=failure):
        result = MaintenanceScheduler(root).scan_once()["alice"]["memory_recovery"]
    assert result["deferred"] and not result["failed"]
    assert record(root)["memory_processed_seq"] == 0
    assert record(root)["memory_status"] == "queued" and record(root)["memory_retry_at"]


def test_failed_memory_analysis_keeps_raw_cursor_and_can_retry(root):
    seed(root)
    queue_memory_extraction(root, "alice", "web", "s1")
    with patch("run.memory.analysis.analyze_memory_batch_resilient", return_value={
            "status": "failed", "error": {"exception_type": "AgentOutputError", "message": "bad format"}}):
        MaintenanceScheduler(root).scan_once()
    assert record(root)["memory_processed_seq"] == 0
    assert record(root)["memory_last_error"]["exception_type"] == "AgentOutputError"
    clear_backoff(root, "memory")
    with patch("run.memory.analysis.analyze_memory_batch_resilient", side_effect=analysis):
        MaintenanceScheduler(root).scan_once()
    assert record(root)["memory_processed_seq"] == 2


def test_stolen_memory_claim_cannot_apply_late_analysis(root):
    seed(root)
    queue_memory_extraction(root, "alice", "web", "s1")
    def stolen(**kwargs):
        mutate_registry_record(root, "alice", "web", "s1", lambda r: {**r, "memory_claim_id": "newer"})
        return analysis(**kwargs)
    with (patch("run.memory.analysis.analyze_memory_batch_resilient", side_effect=stolen),
          patch("run.memory.analysis.persist_round_memory_analysis") as persist):
        MaintenanceScheduler(root).scan_once()
    persist.assert_not_called()
    assert record(root)["memory_claim_id"] == "newer" and record(root)["memory_processed_seq"] == 0


def test_force_maintenance_does_not_promote_memory_implicitly(root):
    config = {"memory": {"tiers": {
        "seven_days": {"days": 7, "upgrade_threshold": 3, "next": "one_month"},
        "one_month": {"days": 30, "upgrade_threshold": 10, "next": "half_year"},
        "half_year": {"days": 180, "upgrade_threshold": 60, "next": None},
    }}}
    store = MemoryStore(root, "alice", config)
    filename, now = normalize_memory_filename("durable preference"), datetime.now(timezone.utc)
    store.create_fragment("half_year", filename, "durable preference", now=now - timedelta(days=181))
    update_fragment_metadata(store, "half_year", filename, weight=60, expires_at=now - timedelta(seconds=1))
    result = MaintenanceScheduler(root).scan_once(now=now, force=True)
    assert "memory_lifecycle" not in result["alice"]
    assert store.get_entry("permanent", filename) is None
    assert store.get_entry("half_year", filename) is not None
