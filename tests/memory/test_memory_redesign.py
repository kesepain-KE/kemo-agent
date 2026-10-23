"""Regression contracts for evidence, resumable work, archive and generated skills."""
from tests.type_assertions import require_present

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import threading
from unittest.mock import patch

import pytest

from agents.self_improve.executor import execute
from plugins.skill_creater.tool import run as skill_tool
from run.agents import AgentRunResult, AgentOutputError
from run.memory import MemoryStore
from run.memory.analysis import persist_round_memory_analysis
from run.memory.archive import archive_expired
from run.memory.evidence import extraction_rounds, quoted_rounds
from run.memory.extraction import run_extraction_pages, source_pages, INPUT_CHAR_BUDGET
from run.memory.generated_skills import reconcile
from run.memory.skill_work import queue_conversion, retry_conversions
from run.memory.store import connection, _READY_DATABASES
from run.memory.work import WorkLedger


NOW = datetime(2026, 10, 15, 8, tzinfo=timezone.utc)
BODY = "用户长期使用 Windows 系统并采用命令行部署项目。"
SKILL = """# Deployment validation

Verify an existing local deployment without inventing commands.

## 何时使用
用户要求验证已有部署，且能取得该项目的真实检查指令时使用。

## 步骤
1. 读取项目说明，核对实际服务名称与验证命令，不猜测端口。
2. 执行已获授权的只读检查并比对结果，记录未通过的检查项。

## 失败处理
如果检查失败，先检查退出码和日志；停止后续发布，保留现场并报告失败，不自动重试破坏性操作。
"""


@pytest.fixture
def store(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    return MemoryStore(tmp_path, "alice", {})


def result(candidates=None, metadata=None, **data):
    return AgentRunResult(agent="self_improve",
                          data={"candidates": candidates or [], **data},
                          raw_text="", usage={}, model="mock", metadata=metadata or {})


def seed(store, tier="seven_days", name="deploy", *, expired=False):
    return store.create_fragment(tier, name, BODY,
                                 now=NOW - timedelta(days=10 if expired else 1))


def context(store, model):
    return SimpleNamespace(runner=SimpleNamespace(root=store.root, user=store.user,
                                                 config=store.config), run_model=model)


def old_reference():
    return {"memory_ref": "seven_days:deploy.md", "filename": "deploy.md", "content": BODY}


def receipt_metadata():
    return {"tool_calls": [{"name": "memory_manage", "result": {
        "ok": True, "result": {"action": "search_many", "results": [
            {"matches": [old_reference()]}]}}}]}


def guided_candidate(**changes):
    return {"action": "reinforce", **old_reference(), "durable": True,
            "evidence": "你长期使用 Windows 系统并采用命令行部署项目。",
            "evidence_rounds": [1],
            "user_evidence": "确认，我依然在 Windows 上用命令行部署项目。", **changes}


def input_rounds(user="确认，我依然在 Windows 上用命令行部署项目。", assistant=None):
    return [{"round": 1, "committed_at": NOW.isoformat(), "messages": [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant or guided_candidate()["evidence"]}]}]


def test_v1_migration_preserves_legacy_events_and_fragments(store):
    seed(store)
    path = store.database_path()
    with sqlite3.connect(path) as db:
        # Recreate the old four-column table with an existing event.
        db.execute("DROP TABLE memory_weight_events")
        db.execute("""CREATE TABLE memory_weight_events(
            fragment_id INTEGER NOT NULL, evidence_date TEXT NOT NULL,
            reason TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(fragment_id,evidence_date))""")
        ident = db.execute("SELECT id FROM memory_fragments").fetchone()[0]
        db.execute("INSERT INTO memory_weight_events VALUES(?,?,'history_evidence',?)",
                   (ident, "2026-10-10", NOW.isoformat()))
        db.execute("UPDATE memory_meta SET value='1' WHERE key='schema_version'")
    _READY_DATABASES.discard(str(path.resolve()).casefold())
    with connection(store.root, store.user) as db:
        assert db.execute("SELECT source FROM memory_weight_events").fetchone()[0] == "historical_migration"
        assert db.execute("SELECT value FROM memory_meta WHERE key='schema_version'").fetchone()[0] == "2"
    assert store.get_entry("seven_days", "deploy")["content"] == BODY


def test_assistant_quote_reinforces_only_with_explicit_same_round_user_backing(store):
    seed(store)
    output = execute(context(store, lambda _: result([guided_candidate()], receipt_metadata())),
                     {"trigger": "context_compression", "rounds": input_rounds()})
    accepted = output.data["candidates"]
    assert len(accepted) == 1
    assert accepted[0]["evidence_source"] == "assistant"
    outcome = store.upsert_candidates(accepted, source={"evidence_dates": {"1": "2026-10-15"}}, now=NOW)
    assert outcome["weighted"] == ["deploy.md"]
    assert store.get_entry("seven_days", "deploy")["content"] == BODY
    with connection(store.root, store.user) as db:
        assert db.execute("SELECT source FROM memory_weight_events").fetchone()[0] == "assistant"
    again = store.upsert_candidates(accepted, source={"evidence_dates": {"1": "2026-10-15"}}, now=NOW)
    assert again["weighted"] == []


def test_assistant_cannot_smuggle_explicit_promotion(store):
    seed(store)
    output = execute(context(store, lambda _: result(
        [guided_candidate(explicit=True)], receipt_metadata())),
        {"trigger": "context_compression", "rounds": input_rounds()})
    assert output.data["candidates"][0]["explicit"] is False
    store.upsert_candidates(output.data["candidates"],
                            source={"evidence_dates": {"1": "2026-10-15"}}, now=NOW)
    assert store.locate("deploy").tier == "seven_days"


def test_user_and_assistant_share_daily_lock_and_user_wins_merged_source(store):
    seed(store)
    candidate = guided_candidate()
    candidate["evidence_sources"] = {"1": "user"}
    candidate["evidence_source"] = "user"
    assistant = {**candidate, "evidence_sources": {"1": "assistant"}, "evidence_source": "assistant"}
    outcome = persist_round_memory_analysis(
        root=store.root, user=store.user, config={}, operation_id="mixed-evidence",
        analysis={"status": "completed", "source": {"evidence_dates": {"1": "2026-10-15"}},
                  "candidates": [candidate, assistant]})
    assert outcome["persisted"]["weighted"] == ["deploy.md"]
    with connection(store.root, store.user) as db:
        rows = db.execute("SELECT source FROM memory_weight_events").fetchall()
    assert len(rows) == 1
    assert rows[0]["source"] == "user"


@pytest.mark.parametrize("user", ["继续", "嗯确认", "确认你的所有说法", "我依然用 Windows 吗？"])
def test_injected_memory_recitation_alone_cannot_self_reinforce(store, user):
    seed(store)
    data = input_rounds(user=user)
    output = execute(context(store, lambda _: result(
        [guided_candidate(user_evidence=user)], receipt_metadata())),
        {"trigger": "context_compression", "rounds": data})
    assert output.data["candidates"] == []
    assert store.get_entry("seven_days", "deploy")["weight"] == 0


@pytest.mark.parametrize("action", ["create", "revise"])
def test_assistant_cannot_create_or_revise(store, action):
    if action == "revise":
        seed(store)
    candidate = guided_candidate(action=action, filename="new.md" if action == "create" else "deploy.md")
    if action == "create":
        candidate.pop("memory_ref")
    output = execute(context(store, lambda _: result([candidate], receipt_metadata())),
                     {"trigger": "context_compression", "rounds": input_rounds()})
    assert output.data["candidates"] == []


@pytest.mark.parametrize("body", [
    "> 已确认 Windows 命令行部署项目。",
    "```text\n已确认 Windows 命令行部署项目。\n```",
    "```text\n已确认 Windows 命令行部署项目。",
    "可能采用 Windows 命令行部署项目。",
    "token=super-secret-sensitive-value",
])
def test_tool_quote_guess_and_credentials_not_assistant_evidence(body):
    data = input_rounds(assistant=body)
    assert quoted_rounds({"evidence": body, "evidence_rounds": [1]}, data, role="assistant") == []
    assert all("tools" not in item and "think" not in item for item in extraction_rounds(data))


@pytest.mark.parametrize("body", [
    '{"token": "sensitive-example-value"}',
    "{'password': 'sensitive-example-value'}",
    '{"api_key": "sensitive-example-value"}',
])
def test_json_credentials_never_enter_sources_or_durable_candidates(store, body):
    from run.memory.evidence import safe_body
    assert safe_body(body, role="user") == ""
    assert safe_body(body, role="assistant") == ""
    data = {"trigger": "context_compression", "rounds": extraction_rounds(input_rounds())}
    extracted = run_extraction_pages(context(store, lambda _: result([{"content": body}])), data)
    assert extracted.data["candidates"] == []
    with connection(store.root, store.user) as db:
        payloads = db.execute("SELECT payload_json FROM memory_work_items").fetchall()
    assert payloads
    assert all("sensitive-example-value" not in row[0] for row in payloads)


def test_candidate_persistence_rejects_sensitive_extra_fields_before_ledger_write(store):
    analysis = {"status": "completed", "candidates": [
        {"action": "create", "filename": "safe", "content": "用户使用 Windows 系统。"},
        {"action": "create", "filename": "unsafe", "content": "用户使用 Linux 系统。",
         "extra": {"token": "sensitive-example-value"}}]}
    outcome = persist_round_memory_analysis(
        root=store.root, user=store.user, config={}, analysis=analysis, operation_id="safe-ledger")
    assert outcome["status"] == "completed"
    assert outcome["persisted"]["rejected"] == 1
    assert outcome["persisted"]["created"] == ["safe.md"]
    with connection(store.root, store.user) as db:
        payloads = db.execute("SELECT payload_json FROM memory_work_items").fetchall()
    assert all("sensitive-example-value" not in row[0] for row in payloads)


def test_source_budget_splits_without_dropping_tail():
    body = "正文片段" * 7000 + "末尾独立事实"
    pages = source_pages(extraction_rounds(input_rounds(user=body)))
    assert len(pages) > 1
    for page in pages:
        assert sum(len(m["content"]) for item in page for m in item["messages"]) <= INPUT_CHAR_BUDGET
    assert any("末尾独立事实" in m["content"] for page in pages for item in page for m in item["messages"])


def test_saturated_model_page_resumes_unreturned_facts_and_survives_restart(store, monkeypatch):
    store.config = {"memory": {"extraction_max_candidates_per_batch": 2}}
    monkeypatch.setattr("run.memory.extraction.MAX_PAGES_PER_RUN", 1)
    calls = []

    def model(data):
        calls.append(data)
        if not data["continuation"]["already_returned"]:
            return result([{"filename": "a", "evidence": "甲"}, {"filename": "b", "evidence": "乙"}])
        return result([{"filename": "c", "evidence": "丙"}])

    data = {"trigger": "context_compression", "rounds": extraction_rounds(input_rounds())}
    with pytest.raises(AgentOutputError, match="预算用尽"):
        run_extraction_pages(context(store, model), data)
    assert len(WorkLedger(store.root, store.user).pending("extraction")) == 1
    # A fresh context/ledger instance continues rather than rerunning page zero.
    resumed = run_extraction_pages(context(store, model), data)
    assert [c["filename"] for c in resumed.data["candidates"]] == ["a", "b", "c"]
    assert len(calls) == 2
    assert not WorkLedger(store.root, store.user).pending("extraction")


def test_saturated_no_progress_never_claims_complete(store):
    store.config = {"memory": {"extraction_max_candidates_per_batch": 1}}
    data = {"trigger": "context_compression", "rounds": extraction_rounds(input_rounds())}
    with pytest.raises(AgentOutputError, match="没有前进"):
        run_extraction_pages(context(store, lambda _: result([{"filename": "a"}])), data)
    assert WorkLedger(store.root, store.user).pending("extraction")[0]["payload"]["page"] == 0


def test_candidate_overflow_applies_batch_limit_and_replays_without_duplicates(store):
    config = {"memory": {"extraction_max_candidates_per_batch": 2}}
    analysis = {"status": "completed", "source": {"evidence_dates": {"1": "2026-10-14"}},
                "candidates": [{"action": "create", "filename": f"fact-{i}", "content": f"事实 {i}",
                                "evidence_rounds": [1], "evidence_source": "user"} for i in range(5)]}
    def persist():
        return persist_round_memory_analysis(root=store.root, user=store.user,
                                             config=config, analysis=analysis, operation_id="overflow")
    assert persist()["status"] == "failed"
    assert len(store.load_all()) == 2
    assert persist()["status"] == "failed"
    assert len(store.load_all()) == 4
    final = persist()
    assert final["status"] == "completed"
    assert len(store.load_all()) == 5
    assert persist()["persisted"]["replayed"]
    assert len(store.load_all()) == 5


def test_real_history_cursor_waits_for_all_candidate_batches(store):
    from run.history import (commit_window, empty_window, load_window,
                             queue_memory_extraction, read_registry, write_registry)
    from run.scheduler.memory_recovery import recover_pending_memory
    config = {"memory": {"extraction_mode": "compression_only",
                          "extraction_max_candidates_per_batch": 2,
                          "recovery_max_rounds_per_scan": 1}}
    (store.root / "config").mkdir()
    (store.root / "config/global_config.json").write_text(json.dumps(config), "utf-8")
    (store.root / "users/alice/user_config.json").write_text("{}", "utf-8")
    archive = store.root / "users/alice/history/conv_pending"
    window = empty_window("alice", "web", "pending")
    window["text"]["messages"] = [{"role": "user", "content": "五个独立事实"},
                                  {"role": "assistant", "content": "已记录"}]
    window["data"].update(rounds=1, memory_processed_round=0, memory_status="deferred",
                          round_metrics=[{"round": 1, "committed_at": NOW.isoformat()}])
    commit_window(archive, window)
    queue_memory_extraction(store.root, store.user, "web", "pending", target_round=1,
                            reason="manual_compression")
    analysis = {"status": "completed", "source": {"evidence_dates": {"1": "2026-10-15"}},
                "candidates": [{"action": "create", "filename": f"queued-{i}",
                                "content": f"独立事实 {i}", "evidence_rounds": [1]}
                               for i in range(5)]}
    scheduler = SimpleNamespace(root=store.root, provider_factory=lambda _: None,
                                _stop_event=threading.Event(), _report_error=lambda *args: None)
    with patch("run.scheduler.maintenance.analyze_round_memory", return_value=analysis):
        for expected_count in (2, 4, 5):
            recovered = recover_pending_memory(scheduler, store.user)
            data = load_window(archive)["data"]
            assert not recovered["failed"]
            assert not recovered["capacity_blocked"]
            assert len(store.load_all()) == expected_count
            assert data["memory_processed_round"] == (1 if expected_count == 5 else 0)
            if expected_count < 5:
                assert data["memory_status"] == "queued"
                assert "memory_error" not in data
                sessions, active = read_registry(store.root, store.user)
                for record in sessions.values():
                    record["memory_retry_at"] = "2000-01-01T00:00:00+00:00"
                write_registry(store.root, store.user, sessions, active)
    assert not WorkLedger(store.root, store.user).pending("candidate")


def test_archive_failure_keeps_active_row_and_pending_work(store):
    seed(store, expired=True)
    with patch("run.memory.archive._publish", side_effect=OSError("disk unavailable")):
        with pytest.raises(OSError):
            archive_expired(store, "seven_days", "deploy.md", NOW)
    assert store.get_entry("seven_days", "deploy") is not None
    assert len(WorkLedger(store.root, store.user).pending("archive")) == 1
    archived = archive_expired(store, "seven_days", "deploy.md", NOW)
    assert archived["archived"]
    assert (store.root / archived["path"]).read_text("utf-8").endswith(BODY + "\n")
    assert store.load_all() == []
    assert not WorkLedger(store.root, store.user).pending("archive")


def test_archive_published_then_database_failure_reuses_same_file(store):
    seed(store, expired=True)
    from run.memory.archive import _publish
    def publish_then_fail(path, body):
        _publish(path, body)
        raise OSError("simulated crash before delete")
    with patch("run.memory.archive._publish", side_effect=publish_then_fail):
        with pytest.raises(OSError):
            archive_expired(store, "seven_days", "deploy", NOW)
    assert store.load_all()
    first = list((store.root / "users/alice/improve/expired_memory").rglob("*.md"))
    assert len(first) == 1
    archive_expired(store, "seven_days", "deploy", NOW + timedelta(days=1))
    assert list((store.root / "users/alice/improve/expired_memory").rglob("*.md")) == first
    assert BODY not in store.select_tier_for_prompt("seven_days", max_files=100).text


def test_archive_database_commit_failure_rolls_back_deletion_and_reuses_file(store):
    seed(store, expired=True)
    @contextmanager
    def fail_write_commit(root, user, *, write=False):
        with connection(root, user, write=write) as db:
            yield db
            if write:
                raise sqlite3.OperationalError("simulated commit failure")
    with patch("run.memory.archive.connection", fail_write_commit):
        with pytest.raises(sqlite3.OperationalError):
            archive_expired(store, "seven_days", "deploy", NOW)
    assert store.get_entry("seven_days", "deploy") is not None
    assert WorkLedger(store.root, store.user).pending("archive")
    files = list((store.root / "users/alice/improve/expired_memory").rglob("*.md"))
    assert len(files) == 1
    archive_expired(store, "seven_days", "deploy", NOW + timedelta(days=1))
    assert store.get_entry("seven_days", "deploy") is None
    assert list((store.root / "users/alice/improve/expired_memory").rglob("*.md")) == files
    assert not WorkLedger(store.root, store.user).pending("archive")


def test_user_skeleton_archive_directory_is_not_a_knowledge_source(store):
    from user_create import _post_process_dirs
    from run.config.knowledge import select_knowledge_index
    _post_process_dirs(store.root, store.user)
    directory = store.root / "users/alice/improve/expired_memory"
    assert directory.is_dir()
    directory.joinpath("index.md").write_text("# Archive-only sentinel", "utf-8")
    knowledge = store.root / "users/alice/knowledge/index.md"
    knowledge.write_text("# Actual knowledge", "utf-8")
    selected = select_knowledge_index(store.root, store.user, scopes=("user",))
    assert "Actual knowledge" in selected.text
    assert "Archive-only sentinel" not in selected.text
    assert len(selected.documents) == 1


@pytest.mark.parametrize("body", ["token=legacy-secret-value", '{"token": "legacy-secret-value"}'])
def test_archive_does_not_publish_legacy_credentials(store, body):
    seed(store, expired=True)
    with connection(store.root, store.user, write=True) as db:
        db.execute("UPDATE memory_fragments SET content=?", (body,))
    with pytest.raises(ValueError, match="敏感凭据"):
        archive_expired(store, "seven_days", "deploy", NOW)
    assert store.load_all()
    assert not list((store.root / "users/alice/improve").rglob("*.md"))


def test_archive_collision_preserves_foreign_file_and_suffixes(store):
    seed(store, expired=True)
    from run.memory.archive import _publish
    injected = []
    def collide_once(path, body):
        if not injected:
            path.write_text("unrelated archive", "utf-8")
            injected.append(path)
        _publish(path, body)
    with patch("run.memory.archive._publish", side_effect=collide_once):
        archived = archive_expired(store, "seven_days", "deploy", NOW)
    assert injected[0].read_text("utf-8") == "unrelated archive"
    assert archived["path"].endswith("-1.md")
    assert (store.root / archived["path"]).is_file()


def generated_skill(store, name="deploy-skill", content=SKILL):
    return skill_tool("create", "agent_create", name, content,
                      source_memories=["deploy.md"],
                      context={"root": str(store.root), "user": store.user,
                               "agent": "self_improve", "agent_trigger": "memory_promotion"})


def test_generated_skill_quality_and_sources_are_mandatory(store):
    seed(store)
    with pytest.raises(ValueError, match="质量门禁"):
        generated_skill(store, content="# Facts\n\nWindows is installed.")
    with pytest.raises(ValueError, match="source_memories"):
        skill_tool("create", "agent_create", "no-source", SKILL,
                   context={"root": str(store.root), "user": store.user,
                            "agent": "self_improve", "agent_trigger": "memory_promotion"})
    assert not (store.root / "users/alice/user_skills/agent_create/no-source").exists()
    with pytest.raises(ValueError, match="可执行步骤"):
        generated_skill(store, content=SKILL.replace(
            "1. 读取项目说明，核对实际服务名称与验证命令，不猜测端口。",
            "1. 用户的 Windows 系统长期可用。").replace(
            "2. 执行已获授权的只读检查并比对结果，记录未通过的检查项。",
            "2. 用户的服务器拥有蓝色外壳。"))


def test_independent_name_collision_creates_stable_suffix_without_overwrite(store):
    seed(store)
    skill_tool("create", "agent_create", "occupied", "# Private\n\nKeep this asset.",
               context={"root": str(store.root), "user": store.user})
    created = generated_skill(store, name="occupied")
    assert created["deduplication"]["resolution"] == "suffix_collision"
    assert created["name"].startswith("occupied-")
    assert (store.root / "users/alice/user_skills/agent_create/occupied/SKILL.md").read_text(
        "utf-8").startswith("# Private")
    assert generated_skill(store, name="occupied")["name"] == created["name"]


def test_generated_same_content_reuses_and_collision_suffix_preserves_existing(store):
    seed(store)
    first = generated_skill(store)
    assert generated_skill(store)["deduplication"]["resolution"] == "reuse_identical"
    assert first["name"] == "deploy-skill"
    skill_tool("create", "agent_create", "occupied", "# Existing\n\nUnrelated private skill.",
               context={"root": str(store.root), "user": store.user})
    other = generated_skill(store, name="occupied", content=SKILL.replace(
        "Deployment validation", "A different procedure").replace("读取项目说明", "读取已授权的远程文档"))
    # A semantic near-duplicate is explicitly deferred, never silently recreated.
    assert other["deferred"]
    assert not other["valid"]
    assert (store.root / "users/alice/user_skills/agent_create/occupied/SKILL.md").read_text(
        "utf-8").startswith("# Existing")


def test_source_edit_and_delete_stale_skill_but_normal_promotion_does_not(store):
    seed(store)
    created = generated_skill(store)
    assert created["provenance"]["sources"][0]["filename"] == "deploy.md"
    store._promote_location(store.locate("deploy"), "one_month", NOW)
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "active"
    store.edit_fragment("one_month", "deploy", "用户已改用 Linux 部署。", now=NOW)
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "stale"
    assert Path(created["path"]).is_dir()  # No destructive automatic cleanup.
    store.delete_fragment("one_month", "deploy")
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "stale"


def test_source_expiration_marks_skill_stale_before_archive_cleanup(store):
    seed(store)
    generated_skill(store)
    with connection(store.root, store.user, write=True) as db:
        db.execute("UPDATE memory_fragments SET expires_at=?",
                   ((NOW - timedelta(days=1)).isoformat(),))
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "stale"
    assert store.get_entry("seven_days", "deploy") is not None


@pytest.mark.parametrize("split", [False, True])
def test_corrected_source_cannot_reactivate_old_skill_during_promotion(store, split):
    seed(store)
    generated_skill(store)
    store.edit_fragment("seven_days", "deploy", "用户确认已经改用 Linux 部署项目。", now=NOW)
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "stale"
    if split:
        store._split_promote_location(store.locate("deploy"), "one_month", NOW, [
            {"filename": "new-device", "content": "Linux 系统"},
            {"filename": "new-workflow", "content": "命令行部署项目"}])
    else:
        store._promote_location(store.locate("deploy"), "one_month", NOW)
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "stale"


def test_merging_valid_source_cannot_overwrite_stale_destination_binding(store):
    seed(store)
    store.create_fragment("one_month", "target", "部署命令需要先运行检查。", now=NOW)
    skill_tool("create", "agent_create", "deploy-skill", SKILL,
               source_memories=["deploy.md", "target.md"],
               context={"root": str(store.root), "user": store.user,
                        "agent": "self_improve", "agent_trigger": "memory_promotion"})
    store.edit_fragment("one_month", "target", "部署流程已变更，不再使用原检查命令。", now=NOW)
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "stale"
    store._promote_location(store.locate("deploy"), "one_month", NOW,
                            target_filename="target", merged_content="新的命令行部署检查规则。")
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "stale"


def test_split_and_merge_transfer_generated_skill_lineage(store, monkeypatch):
    monkeypatch.setattr("run.memory.utc_now", lambda: NOW)
    seed(store)
    generated_skill(store)
    store._split_promote_location(store.locate("deploy"), "one_month", NOW, [
        {"filename": "device", "content": "Windows 系统"},
        {"filename": "procedure", "content": "命令行部署项目"}])
    state = reconcile(store.root, store.user)["deploy-skill"]
    assert state["status"] == "active"
    assert {s["filename"] for s in state["sources"]} == {"device.md", "procedure.md"}
    store.create_fragment("half_year", "target", "目标部署规则。", now=NOW)
    store._promote_location(store.locate("procedure"), "half_year", NOW,
                            merged_content="目标部署规则采用命令行。", target_filename="target")
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "active"
    store.delete_fragment("one_month", "device")
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "stale"


def test_conversion_survives_tool_failure_and_retries_after_promotion(store):
    seed(store, tier="half_year")
    requested = {"from_tier": "half_year", "to_tier": "permanent",
                 "filename": "deploy.md", "content": BODY}
    queue_conversion(store, requested)
    store._promote_location(store.locate("deploy"), "permanent", NOW)
    runner = SimpleNamespace(run=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("tool removed")))
    assert retry_conversions(store, runner)[0]["stage"] == "skill_conversion"
    assert WorkLedger(store.root, store.user).pending("skill")
    def model(*args, **kwargs):
        generated_skill(store)
        return result(promotions=[{"filename": "deploy.md", "skill_created": True}])
    assert retry_conversions(store, SimpleNamespace(run=model)) == []
    assert not WorkLedger(store.root, store.user).pending("skill")


def test_conversion_pending_lineage_survives_split(store):
    seed(store, tier="half_year")
    requested = {"from_tier": "half_year", "to_tier": "permanent",
                 "filename": "deploy.md", "content": BODY}
    queue_conversion(store, requested)
    store._split_promote_location(store.locate("deploy"), "permanent", NOW, [
        {"filename": "device", "content": "Windows 系统"},
        {"filename": "procedure", "content": "命令行部署项目"}])
    captured = []
    def model(_name, data, **kwargs):
        captured.append(data)
        skill_tool("create", "agent_create", "combined", SKILL,
                   source_memories=data["conversion_sources"],
                   context={"root": str(store.root), "user": store.user,
                            "agent": "self_improve", "agent_trigger": "memory_promotion"})
        return result(promotions=[{"filename": "device.md", "skill_created": True}])
    assert retry_conversions(store, SimpleNamespace(run=model)) == []
    assert set(captured[0]["conversion_sources"]) == {"device.md", "procedure.md"}
    assert not WorkLedger(store.root, store.user).pending("skill")


@pytest.mark.parametrize("split", [False, True])
def test_due_permanent_promotion_settles_skill_after_source_transfer(store, split):
    from cron.review_due import scan_and_promote
    seed(store, tier="half_year")
    with connection(store.root, store.user, write=True) as db:
        db.execute("UPDATE memory_fragments SET weight=60, expires_at=?",
                   ((NOW - timedelta(days=1)).isoformat(),))
    def model(_name, data, **kwargs):
        generated_skill(store)
        decision = {"from_tier": "half_year", "to_tier": "permanent",
                    "filename": "deploy.md", "memory_type": "B", "skill_created": True}
        if split:
            decision["split_into"] = [
                {"filename": "device", "content": "Windows 系统", "memory_type": "B"},
                {"filename": "procedure", "content": "命令行部署项目", "memory_type": "B"}]
        return result(promotions=[decision])
    with patch("cron.review_due.AgentRunner", return_value=SimpleNamespace(run=model)):
        promoted = scan_and_promote(root=store.root, user=store.user, config={}, now=NOW)
    assert promoted["status"] == "completed"
    assert promoted["applied"] == ["deploy.md"]
    assert not promoted["errors"]
    assert reconcile(store.root, store.user)["deploy-skill"]["status"] == "active"
    assert not WorkLedger(store.root, store.user).pending("skill")


def test_prompt_excludes_stale_but_catalog_keeps_it_visible(store, monkeypatch):
    from run.config.prompt_sources import PromptSourceRegistry
    monkeypatch.setattr("run.memory.utc_now", lambda: NOW)
    seed(store)
    generated_skill(store)
    registry = PromptSourceRegistry(store.root, store.user)
    registry.add_skills("user", store.root / "users/alice/user_skills")
    assert len(registry.select_skills()) == 1
    store.delete_fragment("seven_days", "deploy")
    assert registry.select_skills() == ()
    assert len(registry.select_skills(include_stale=True)) == 1


def test_web_catalog_retains_stale_status_and_manual_cleanup(store, monkeypatch):
    from web.service import WebRunService
    monkeypatch.setattr("run.memory.utc_now", lambda: NOW)
    (store.root / "config").mkdir()
    (store.root / "config/global_config.json").write_text("{}", "utf-8")
    (store.root / "users/alice/user_config.json").write_text("{}", "utf-8")
    seed(store)
    generated_skill(store)
    service = WebRunService(store.root)
    first = next(i for i in service.skills("alice")["items"] if i["category"] == "agent_generated")
    assert first["memory_lifecycle"]["status"] == "active"
    store.delete_fragment("seven_days", "deploy")
    stale = next(i for i in service.skills("alice")["items"] if i["category"] == "agent_generated")
    assert stale["memory_lifecycle"]["status"] == "stale"
    assert stale["editable"] and not stale["enabled"]
    assert service.delete_skill("alice", "agent_generated", stale["name"])["deleted"]
    assert "deploy-skill" not in reconcile(store.root, store.user)


def test_extraction_lease_prevents_duplicate_processing_and_recovers_expiration(store):
    ledger = WorkLedger(store.root, store.user)
    ledger.enqueue("extraction", "lease", {})
    with ledger.lease("lease"):
        with pytest.raises(AgentOutputError, match="正在处理"):
            with ledger.lease("lease"):
                pytest.fail("duplicate lease")
    with connection(store.root, store.user, write=True) as db:
        db.execute("UPDATE memory_work_items SET lease_token='dead', lease_until='2000-01-01' "
                   "WHERE work_key='lease'")
    with ledger.lease("lease") as token:
        ledger.save("lease", {"resumed": True}, lease_token=token)
    assert require_present(ledger.get("lease"))["payload"]["resumed"]


def test_builtin_and_template_subagents_use_80_without_relaxing_guards():
    root = Path(__file__).resolve().parents[2]
    for path in [*(root / "agents").glob("*/agent-config.json"),
                 root / "template/agent/agent-config.json"]:
        assert json.loads(path.read_text("utf-8"))["tools"]["max_iterations"] == 80
    config = json.loads((root / "config/global_config.json").read_text("utf-8"))
    assert config["tools"]["consecutive_identical_call_limit"] == 8
    assert config["history"]["consecutive_tool_fail_limit"] == 5
    assert config["agent_runtime"]["default_timeout"] == 600
    assert [config["memory"]["tiers"][tier]["upgrade_threshold"]
            for tier in ("seven_days", "one_month", "half_year")] == [3, 10, 60]
