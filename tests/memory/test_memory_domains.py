"""Domain contracts exercise the real executor, SQLite store and lifecycle."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from agents.self_improve.executor import execute
from cron.review_due import scan_and_promote
from run.agents import AgentRunResult
from run.config import load_config
from run.memory import (
    MemoryConfigError, MemoryError, MemoryLocation, MemoryStore,
    memory_domain_rules, memory_domains_for_profile, memory_profile,
    normalize_memory_domain,
)
from run.memory.analysis import analyze_memory_batch, persist_round_memory_analysis
from run.memory import store as schema
from run.memory.extraction import run_extraction_pages
from run.memory.work import WorkLedger, digest


NOW = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
COMPANION = {"memory": {"profile": "companion"}}


@pytest.fixture
def personal(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    return MemoryStore(tmp_path, "alice", COMPANION)


def candidate(name, domain="fact", **changes):
    return {"action": "create", "filename": name, "content": f"用户描述的{name}。",
            "memory_domain": domain, **changes}


def model_result(candidates, metadata=None):
    return AgentRunResult(agent="self_improve", data={"candidates": candidates},
                          raw_text="", usage={}, model="test", metadata=metadata or {})


def run_executor(store, candidates, user="这周正在准备考试。", assistant="我喜欢吃鱼。",
                 metadata=None, **overrides):
    seen = []

    def model(data):
        seen.append(data)
        return model_result(candidates, metadata)

    context = SimpleNamespace(runner=SimpleNamespace(
        root=store.root, user=store.user, config=store.config), run_model=model)
    payload = {"trigger": "context_compression", "source": {"id": "unchanged"},
               "rounds": [{"round": 1, "committed_at": NOW.isoformat(), "messages": [
                   {"role": "user", "content": user},
                   {"role": "assistant", "content": assistant}]}], **overrides}
    return execute(context, payload), seen


def test_default_profile_and_domain_rules_are_independent_copies():
    assert memory_profile({}) == "companion"
    assert memory_domains_for_profile({}) == ("fact", "relation", "state")
    assert memory_profile({"memory": {"profile": "work"}}) == "work"
    assert memory_domains_for_profile(COMPANION) == ("fact", "relation", "state")
    assert memory_domain_rules({"memory": {"profile": "work"}}) == {}
    rules = memory_domain_rules(COMPANION)
    rules["relation"]["upgrade_threshold"] = 99
    assert memory_domain_rules(COMPANION)["relation"]["upgrade_threshold"] == 1


def test_memory_prompts_preserve_thematic_personal_context():
    root = Path(__file__).resolve().parents[2]
    extraction = (root / "agents/self_improve/AGENT.md").read_text("utf-8")
    trigger = (root / "agents/self_improve/trigger.md").read_text("utf-8")
    hot_profile = (root / "agents/memory_temporary_important/AGENT.md").read_text("utf-8")
    lifecycle = (root / "run/memory/lifecycle.md").read_text("utf-8")

    assert "按可理解的主题组织" in extraction
    assert "只有主体、时效或语义边界确实不同才另建" in extraction
    assert "完整主题" in trigger
    assert "一页连贯简介" in hot_profile
    assert "不按句子、属性标签或字段拆成微片" in lifecycle
    assert "任意一个边界可以独立处理，就必须拆" not in lifecycle


@pytest.mark.parametrize("section", [
    {"profile": "personal"}, {"profile": None}, {"profile": False},
    {"domain_rules": {"fact": {}}}, {"domain_rules": {"unknown": {}}},
    {"domain_rules": []}, {"domain_rules": {"relation": []}},
    {"domain_rules": {"relation": {"upgrade_threshold": True}}},
    {"domain_rules": {"relation": {"upgrade_threshold": -1}}},
    {"domain_rules": {"relation": {"upgrade_threshold": 1.2}}},
    {"domain_rules": {"state": {"refresh_expiry_on_update": "true"}}},
    {"domain_rules": {"state": {"typo": 1}}},
])
def test_invalid_config_fails_even_before_first_database_use(tmp_path, section):
    (tmp_path / "config").mkdir()
    (tmp_path / "users/alice").mkdir(parents=True)
    (tmp_path / "config/global_config.yaml").write_text(
        json.dumps({"memory": section}), encoding="utf-8")
    (tmp_path / "users/alice/user_config.yaml").write_text("{}", encoding="utf-8")
    with pytest.raises(MemoryConfigError):
        load_config("alice", tmp_path)
    assert not (tmp_path / "users/alice/improve/memory.sqlite3").exists()


@pytest.mark.parametrize("value", [None, ""])
def test_missing_domain_is_fact(value):
    assert normalize_memory_domain(value) == "fact"


@pytest.mark.parametrize("value", [[], {}, False, 0, "FACT", "unknown"])
def test_invalid_domain_does_not_fall_back(value):
    with pytest.raises(MemoryError):
        normalize_memory_domain(value)


def test_work_rejects_new_domains_but_keeps_good_candidates(personal):
    work = MemoryStore(personal.root, personal.user, {"memory": {"profile": "work"}})
    invalid_list_domain: Any = ["state"]
    outcome = work.upsert_candidates([
        candidate("事实"), candidate("称呼", "relation"), candidate("近况", "state"),
        candidate("非法", invalid_list_domain),
    ], now=NOW)
    assert outcome["created"] == ["事实.md"]
    assert outcome["rejected"] == 3
    assert [r["reason"] for r in outcome["rejection_reasons"]] == [
        "domain_not_enabled", "domain_not_enabled", "invalid_memory_domain"]
    assert work.load_all()[0].get("domain", "fact") == "fact"


def test_personal_candidates_pass_without_long_term_flag_but_require_user_quotes(personal):
    candidates = [
        candidate("学业近况", "state", content="用户这周准备考试。", evidence="这周正在准备考试。"),
        candidate("助手设定", "relation", content="用户喜欢吃鱼。", evidence="我喜欢吃鱼。"),
        candidate("猜测", "fact", content="用户长期爱学习。", evidence="这周正在准备考试。"),
    ]
    result, seen = run_executor(personal, candidates)
    assert [c["filename"] for c in result.data["candidates"]] == ["学业近况.md"]
    assert result.metadata["candidate_filter"]["rejected"] == 2
    assert seen[0]["memory_profile"] == "companion"
    assert seen[0]["memory_domains"] == ["fact", "relation", "state"]
    assert seen[0]["source"] == {"id": "unchanged"}


def test_host_profile_cannot_be_spoofed_by_input(personal):
    work = MemoryStore(personal.root, personal.user, {"memory": {"profile": "work"}})
    result, seen = run_executor(work, [
        candidate("近况", "state", evidence="这周正在准备考试。", durable=True),
    ], memory_profile="companion", memory_domains=["state"])
    assert result.data["candidates"] == []
    assert seen[0]["memory_profile"] == "work"
    assert seen[0]["memory_domains"] == ["fact"]


@pytest.mark.parametrize("domain", ["fact", "relation", "state"])
@pytest.mark.parametrize("action", ["create", "revise", "forget"])
def test_all_new_write_actions_reject_assistant_only_evidence(personal, domain, action):
    personal.create_fragment("seven_days", "目标", "原有正文", domain=domain, now=NOW)
    hit = {"memory_ref": "seven_days:目标.md", "filename": "目标.md",
           "content": "原有正文", "domain": domain}
    metadata = {"tool_calls": [{"name": "memory_manage", "result": {
        "ok": True, "result": {"action": "search_many", "results": [{"matches": [hit]}]}}}]}
    entry = candidate("新建" if action == "create" else "目标", domain,
                      action=action, evidence="我喜欢吃鱼。", durable=True, explicit=True)
    if action != "create":
        entry["memory_ref"] = hit["memory_ref"]
    result, _ = run_executor(personal, [entry], metadata=metadata)
    assert result.data["candidates"] == []
    assert personal.get_entry("seven_days", "目标")["content"] == "原有正文"
    assert personal.locate("新建") is None


@pytest.mark.parametrize("via_candidate", [False, True])
@pytest.mark.parametrize("domain", ["fact", "relation", "state"])
def test_only_state_content_revision_refreshes_expiry(personal, via_candidate, domain):
    original = personal.create_fragment("seven_days", "目标", "旧内容", domain=domain, now=NOW)
    later = NOW + timedelta(days=3)
    if via_candidate:
        outcome = personal.upsert_candidates([candidate(
            "目标", domain, action="revise", memory_ref="seven_days:目标.md",
            content="新内容")], now=later)
        assert outcome["rejected"] == 0
    else:
        personal.edit_fragment("seven_days", "目标", "新内容", now=later)
    row = personal.get_entry("seven_days", "目标")
    assert row["created_at"] == original["created_at"]
    assert row["expires_at"] == (
        (later + timedelta(days=7)).isoformat() if domain == "state" else original["expires_at"])
    assert row["tier_entered_at"] == (later.isoformat() if domain == "state" else original["tier_entered_at"])
    assert row.get("domain", "fact") == domain


def test_state_same_body_reinforcement_and_disabled_refresh_do_not_extend(personal):
    original = personal.create_fragment("seven_days", "状态", "最近考试", domain="state", now=NOW)
    later = NOW + timedelta(days=2)
    personal.upsert_candidates([candidate("状态", "state", action="reinforce",
        memory_ref="seven_days:状态.md", content="忽略替代正文")], now=later)
    assert personal.get_entry("seven_days", "状态")["expires_at"] == original["expires_at"]
    disabled = MemoryStore(personal.root, personal.user, {"memory": {
        "profile": "companion", "domain_rules": {"state": {"refresh_expiry_on_update": False}}}})
    disabled.edit_fragment("seven_days", "状态", "最近搬家", now=later)
    disabled_entry = disabled.get_entry("seven_days", "状态")
    assert disabled_entry is not None
    assert disabled_entry["expires_at"] == original["expires_at"]


def test_state_remains_short_and_temporary_even_when_explicit(personal):
    result = personal.upsert_candidates([
        candidate("短近况", "state", explicit=True),
        candidate("长近况", "state", content="字" * 151, memory_type="A"),
    ], now=NOW)
    assert result["created"] == ["短近况.md"]
    assert result["rejected"] == 1
    assert personal.locate("短近况").tier == "seven_days"
    with pytest.raises(MemoryError):
        personal.create_fragment("permanent", "错层", "近况", domain="state")
    with pytest.raises(MemoryError):
        personal.edit_fragment("seven_days", "短近况", "字" * 151)


def test_relation_promotes_after_second_evidence_day_state_always_archives(personal):
    days = {"1": NOW.date().isoformat(), "2": (NOW + timedelta(days=1)).date().isoformat()}
    personal.upsert_candidates([candidate("称呼", "relation", evidence_rounds=[1, 2])],
                               source={"evidence_dates": days}, now=NOW + timedelta(days=1))
    personal.create_fragment("seven_days", "近期", "近况", domain="state", now=NOW)
    with schema.connection(personal.root, personal.user, write=True) as db:
        db.execute("UPDATE memory_fragments SET weight=99 WHERE domain='state'")
    outcome = personal.review_due(now=NOW + timedelta(days=9))
    assert outcome == {"upgraded": ["称呼.md"], "deleted": ["近期.md"]}
    assert personal.get_entry("one_month", "称呼")["domain"] == "relation"
    archive = next((personal.root / "users/alice/improve/expired_memory").rglob("*.md"))
    assert 'domain: "state"' in archive.read_text("utf-8")


def test_cron_archives_state_without_calling_promotion_model(personal):
    personal.create_fragment("seven_days", "近况", "本周近况", domain="state", now=NOW)
    with patch("cron.review_due.AgentRunner", side_effect=AssertionError("must not promote state")):
        outcome = scan_and_promote(root=personal.root, user=personal.user,
                                  config=personal.config, now=NOW + timedelta(days=8))
    assert outcome["requested"] == 0
    assert outcome["deleted"] == ["近况.md"]


def test_revision_cannot_change_domain_or_merge_domains(personal):
    personal.create_fragment("seven_days", "称呼", "用户称呼", domain="relation", now=NOW)
    outcome = personal.upsert_candidates([candidate("称呼", "fact", action="revise",
        memory_ref="seven_days:称呼.md", content="改成事实")], now=NOW)
    assert outcome["rejection_reasons"][0]["reason"] == "memory_domain_mismatch"
    personal.create_fragment("one_month", "目标", "另一事实", now=NOW)
    with pytest.raises(MemoryError, match="不能融合"):
        personal._promote_location(MemoryLocation("seven_days", "称呼"), "one_month", NOW,
                                  target_filename="目标", merged_content="不能混合")
    assert personal.locate("称呼").tier == "seven_days"


def test_split_keeps_relation_domain(personal):
    personal.create_fragment("seven_days", "相处", "相处内容", domain="relation", now=NOW)
    children = personal._split_promote_location(
        MemoryLocation("seven_days", "相处"), "one_month", NOW,
        [{"filename": "称呼", "content": "用户称呼"},
         {"filename": "玩笑", "content": "用户玩笑"}])
    assert len(children) == 2
    assert all(item["domain"] == "relation" for item in personal.load_tier("one_month"))


def test_companion_selects_domains_before_file_limit_work_keeps_old_order(personal):
    for name, domain, weight in [("事实", "fact", 99), ("相处", "relation", 5), ("近况", "state", 0)]:
        personal.create_fragment("seven_days", name, name, domain=domain, now=NOW)
        with schema.connection(personal.root, personal.user, write=True) as db:
            db.execute("UPDATE memory_fragments SET weight=? WHERE filename=?", (weight, name + ".md"))
    assert personal.select_tier_for_prompt("seven_days", max_files=2).selected_ids == ("近况.md", "相处.md")
    work = MemoryStore(personal.root, personal.user, {"memory": {"profile": "work"}})
    assert work.select_tier_for_prompt("seven_days", max_files=2).selected_ids == ("事实.md", "相处.md")
    personal.create_fragment("permanent", "A", "永久事实", now=NOW)
    personal.create_fragment("permanent", "B", "永久关系", domain="relation", now=NOW)
    assert personal.select_tier_for_prompt("permanent", max_files=1).selected_ids == ("A.md", "B.md")


def test_analysis_forwards_profile_without_changing_source():
    seen = []

    def run(name, data, **kwargs):
        seen.append(data)
        return model_result([])

    runner = SimpleNamespace(config=COMPANION, run=run)
    rounds = [{"round": 1, "messages": [{"role": "user", "content": "最近考试"}]}]
    result = analyze_memory_batch(rounds=rounds, agent_runner=runner,
                                  cancel_event=None, source={"id": "stable"})
    assert result["status"] == "completed"
    assert seen[0]["memory_profile"] == "companion"
    assert seen[0]["source"] == {"id": "stable", "evidence_dates": {}}


def test_domain_rejections_survive_persistent_work_aggregation(personal):
    work = MemoryStore(personal.root, personal.user, {"memory": {"profile": "work"}})
    invalid_mapping_domain: Any = {}
    analysis = {"status": "completed", "source": {}, "candidates": [
        candidate("事实"), candidate("禁用域", "relation"),
        candidate("非法", invalid_mapping_domain)]}
    result = persist_round_memory_analysis(root=work.root, user=work.user,
                                          config=work.config, analysis=analysis)
    assert result["status"] == "completed"
    assert result["persisted"]["rejected"] == 2
    assert {r["reason"] for r in result["persisted"]["rejection_reasons"]} == {
        "domain_not_enabled", "invalid_memory_domain"}


def test_completed_narrative_job_replays_without_model(personal):
    old_input = {"trigger": "context_compression", "rounds": [
        {"round": 1, "messages": [{"role": "user", "content": "老任务的用户原话"}]}],
        "source": {"id": "narrative_batch"}, "memory_profile": "work", "memory_domains": ["fact"]}
    ledger = WorkLedger(personal.root, personal.user)
    key = "extract:" + digest({"source": old_input, "paging": "narrative-tokens-v1"})
    state = {"page": 1, "candidates": [{"filename": "旧结果"}],
             "metadata": {"tool_calls": []}, "usage": {}, "model": "old_model"}
    ledger.enqueue("extraction", key, state)
    ledger.save(key, state, completed=True)

    def model(_):
        pytest.fail("completed narrative extraction must not call the model again")

    context = SimpleNamespace(runner=SimpleNamespace(
        root=personal.root, user=personal.user, config={}), run_model=model)
    result = run_extraction_pages(context, {
        **old_input, "memory_profile": "work", "memory_domains": ["fact"]})
    assert result.data["candidates"] == [{"filename": "旧结果"}]
    assert result.model == "old_model"


def test_fact_null_threshold_preserves_legacy_zero_threshold(personal):
    from run.memory import DEFAULT_TIERS, memory_upgrade_threshold
    tiers = {name: dict(rule) for name, rule in DEFAULT_TIERS.items()}
    tiers["seven_days"]["upgrade_threshold"] = None
    assert memory_upgrade_threshold({"memory": {"tiers": tiers}}, "seven_days", "fact") == 0


def old_memory_database(path=":memory:"):
    db = sqlite3.connect(path)
    schema._configure(db)
    schema._ensure_schema(db)
    db.execute("ALTER TABLE memory_fragments DROP COLUMN domain")
    db.execute("UPDATE memory_meta SET value='2' WHERE key='schema_version'")
    db.execute("""INSERT INTO memory_fragments(
        filename,filename_key,tier,content,content_hash,created_at,
        content_updated_at,tier_entered_at,expires_at)
        VALUES('old.md','old.md','seven_days','original','hash',?,?,?,?)""",
        (NOW.isoformat(), NOW.isoformat(), NOW.isoformat(), (NOW + timedelta(days=7)).isoformat()))
    db.commit()
    return db


def test_v2_migration_assigns_fact_without_changing_old_data():
    with closing(old_memory_database()) as db:
        before = dict(db.execute("SELECT * FROM memory_fragments").fetchone())
        schema._ensure_schema(db)
        after = dict(db.execute("SELECT * FROM memory_fragments").fetchone())
        assert after.pop("domain") == "fact"
        assert after == before
        assert db.execute("SELECT value FROM memory_meta WHERE key='schema_version'").fetchone()[0] == str(schema.MEMORY_DB_SCHEMA_VERSION)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE memory_fragments SET domain='typo'")


def test_migration_failure_rolls_back_domain_and_version():
    with closing(old_memory_database()) as db:
        original = schema._initialize_schema

        def fail(database):
            original(database)
            raise RuntimeError("injected failure")

        with patch.object(schema, "_initialize_schema", side_effect=fail):
            with pytest.raises(RuntimeError, match="injected failure"):
                schema._ensure_schema(db)
        assert "domain" not in {r["name"] for r in db.execute("PRAGMA table_info(memory_fragments)")}
        assert db.execute("SELECT value FROM memory_meta WHERE key='schema_version'").fetchone()[0] == "2"
        assert db.execute("SELECT content FROM memory_fragments").fetchone()[0] == "original"


def test_v2_migration_keeps_existing_and_new_wal_readers_available(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    path = tmp_path / "migration.sqlite3"
    with closing(old_memory_database(path)) as seed:
        seed.execute("PRAGMA journal_mode=WAL")
    migrated, release = Event(), Event()
    initialize = schema._initialize_schema

    def pause_before_commit(database):
        initialize(database)
        migrated.set()
        assert release.wait(5), "reader did not release the migration"

    def upgrade():
        with closing(sqlite3.connect(path)) as database:
            schema._configure(database)
            schema._ensure_schema(database)

    with closing(sqlite3.connect(path, timeout=0.1)) as reader:
        reader.execute("BEGIN")
        assert reader.execute("SELECT content FROM memory_fragments").fetchone() == ("original",)
        with patch.object(schema, "_initialize_schema", side_effect=pause_before_commit):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(upgrade)
                try:
                    assert migrated.wait(5), "migration did not reach its uncommitted DDL"
                    for connection in (reader, sqlite3.connect(path, timeout=0.1)):
                        try:
                            assert connection.execute(
                                "SELECT content FROM memory_fragments"
                            ).fetchone() == ("original",)
                            assert connection.execute(
                                "SELECT value FROM memory_meta WHERE key='schema_version'"
                            ).fetchone() == ("2",)
                            assert "domain" not in {
                                row[1] for row in connection.execute(
                                    "PRAGMA table_info(memory_fragments)")
                            }
                        finally:
                            if connection is not reader:
                                connection.close()
                finally:
                    release.set()
                future.result(timeout=5)
        # The original read transaction retains its old snapshot after commit.
        assert "domain" not in {
            row[1] for row in reader.execute("PRAGMA table_info(memory_fragments)")
        }
        reader.rollback()
        assert reader.execute("SELECT domain, content FROM memory_fragments").fetchone() == (
            "fact", "original")
        assert reader.execute(
            "SELECT value FROM memory_meta WHERE key='schema_version'"
        ).fetchone() == (str(schema.MEMORY_DB_SCHEMA_VERSION),)


def test_new_database_has_domain_constraint_and_no_extra_index(personal):
    personal.create_fragment("seven_days", "事实", "正文", now=NOW)
    with schema.connection(personal.root, personal.user) as db:
        assert db.execute("SELECT domain FROM memory_fragments").fetchone()[0] == "fact"
        assert db.execute("SELECT value FROM memory_meta WHERE key='schema_version'").fetchone()[0] == str(schema.MEMORY_DB_SCHEMA_VERSION)
        assert all("domain" not in str(r["name"]) for r in db.execute("PRAGMA index_list(memory_fragments)"))
