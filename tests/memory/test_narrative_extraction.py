"""Narrative selection is independent of tool-card count and active VIEW."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from provider.tokenizer import count_value
from run.cards import CardStore, compact
from run.history import claim_pending_memory, queue_memory_extraction
from run.memory.analysis import extract_card_memory
from run.memory.evidence import quoted_rounds
from run.memory.extraction import MemoryWorkPending, run_extraction_pages, source_pages
from run.agents import AgentRunResult
from tests.cards.test_runtime import root
from tests.cards.test_store import summary
from tests.card_history_support import seed_history


def completed_analysis(**kwargs):
    return {"status": "completed", "candidate_count": 0, "candidates": [],
            "source": kwargs.get("source", {})}


def test_more_than_500_tool_cards_do_not_separate_user_guidance_and_final(root):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    initial = store.append("web", "s1", "narrative", "user_text",
                           {"text": "我长期使用 Windows，希望你记住部署偏好。"}, level="initial")
    guidance = store.append("web", "s1", "narrative", "user_text",
                            {"text": "补充：喜欢先验证本地结果，再决定发布。"}, level="guidance")
    store.append("web", "s1", "narrative", "agent_text",
                 {"text": "正在处理，不是交付。"}, level="interim")
    store.append("web", "s1", "narrative", "agent_text",
                 {"text": "未完成重试残片", "incomplete": True, "retry_snapshot": True}, level="interim")
    for number in range(600):
        store.append("web", "s1", "narrative", "tool_result", {
            "original_call": {"name": "work", "arguments": {}},
            "execution": {"status": "completed"}, "result": "tool noise " * 10,
        }, tool_call_id=f"callid_{number}")
    final = store.append("web", "s1", "narrative", "agent_text",
                         {"text": "已核实部署流程，以下是最终结果。"}, level="final")
    compact(store, "web", "s1", summarize=lambda _: summary())
    queue_memory_extraction(root, "alice", "web", "s1")
    claim = claim_pending_memory(root, "alice", source="web", session_id="s1")
    assert claim["memory_claim_end_seq"] == final.seq > 500
    config = {"memory": {"extraction_mode": "compression_only"}}
    with patch("run.memory.analysis.analyze_memory_batch_resilient", side_effect=completed_analysis) as analyze:
        outcome = extract_card_memory(root=root, user="alice", source="web", session_id="s1",
                                      config=config, agent_runner=SimpleNamespace(config=config), claim=claim)
    assert outcome["status"] == "completed", outcome
    records = analyze.call_args.kwargs["rounds"]
    assert [r["seq"] for r in records] == [initial.seq, guidance.seq, final.seq]
    assert [r["level"] for r in records] == ["initial", "guidance", "final"]
    assert all(r["card_id"] and r["integration_id"] == "narrative" for r in records)
    found_record = store.session("web", "s1")
    assert found_record is not None
    assert found_record["memory_processed_seq"] == final.seq


def test_withdrawn_integration_is_not_memory_evidence(root):
    history = seed_history(root, "alice", "web", "s1", [
        {"role": "user", "content": "我喜欢本地验证。"},
        {"role": "assistant", "content": "好的，先验证。"},
        {"role": "user", "content": "这条已被编辑撤回。"},
        {"role": "assistant", "content": "旧回复也已撤回。"},
    ])
    withdrawn_id = history.cards[-1].integration_id
    with history.store.db(write=True) as db:
        db.execute("UPDATE card_integrations SET record_json=json_set(record_json,'$.withdrawn',1) "
                   "WHERE integration_id=?", (withdrawn_id,))
    queue_memory_extraction(root, "alice", "web", "s1")
    config = {"memory": {"extraction_mode": "compression_only"}}
    with patch("run.memory.analysis.analyze_memory_batch_resilient", side_effect=completed_analysis) as analyze:
        outcome = extract_card_memory(root=root, user="alice", source="web", session_id="s1",
                                      config=config, agent_runner=SimpleNamespace(config=config))
    assert outcome["status"] == "completed", outcome
    records = analyze.call_args.kwargs["rounds"]
    assert [r["seq"] for r in records] == [1, 2]
    found_record = history.store.session("web", "s1")
    assert found_record is not None
    assert found_record["memory_processed_seq"] == 4


def test_huge_answer_pages_keep_user_context_and_lossless_offsets():
    prompt = "我希望把部署经验作为一个连贯主题整理。"
    body = "这是一段有上下文的最终回答。" * 2500 + "最终末尾事实"
    rounds = [
        {"round": 1, "seq": 1, "card_id": "card_user", "integration_id": "i", "level": "initial",
         "messages": [{"role": "user", "content": prompt}]},
        {"round": 602, "seq": 602, "card_id": "card_final", "integration_id": "i", "level": "final",
         "messages": [{"role": "assistant", "content": body}]},
    ]
    pages = source_pages(rounds, token_budget=600)
    assert len(pages) > 2
    assert all(count_value(page) <= 600 for page in pages)
    actual = "".join(m["content"] for page in pages for record in page
                     if not record.get("context_only") for m in record["messages"]
                     if m["role"] == "assistant")
    assert actual == body
    assistant_pages = [page for page in pages if any(
        m["role"] == "assistant" for record in page for m in record["messages"])]
    for page in assistant_pages:
        assert any(record.get("context_only") and record["seq"] == 1 for record in page)
        assert all(record["integration_id"] == "i" for record in page)
    copied = [record for record in assistant_pages[-1] if record.get("context_only")]
    assert not quoted_rounds({"evidence": prompt, "evidence_rounds": [1]}, copied)


def test_huge_user_prose_keeps_original_raw_identity_and_every_character():
    body = "用户自己的中文叙述与英文 English mixed。" * 1200 + "最后的用户事实"
    record = {"round": 77, "seq": 77, "card_id": "card_original", "integration_id": "same",
              "level": "guidance", "messages": [{"role": "user", "content": body}]}
    pages = source_pages([record], token_budget=800)
    assert len(pages) > 1
    assert all(count_value(page) <= 800 for page in pages)
    records = [r for page in pages for r in page if not r.get("context_only")]
    assert "".join(r["messages"][0]["content"] for r in records) == body
    assert all((r["round"], r["seq"], r["card_id"], r["level"]) ==
               (77, 77, "card_original", "guidance") for r in records)
    assert records[0]["segment"]["offset"] == 0
    assert records[-1]["segment"]["end"] == len(body)


def test_complete_model_input_respects_small_token_budget_and_excludes_global_dates(root):
    captured = []

    def model(data):
        captured.append(data)
        assert count_value(data) <= 600
        return AgentRunResult(agent="self_improve", data={"candidates": []},
                              raw_text="", usage={}, model="mock", metadata={})

    context = SimpleNamespace(runner=SimpleNamespace(root=root, user="alice",
                              config={"memory": {"extraction_input_tokens": 600}}),
                              run_model=model)
    data = {"trigger": "context_compression", "rounds": [
        {"round": 7, "messages": [{"role": "user", "content": "用户主题。" * 40}]}],
        "source": {"id": "large-date-index", "evidence_dates": {
            str(number): "2026-10-15" for number in range(1000)}}}
    result = run_extraction_pages(context, data)
    assert result.data == {"candidates": []} and captured
    assert all("evidence_dates" not in item["source"] for item in captured)


def test_pending_page_geometry_survives_config_change(root, monkeypatch):
    monkeypatch.setattr("run.memory.extraction.MAX_PAGES_PER_RUN", 1)
    captured = []
    config = {"memory": {"extraction_input_tokens": 600}}

    def model(data):
        captured.extend(record for record in data["rounds"] if not record.get("context_only"))
        return AgentRunResult(agent="self_improve", data={"candidates": []},
                              raw_text="", usage={}, model="mock", metadata={})

    runner = SimpleNamespace(root=root, user="alice", config=config)
    context = SimpleNamespace(runner=runner, run_model=model)
    body = "一份完整的长期个人主题叙述。" * 200 + "结尾不能丢"
    data = {"trigger": "context_compression", "rounds": [
        {"round": 1, "messages": [{"role": "user", "content": body}]}]}
    with pytest.raises(MemoryWorkPending):
        run_extraction_pages(context, data)
    runner.config = {"memory": {"extraction_input_tokens": 12000}}
    for _ in range(30):
        try:
            run_extraction_pages(context, data)
            break
        except MemoryWorkPending:
            pass
    else:
        pytest.fail("narrative cursor did not finish")
    assert "".join(r["messages"][0]["content"] for r in captured) == body


def test_tool_only_raw_coverage_requires_no_model_call(root):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    store.append("web", "s1", "tools-only", "tool_result", {
        "original_call": {"name": "work", "arguments": {}},
        "execution": {"status": "completed"}, "result": "not personal memory",
    }, tool_call_id="callid_only")
    config = {"memory": {"extraction_mode": "compression_only"}}
    with patch("run.memory.analysis.analyze_memory_batch_resilient") as analyze:
        outcome = extract_card_memory(root=root, user="alice", source="web", session_id="s1",
                                      config=config, agent_runner=SimpleNamespace(config=config))
    assert outcome["status"] == "completed" and outcome["processed_seq"] == 1
    analyze.assert_not_called()


def test_deferred_claim_range_survives_larger_config_budget(root):
    from run.history import defer_memory_claim, mutate_registry_record
    seed_history(root, "alice", "web", "s1", [
        message for number in range(3) for message in (
            {"role": "user", "content": f"用户偏好 {number}"},
            {"role": "assistant", "content": f"最终回复 {number}"})])
    first = claim_pending_memory(root, "alice", input_tokens=1)
    assert first["memory_claim_end_seq"] == 2
    defer_memory_claim(root, "alice", "web", "s1", claim_id=first["memory_claim_id"],
                       error={"work_pending": True})
    mutate_registry_record(root, "alice", "web", "s1", lambda record: {**record, "memory_retry_at": ""})
    resumed = claim_pending_memory(root, "alice", input_tokens=12000)
    assert resumed["memory_claim_end_seq"] == first["memory_claim_end_seq"]
    assert resumed["memory_claim_start_seq"] == 1
