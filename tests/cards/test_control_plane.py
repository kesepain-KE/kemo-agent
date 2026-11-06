"""CardStream replaces history consumers, not just the main inference path."""
from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import patch

import pytest

from run.cards import CardConflictError, CardStore, IntegrationStore, compact, session_key
from run.config import load_config
from run.history import (
    claim_pending_memory, claim_pending_summary, close_session, delete_all_sessions,
    delete_session, find_record, finish_memory_claim, finish_summary_claim,
    get_or_reserve_active, mutate_registry_record, queue_memory_extraction,
    queue_summary, reserve_session, update_session_marks, update_title,
    upsert_registry_record, withdraw_last_integration,
)
from run.history.archive_catalog import query_archives
from run.history.archive_query_common import decode_token, encode_token, serialized_size
from run.history.archive_reader import read_archive
from run.history.lease_presence import update_presence
from run.history.web_lifecycle import active_session_lease_clients, touch_session_lease
from run.memory import extract_card_memory
from tests.card_history_support import seed_history
from tests.cards.test_runtime import root
from tests.cards.test_store import batch, summary
from web.errors import InvalidRequestError
from web.service import WebRunService


def completed(root, identity="s1", *, text="用户原文" * 1000):
    store = CardStore(root, "alice")
    reserve_session(root, "alice", "web", identity)
    integration = IntegrationStore(store, "web", identity, "i_" + identity)
    integration.start()
    store.append("web", identity, integration.id, "user_text", {"text": text}, level="initial")
    integration.request_started()
    integration.request_finished(accepted_seq=store.snapshot("web", identity).last_seq)
    integration.finish("completed", final_payload={"text": "完成", "reasoning": "只留原文"})
    return store, integration


def test_history_search_and_web_read_raw_after_compaction_and_never_touch_old_files(root):
    from plugins.history_search.tool import run
    old = root / "users/alice/history"
    old.mkdir()
    sentinels = {old / "history.sqlite3": b"not sqlite; never open", old / "window.json": b"legacy data"}
    for path, body in sentinels.items():
        path.write_bytes(body)
    store, _ = completed(root, text="needle-original" * 2000)
    before = tuple(card.raw_payload_json for card in store.raw_page("web", "s1"))
    compact(store, "web", "s1", summarize=lambda _: summary())
    history = WebRunService(root).history("alice", "s1")
    assert history["engine"] == "cardstream"
    assert history["messages"][0]["content"] == "needle-original" * 2000
    assert history["cards"][0]["message"]["content"] == history["messages"][0]["content"]
    assert history["user_messages"][0]["content"] == "needle-original" * 2000
    result = run(action="search", query="needle-original",
                 context={"root": str(root), "user": "alice"})
    assert result["matches"][0]["seq"] == 1
    assert tuple(card.raw_payload_json for card in store.raw_page("web", "s1")) == before
    for path, body in sentinels.items():
        assert path.read_bytes() == body
    assert sorted(path.name for path in old.iterdir() if not path.name.startswith("cards.sqlite3")) == sorted(
        path.name for path in sentinels)


def test_web_history_keeps_initial_input_outside_long_raw_pages_and_preserves_cursors(root):
    store, _ = completed(root, text="earlier round")
    integration = IntegrationStore(store, "web", "s1", "long_round")
    integration.start()
    initial = store.append("web", "s1", integration.id, "user_text", {
        "text": "original long task",
        "metadata": {"origin": "web", "private": "do not expose"},
        "attachments": [{
            "asset_id": "image", "name": "original.png", "media_kind": "image",
            "scope": "file_upload", "relative_path": "original.png",
            "mime_type": "image/png", "size": 0,
        }],
    }, level="initial")
    for index in range(65):
        store.append("web", "s1", integration.id, "agent_text",
                     {"text": f"progress {index}"}, level="interim")
    store.append("web", "s1", integration.id, "user_text", {
        "text": "later guidance", "guidance_id": "guide",
    }, level="guidance")
    integration.request_started()
    integration.request_finished(accepted_seq=store.snapshot("web", "s1").last_seq)
    integration.finish("completed", final_payload={"text": "long task done"})
    service, before, seen = WebRunService(root), None, []
    while True:
        page = service.history("alice", "s1", limit=20, before=before)
        if before is None:
            assert [message["content"] for message in page["user_messages"]] == [
                "earlier round", "original long task"]
            assert page["user_messages"][1]["metadata"] == {"origin": "web"}
            assert page["user_messages"][1]["attachments"][0]["name"] == "original.png"
        else:
            assert "user_messages" not in page
        assert len(page["cards"]) <= 20
        assert page["pagination"]["first_seq"] == page["cards"][0]["seq"]
        assert page["pagination"]["last_seq"] == page["cards"][-1]["seq"]
        metric = next(item for item in page["integrations"] if item["integration_id"] == integration.id)
        anchor = metric["initial_card"]
        assert anchor["card_id"] == initial.card_id
        assert anchor["seq"] == initial.seq
        assert anchor["message"]["content"] == "original long task"
        assert anchor["message"]["metadata"] == {"origin": "web"}
        assert anchor["message"]["attachments"][0]["name"] == "original.png"
        assert "path" not in anchor["message"]["attachments"][0]
        seen = [card["seq"] for card in page["cards"]] + seen
        if not page["pagination"]["has_more_before"]:
            break
        before = page["pagination"]["next_before"]
    assert seen == list(range(1, store.snapshot("web", "s1").last_seq + 1))


def test_web_history_input_anchors_are_bound_to_page_session_and_source(root):
    completed(root, "s1", text="visible input")
    completed(root, "other", text="other session secret")
    store = CardStore(root, "alice")
    app = IntegrationStore(store, "app", "s1", "app_round")
    app.start()
    store.append("app", "s1", app.id, "user_text", {"text": "app secret"}, level="initial")
    app.request_started()
    app.request_finished(accepted_seq=store.snapshot("app", "s1").last_seq)
    app.finish("completed", final_payload={"text": "app done"})
    page = WebRunService(root).history("alice", "s1", limit=1)
    assert len(page["cards"]) == 1
    assert len(page["integrations"]) == 1
    assert page["integrations"][0]["initial_card"]["message"]["content"] == "visible input"
    assert [message["content"] for message in page["user_messages"]] == ["visible input"]
    assert "secret" not in json.dumps(page)


def test_web_history_navigation_includes_all_formal_inputs_without_deduplicating_text(root):
    store, _ = completed(root, text="重复发言")
    for iid, text, attachments in (
        ("repeat", "重复发言", []),
        ("attachment", "", [{"asset_id": "doc", "name": "notes.txt", "scope": "external"}]),
        ("automatic", "【任务计划连续执行】\n计划 ID：plan", []),
        ("withdrawn", "撤回的问题", []),
    ):
        integration = IntegrationStore(store, "web", "s1", iid)
        integration.start()
        store.append("web", "s1", iid, "user_text",
                     {"text": text, "attachments": attachments}, level="initial")
        store.append("web", "s1", iid, "user_text", {"text": "过程引导"}, level="guidance")
        integration.request_started()
        integration.request_finished(accepted_seq=store.snapshot("web", "s1").last_seq)
        integration.finish("completed", final_payload={"text": "完成"})
    withdraw_last_integration(root, "alice", "web", "s1",
                              expected_integration_count=5, expected_prompt="撤回的问题")
    page = WebRunService(root).history("alice", "s1", limit=1)
    assert len(page["cards"]) == 1
    messages = page["user_messages"]
    assert [message["content"] for message in messages] == ["重复发言", "重复发言", ""]
    assert len({message["card_id"] for message in messages}) == 3
    assert [message["seq"] for message in messages] == sorted(message["seq"] for message in messages)
    assert messages[-1]["attachments"][0]["name"] == "notes.txt"


def test_close_and_bulk_delete_refuse_live_executions_and_roll_back_every_identity(root):
    store, _ = completed(root, "first")
    live = IntegrationStore(store, "web", "live", "i_live")
    live.start()
    live.register_tool("callid_live", {"name": "effect", "arguments": {}})
    live.finish("cancelled")
    for action in (
        lambda: close_session(root, "alice", "web", "live"),
        lambda: delete_session(root, "alice", "web", "live"),
        lambda: delete_all_sessions(root, "alice", "web"),
    ):
        with pytest.raises(CardConflictError):
            action()
    assert find_record(root, "alice", "web", "first")["lifecycle"] == "open"
    assert store.session("web", "live")["lifecycle"] == "open"


def test_presence_is_source_scoped_and_release_sequence_cannot_be_reversed(root):
    store, _ = completed(root)
    store.ensure_session("app", "s1")
    assert update_presence(root, "alice", "web", "s1", "doc", 2, now=10)["active"]
    assert update_presence(root, "alice", "web", "s1", "doc", 4, release=True, now=11)["accepted"]
    assert not update_presence(root, "alice", "web", "s1", "doc", 3, now=12)["active"]
    assert touch_session_lease(root, "alice", "web", "s1", "doc", now=13) == 0
    assert update_presence(root, "alice", "app", "s1", "doc", 1, now=13)["active"]
    assert active_session_lease_clients(root, "alice", "web", "s1", now=13) == []
    assert active_session_lease_clients(root, "alice", "app", "s1", now=13) == ["doc"]


def test_other_document_lease_blocks_entire_bulk_delete(root):
    store, _ = completed(root, "first")
    completed(root, "second")
    update_presence(root, "alice", "web", "second", "other", 1)
    with pytest.raises(CardConflictError):
        delete_all_sessions(root, "alice", "web", client_id="mine")
    assert store.session("web", "first")["lifecycle"] == "open"
    assert store.session("web", "second")["lifecycle"] == "open"
    count, deleted_files = delete_all_sessions(root, "alice", "web", client_id="other")
    assert (count, deleted_files) == (2, 0)
    assert store.raw_page("web", "first") == ()


def test_marks_survive_concurrent_metadata_mutation_and_stale_snapshots_cannot_overwrite_them(root):
    completed(root)
    stale = find_record(root, "alice", "web", "s1")
    update_session_marks(root, "alice", "web", "s1", favorite=True, pinned=True)
    with pytest.raises(CardConflictError, match="Stale metadata"):
        upsert_registry_record(root, "alice", {**stale, "title": "stale title"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda n: mutate_registry_record(root, "alice", "web", "s1", lambda record: {**record, "title": str(n)}), range(10)))
    current = find_record(root, "alice", "web", "s1")
    assert current["favorite"] and current["pinned_at"]
    assert current["updated_at"] >= stale["updated_at"]


def test_deleted_preferred_identity_is_not_reused_for_new_raw(root):
    store, _ = completed(root)
    compact(store, "web", "s1", summarize=lambda _: summary())
    delete_session(root, "alice", "web", "s1")
    new, created = get_or_reserve_active(root, "alice", "web", "browser", preferred_session_id="s1")
    assert created and new["session_id"] != "s1"
    assert store.session("web", "s1")["lifecycle"] == "deleted"
    assert store.raw_page("web", "s1") == ()


def test_utf8_raw_pagination_is_lossless_bounded_and_frozen_against_append(root):
    body = '中文😀\\"\\n' * 1000
    store, _ = completed(root, text=body)
    archive = query_archives(root, "alice", action="list")["archives"][0]["archive_id"]
    cursor, pieces, through = "", [], None
    for index in range(1000):
        result = read_archive(root, "alice", archive_id=archive, cursor=cursor, page_char_limit=4000)
        assert serialized_size(result) <= 4000
        through = through or result["through_seq"]
        assert result["through_seq"] == through
        pieces.extend(item["content"] for item in result["messages"] if item["seq"] == 1)
        if index == 0:
            store.append("web", "s1", "later", "user_text", {"text": "not in frozen cursor"})
        if not result["has_more"]:
            break
        cursor = result["next_cursor"]
    else:
        pytest.fail("RAW cursor made no bounded progress")
    assert "".join(pieces) == body
    assert through == 2
    (root / "users/bob").mkdir()
    CardStore(root, "bob").ensure_session("web", "s1")
    with pytest.raises(ValueError, match="当前用户"):
        read_archive(root, "bob", archive_id=archive)
    first = read_archive(root, "alice", archive_id=archive, page_char_limit=4000)
    token = decode_token(first["next_cursor"], "card-read-v1")
    token[-1] = 1  # Middle of the first multibyte UTF-8 character.
    with pytest.raises(ValueError, match="UTF-8"):
        read_archive(root, "alice", archive_id=archive, cursor=encode_token(token), page_char_limit=4000)


def test_memory_claim_coverage_is_raw_seq_and_compaction_does_not_advance_it(root):
    store, _ = completed(root)
    queue_memory_extraction(root, "alice", "web", "s1")
    claim = claim_pending_memory(root, "alice", source="web", session_id="s1", input_tokens=1)
    assert (claim["memory_claim_start_seq"], claim["memory_claim_end_seq"]) == (1, 2)
    compact(store, "web", "s1", summarize=lambda _: summary())
    assert store.session("web", "s1")["memory_processed_seq"] == 0
    finish_memory_claim(root, "alice", "web", "s1", claim_id=claim["memory_claim_id"])
    following = claim_pending_memory(root, "alice", input_tokens=1)
    assert following is None
    assert store.session("web", "s1")["memory_processed_seq"] == 2


@pytest.mark.parametrize("stolen", [True, False])
def test_late_memory_analysis_cannot_apply_after_claim_loss_or_expiry(root, stolen):
    store, _ = completed(root)
    queue_memory_extraction(root, "alice", "web", "s1")
    claim = claim_pending_memory(root, "alice")
    change = {"memory_claim_id": "new-owner"} if stolen else {"memory_claim_expires_at": "2000-01-01T00:00:00+00:00"}
    mutate_registry_record(root, "alice", "web", "s1", lambda current: {**current, **change})
    with patch("run.memory.analysis.analyze_memory_batch_resilient",
               return_value={"status": "completed", "candidates": []}), patch(
               "run.memory.analysis.persist_round_memory_analysis") as persist:
        result = extract_card_memory(root=root, user="alice", source="web", session_id="s1",
            config=load_config("alice", root), agent_runner=object(), claim=claim)
    assert result["status"] == "failed"
    persist.assert_not_called()
    assert store.session("web", "s1")["memory_processed_seq"] == 0


def test_background_title_cannot_overwrite_concurrent_manual_rename(root):
    completed(root)
    queue_summary(root, "alice", "web", "s1")
    claim = claim_pending_summary(root, "alice")
    update_title(root, "alice", "web", "s1", "我的标题")
    result = finish_summary_claim(root, "alice", "web", "s1", claim_id=claim["summary_claim_id"],
                                  title="模型标题", summary="摘要")
    assert result["title"] == "我的标题" and result["summary"] == "摘要"
    assert result["summary_completed_seq"] == 2


def test_withdraw_only_changes_view_and_is_not_repeatable(root):
    store, _ = completed(root)
    before = store.raw_page("web", "s1")
    withdraw_last_integration(root, "alice", "web", "s1", expected_integration_count=1,
                              expected_prompt=before[0].raw_payload["text"])
    assert store.snapshot("web", "s1").history_tokens == 0
    after = store.raw_page("web", "s1")
    assert [(card.card_id, card.seq, card.raw_kind, card.raw_level, card.raw_payload_json, card.raw_tokens)
            for card in after] == [
                (card.card_id, card.seq, card.raw_kind, card.raw_level, card.raw_payload_json, card.raw_tokens)
                for card in before]
    assert all(not card.token_active and card.view_revision == original.view_revision + 1
               for card, original in zip(after, before))
    with pytest.raises(CardConflictError, match="已经撤回"):
        withdraw_last_integration(root, "alice", "web", "s1", expected_integration_count=1,
                                  expected_prompt=before[0].raw_payload["text"])


def test_withdraw_skips_already_withdrawn_integrations_and_preserves_raw(root):
    store, _ = completed(root, text="第一轮问题")
    second = IntegrationStore(store, "web", "s1", "i_second")
    second.start()
    store.append("web", "s1", second.id, "user_text", {"text": "第二轮问题"}, level="initial")
    second.request_started()
    second.request_finished(accepted_seq=store.snapshot("web", "s1").last_seq)
    second.finish("completed", final_payload={"text": "第二轮回答"})
    before = store.raw_page("web", "s1")

    latest = withdraw_last_integration(
        root, "alice", "web", "s1", expected_integration_count=2, expected_prompt="第二轮问题")
    previous = withdraw_last_integration(
        root, "alice", "web", "s1", expected_integration_count=2, expected_prompt="第一轮问题")

    assert latest["integration_id"] == "i_second"
    assert previous["integration_id"] == "i_s1"
    assert store.snapshot("web", "s1").history_tokens == 0
    after = store.raw_page("web", "s1")
    assert [(card.card_id, card.seq, card.raw_payload_json) for card in after] == [
        (card.card_id, card.seq, card.raw_payload_json) for card in before]
    with pytest.raises(CardConflictError, match="已经撤回"):
        withdraw_last_integration(
            root, "alice", "web", "s1", expected_integration_count=2, expected_prompt="第一轮问题")


@pytest.mark.parametrize("has_older_round", [False, True])
def test_withdraw_rejects_only_the_target_round_absorbed_by_a_summary(root, has_older_round):
    prompt = "目标提问" * 1000
    store, _ = completed(root, text="旧提问" * 1000 if has_older_round else prompt)
    if has_older_round:
        seed_history(root, "alice", "web", "s1", [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "目标回答"},
        ])
    # Cover both the summary's host integration and a later, partially absorbed round.
    before = store.replace_prefix(batch(store, 3 if has_older_round else 1), summary())
    raw_before = store.raw_page("web", "s1")

    with pytest.raises(CardConflictError, match="该轮内容已合并进压缩摘要"):
        withdraw_last_integration(
            root, "alice", "web", "s1",
            expected_integration_count=2 if has_older_round else 1, expected_prompt=prompt)

    assert store.snapshot("web", "s1") == before
    assert store.raw_page("web", "s1") == raw_before


def test_retired_context_settings_are_rejected_without_rewriting_existing_user_keys(root):
    completed(root)
    service = WebRunService(root)
    original = (root / "users/alice/user_config.yaml").read_bytes()
    with pytest.raises(InvalidRequestError, match="已移除"):
        service.patch_user_config("alice", {"agents": {"max_rounds": 5}})
    assert (root / "users/alice/user_config.yaml").read_bytes() == original
