"""History search exposes RAW identities, bounded pages and full original text."""
import json
import tracemalloc
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from plugins.history_search.tool import run
from plugins.manifest import discover_plugin_manifests
from run.cards import CardStore
from run.history import close_session, delete_session
from run.history.archive_query_common import decode_token, encode_token
from run.tools import ToolValidationError, validate_arguments
from tests.card_history_support import seed_history

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def root(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    (tmp_path / "users/bob").mkdir()
    return tmp_path


@pytest.fixture
def history_with_tools(root):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    bodies = []
    for kind, level, text in [
        ("user_text", "initial", "用户正文"),
        ("agent_text", "interim", "前缀" * 100 + "树莓派" + "后缀" * 100),
        ("user_text", "guidance", "needle 引导正文"),
        ("agent_text", "final", "needle 智能体正文"),
    ]:
        bodies.append(store.append("web", "s1", "mixed", kind, {
            "text": text, "metadata": {"internal": "metadata-only-marker"},
            "reasoning": "reasoning-only-marker",
        }, level=level))
        call_id = f"call-{len(bodies)}"
        call = {"name": "history_search", "arguments": {"query": "tool-only-marker needle"}}
        store.append("web", "s1", "mixed", "tool_call", call, tool_call_id=call_id)
        store.append("web", "s1", "mixed", "tool_result", {
            "original_call": call,
            "result": {"content": "tool-only-marker needle " * 200},
            "execution": {"status": "completed"},
        }, tool_call_id=call_id)
    return store, bodies


def seed(root, sid="s1", texts=("needle",), *, source="web", stamp="2026-07-20T00:00:00+00:00"):
    with patch("run.cards.store.utc_now", return_value=stamp):
        return seed_history(root, "alice", source, sid, [
            {"role": "user" if n % 2 == 0 else "assistant", "content": text}
            for n, text in enumerate(texts)])


def search(root, query="", **kwargs):
    return run(query, context={"root": str(root), "user": "alice"}, **kwargs)


def test_raw_time_role_and_source_filters_return_stable_card_metadata(root):
    seed(root, "old", stamp="2026-07-19T00:00:00Z")
    seed(root, "selected", texts=("needle user", "needle assistant"))
    seed(root, "other-source", source="cli")
    result = search(root, "needle", since="2026-07-20", until="2026-07-20", role="user", source="web")
    assert result["total_matches"] == 1
    match = result["matches"][0]
    assert match["session_id"] == "selected" and match["seq"] == 1
    assert match["card_id"] and match["integration_id"] and match["raw_kind"] == "user_text"
    assert "window" not in match and "match_index" not in match


def test_shanghai_date_boundary_and_newest_first(root):
    seed(root, "before", stamp="2026-07-19T15:59:59Z")
    seed(root, "midnight", stamp="2026-07-19T16:00:00Z")
    seed(root, "latest", stamp="2026-07-20T01:00:00Z")
    assert [m["session_id"] for m in search(root, "needle")["matches"]] == ["latest", "midnight", "before"]
    assert search(root, "needle", since="2026-07-20", until="2026-07-20")["total_matches"] == 2


def test_word_exact_substring_and_regex_modes(root):
    seed(root, texts=("main", "email", "AI is great", " AI ", "设备 IP 192.168.10.110"))
    assert search(root, "AI")["total_matches"] == 4
    assert {m["seq"] for m in search(root, "AI", match_mode="word")["matches"]} == {3, 4}
    assert search(root, "AI", match_mode="exact")["matches"][0]["seq"] == 4
    assert search(root, r"IP.*\d+\.\d+\.\d+\.\d+", regex=True)["total_matches"] == 1


def test_search_and_surrounding_context_only_include_bounded_conversation_text(root, history_with_tools):
    store, bodies = history_with_tools
    before = store.raw_page("web", "s1")
    for query in ("tool-only-marker", "metadata-only-marker", "reasoning-only-marker"):
        assert search(root, query)["total_matches"] == 0
    assert search(root, r".*", regex=True)["total_matches"] == len(bodies)
    assert search(root, "needle")["total_matches"] == 2
    assert search(root, "needle", role="user")["matches"][0]["seq"] == bodies[2].seq
    assert search(root, "needle", role="assistant")["matches"][0]["seq"] == bodies[3].seq
    match = search(root, "树莓派", role="assistant", max_snippet=40,
                   context_messages=2, max_context_chars=50)["matches"][0]
    assert len(match["snippet"]) <= 40 and "树莓派" in match["snippet"]
    assert [item["seq"] for item in match["context"]] == [card.seq for card in bodies]
    assert [item["content"] for item in match["context"]] == [
        card.raw_payload["text"][:50] for card in bodies]
    assert store.raw_page("web", "s1") == before


@pytest.mark.parametrize("role", ["any", "user", "assistant"])
def test_read_pages_count_only_conversation_text_and_skip_trailing_tools(root, history_with_tools, role):
    store, bodies = history_with_tools
    expected = [card for card in bodies if role == "any"
                or card.raw_kind == ("user_text" if role == "user" else "agent_text")]
    before = store.raw_page("web", "s1")
    archive_id = search(root, action="list")["archives"][0]["archive_id"]
    cursor = ""
    for index, card in enumerate(expected):
        page = search(root, action="read", archive_id=archive_id, role=role, limit=1, cursor=cursor)
        assert page["total_messages"] == len(expected)
        assert [(item["seq"], item["raw_kind"], item["raw_level"], item["content"])
                for item in page["messages"]] == [
                    (card.seq, card.raw_kind, card.raw_level, card.raw_payload["text"])]
        assert page["has_more"] is (index + 1 < len(expected))
        cursor = page["next_cursor"]
    assert cursor is None
    assert store.raw_page("web", "s1") == before


def test_read_preserves_empty_text_card_and_cursor(root):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    store.append("web", "s1", "mixed", "user_text", {"text": "start"}, level="initial")
    empty = store.append("web", "s1", "mixed", "agent_text", {"text": ""}, level="interim")
    tail = store.append("web", "s1", "mixed", "agent_text", {"text": "后续正文🙂"}, level="final")
    archive_id = search(root, action="list")["archives"][0]["archive_id"]

    first = search(root, action="read", archive_id=archive_id, limit=2)
    assert first["total_messages"] == 3 and first["has_more"]
    assert [(item["seq"], item["content"], item["partial"]) for item in first["messages"]] == [
        (1, "start", False), (empty.seq, "", False),
    ]
    second = search(root, action="read", archive_id=archive_id, cursor=first["next_cursor"])
    assert [(item["seq"], item["content"]) for item in second["messages"]] == [(tail.seq, "后续正文🙂")]
    assert not second["has_more"] and second["next_cursor"] is None


def test_limit_counts_all_matches_and_character_budget_advances_offset(root):
    seed(root, texts=tuple(f"paged hit {n} " + "x" * 300 for n in range(10)))
    result = search(root, "hit", limit=3)
    assert result["total_matches"] == 10 and len(result["matches"]) == 3 and result["has_more"]
    seen, offset = set(), 0
    while True:
        page = search(root, "hit", limit=10, offset=offset, max_snippet=320, page_char_limit=1000)
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= 1000
        ids = {m["card_id"] for m in page["matches"]}
        assert ids and not ids & seen
        seen |= ids
        if not page["has_more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert len(seen) == 10


def test_deleted_session_is_not_searchable_and_raw_is_erased(root):
    history = seed(root)
    before = history.cards
    assert search(root, "needle")["total_matches"] == 1
    assert history.cards == before
    delete_session(root, "alice", "web", "s1")
    assert search(root, "needle")["total_matches"] == 0
    assert history.cards == []


def test_directory_dates_and_utf8_read_are_lossless(root):
    original = "开头🙂" + "多模态历史正文" * 900 + "结尾"
    seed(root, "long", texts=(original, "saved reply"), stamp="2026-10-23T16:00:00Z")
    seed(root, "second", texts=("separate session",), stamp="2026-10-24T10:00:00+08:00")
    dates = search(root, action="dates")
    assert dates["dates"] == [{"date": "2026-10-24", "archive_count": 2}]
    listed = search(root, action="list", session_id="long")
    archive_id = listed["archives"][0]["archive_id"]
    recovered, cursor, pages = "", "", 0
    while True:
        page = search(root, action="read", archive_id=archive_id, cursor=cursor, page_char_limit=2200)
        for item in page["messages"]:
            if item["seq"] == 1:
                recovered += item["content"]
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) <= 2200
        pages += 1
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert recovered == original and pages > 2


def test_archive_id_and_cursor_are_scoped_to_user_and_session(root):
    seed(root, "long", texts=("x" * 3000,))
    seed(root, "other")
    archives = search(root, action="list")["archives"]
    ids = {a["session_id"]: a["archive_id"] for a in archives}
    page = search(root, action="read", archive_id=ids["long"], page_char_limit=2200)
    with pytest.raises(ValueError):
        search(root, action="read", archive_id=ids["other"], cursor=page["next_cursor"])
    with pytest.raises(ValueError):
        run(action="read", archive_id=ids["long"], context={"root": str(root), "user": "bob"})
    delete_session(root, "alice", "web", "long")
    with pytest.raises(ValueError):
        search(root, action="read", archive_id=ids["long"])


def test_reads_do_not_create_database_or_touch_old_windows(root):
    legacy = root / "users/alice/history/history.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"old unrelated data")
    assert search(root, action="dates")["dates"] == []
    assert not (root / "users/alice/history/cards.sqlite3").exists()
    assert legacy.read_bytes() == b"old unrelated data"


@pytest.mark.parametrize("kwargs", [
    {"since": "2026-02-30"}, {"since": "2026-07-21", "until": "2026-07-20"},
    {"regex": True, "query": "["}, {"role": "tool"}, {"match_mode": "fuzzy"},
    {"action": "read"}, {"action": "dates", "query": "mixed"},
])
def test_validation_rejects_invalid_and_mixed_arguments(root, kwargs):
    query = kwargs.pop("query", "x" if kwargs.get("action", "search") == "search" else "")
    with pytest.raises(ValueError):
        search(root, query, **kwargs)


def test_manifest_preserves_discoverable_search_and_archive_actions():
    manifest = next(m for m in discover_plugin_manifests(PROJECT_ROOT) if m.tool["name"] == "history_search")
    assert manifest.tool["version"] == "1.3.0"
    assert manifest.tool["input_schema"]["required"] == []
    assert set(manifest.tool["input_schema"]["properties"]["action"]["enum"]) == {"search", "dates", "list", "read"}


def test_search_hit_reads_five_text_cards_per_side_past_tool_gaps(root):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    bodies = []
    for i in range(13):
        if i in (6, 7):
            for j in range(3):
                call_id = f"gap-{i}-{j}"
                store.append("web", "s1", "mixed", "tool_call", {"name": "example"}, tool_call_id=call_id)
                store.append("web", "s1", "mixed", "tool_result", {"text": "private tool data"}, tool_call_id=call_id)
        text = "定位正文" if i == 6 else f"相邻正文 {i}"
        bodies.append(store.append("web", "s1", "mixed", "user_text", {"text": text}))

    hit = search(root, "定位正文", match_mode="fulltext")["matches"][0]
    result = search(root, action="read", anchor_card_id=hit["card_id"], role="assistant", limit=1)
    assert result["mode"] == "anchor"
    assert result["anchor"] == {"card_id": bodies[6].card_id, "seq": bodies[6].seq, "index": 5}
    assert [item["seq"] for item in result["messages"]] == [card.seq for card in bodies[1:12]]
    assert [item["content"] for item in result["messages"]] == [card.raw_payload["text"] for card in bodies[1:12]]
    assert result["archive"]["archive_id"]
    assert "next_cursor" not in result
    assert not result["window_clipped"] and not result["page_limited_by_chars"]


@pytest.mark.parametrize(("position", "before", "after", "start", "stop"), [
    (0, 5, 2, 0, 3),
    (3, 2, 5, 1, 4),
    (1, 0, 2, 1, 4),
    (2, 2, 0, 0, 3),
    (1, 0, 0, 1, 2),
])
def test_anchor_window_is_asymmetric_and_natural_ends_are_not_clipping(
    root, history_with_tools, position, before, after, start, stop,
):
    _, bodies = history_with_tools
    result = search(root, action="read", anchor_card_id=bodies[position].card_id, before=before, after=after)
    assert [item["card_id"] for item in result["messages"]] == [card.card_id for card in bodies[start:stop]]
    assert result["anchor"]["index"] == position - start
    assert not result["window_clipped"] and not result["page_limited_by_chars"]


def test_anchor_resolves_exact_user_source_and_archive_generation(root):
    web = seed(root, texts=("Alice web",)).cards[0]
    cli = seed(root, texts=("Alice cli",), source="cli").cards[0]
    bob = seed_history(root, "bob", "web", "s1", [{"role": "user", "content": "Bob web"}]).cards[0]
    assert web.seq == cli.seq == bob.seq == 1

    args = {"action": "read", "anchor_card_id": web.card_id}
    page = search(root, **args)
    other = search(root, action="read", anchor_card_id=cli.card_id)
    archive_id = page["archive"]["archive_id"]
    assert page["archive"]["session_id"] == other["archive"]["session_id"] == "s1"
    assert (page["archive"]["source"], other["archive"]["source"]) == ("web", "cli")
    assert [item["content"] for item in page["messages"]] == ["Alice web"]
    assert search(root, **args, archive_id=archive_id) == page

    parts = decode_token(archive_id, "card-archive-v1")
    stale_id = encode_token([*parts[:-1], "stale-generation"])
    for wrong_id in (other["archive"]["archive_id"], stale_id):
        with pytest.raises(ValueError, match="同一归档代际"):
            search(root, **args, archive_id=wrong_id)

    bob_context = {"root": str(root), "user": "bob"}
    with pytest.raises(ValueError, match="不存在或不可见"):
        run(**args, context=bob_context)
    with pytest.raises(ValueError, match="当前用户"):
        run(action="read", anchor_card_id=bob.card_id, archive_id=archive_id, context=bob_context)

    delete_session(root, "alice", "web", "s1")
    with pytest.raises(ValueError, match="不存在或不可见"):
        search(root, **args, archive_id=archive_id)


def test_anchor_reads_closed_archive_and_empty_text_without_reopening(root):
    history = seed(root, texts=("before", ""))
    anchor = history.cards[-1]
    close_session(root, "alice", "web", "s1")
    closed = history.store.session("web", "s1")
    assert closed["lifecycle"] == "closed"
    result = search(root, action="read", anchor_card_id=anchor.card_id)
    message = result["messages"][result["anchor"]["index"]]
    assert (message["card_id"], message["content"], message["partial"]) == (anchor.card_id, "", False)
    assert history.store.session("web", "s1") == closed


def test_invalid_and_tool_anchors_never_fall_back_to_archive_paging(root, history_with_tools):
    store, _ = history_with_tools
    archive_id = search(root, action="list")["archives"][0]["archive_id"]
    tool_ids = [card.card_id for card in store.raw_page("web", "s1") if card.raw_kind.startswith("tool_")]
    assert tool_ids
    for card_id in [*tool_ids, "card_missing"]:
        with pytest.raises(ValueError, match="不存在或不可见"):
            search(root, action="read", archive_id=archive_id, anchor_card_id=card_id)


@pytest.mark.parametrize(("extra", "field"), [
    ({"before": -1}, "before"),
    ({"after": 21}, "after"),
    ({"before": 10**100}, "before"),
    ({"before": True}, "before"),
    ({"after": False}, "after"),
    ({"cursor": "cursor"}, "cursor 不能同时使用"),
])
def test_read_anchor_rejects_invalid_window_and_cursor(root, history_with_tools, extra, field):
    _, bodies = history_with_tools
    with pytest.raises(ValueError, match=field):
        search(root, action="read", anchor_card_id=bodies[0].card_id, **extra)


@pytest.mark.parametrize("args", [
    {"action": "search", "anchor_card_id": "card_other"},
    {"action": "list", "anchor_card_id": "card_other"},
    {"action": "read", "before": 0},
])
def test_anchor_parameters_require_anchored_read(root, args):
    with pytest.raises(ValueError, match="仅适用于带 anchor_card_id"):
        search(root, **args)


@pytest.mark.parametrize("long_anchor", [False, True])
def test_anchor_gets_budget_before_neighbors_after_utf8_and_json_escaping(root, long_anchor):
    unit = '中文🙂"\\\n'
    text = unit * (1000 if long_anchor else 1)
    neighbor = unit * 1000
    anchor = seed(root, texts=(neighbor, text, neighbor)).cards[1]
    page = search(root, action="read", anchor_card_id=anchor.card_id, page_char_limit=1000)

    assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 1000
    assert page["window_clipped"] and page["page_limited_by_chars"]
    entry = page["messages"][page["anchor"]["index"]]
    assert entry["card_id"] == anchor.card_id and entry["partial"] is long_anchor
    assert unit in entry["content"] and text.startswith(entry["content"])
    if long_anchor:
        assert page["messages"] == [entry]
        alone = search(root, action="read", anchor_card_id=anchor.card_id, before=0, after=0, page_char_limit=1000)
        assert alone["page_limited_by_chars"] and not alone["window_clipped"]
    else:
        assert entry["content"] == text


def test_anchor_fails_when_budget_cannot_include_anchor(root):
    history = seed_history(root, "alice", "web", "s1", [{"role": "user", "content": "正文"}],
                           metadata={"title": "标题" * 100, "summary": "摘要" * 250})
    with pytest.raises(ValueError, match="page_char_limit 太小，无法容纳锚点正文"):
        search(root, action="read", anchor_card_id=history.cards[0].card_id, page_char_limit=1000)


def test_anchor_read_does_not_create_missing_history_store(root):
    path = CardStore(root, "alice").path
    with pytest.raises(ValueError, match="不存在或不可见"):
        search(root, action="read", anchor_card_id="card_missing")
    assert not path.exists()


def test_manifest_and_tool_validator_accept_text_and_tool_anchor_parameters():
    manifest = next(m for m in discover_plugin_manifests(PROJECT_ROOT) if m.tool["name"] == "history_search")
    schema = manifest.tool["input_schema"]
    args = {"action": "read", "anchor_card_id": "card_example", "before": 0, "after": 20}
    validate_arguments(schema, args)
    for name in ("before", "after"):
        assert schema["properties"][name]["default"] == 5
    assert schema["properties"]["include_tools"]["default"] is False
    assert schema["properties"]["max_tool_chars"]["default"] == 2000
    validator = Draft202012Validator(schema)
    for cap in (0, 200, 20_000):
        options = {**args, "include_tools": True, "max_tool_chars": cap}
        validate_arguments(schema, options)
        assert validator.is_valid(options)
    for cap in (1, 199, 20_001):
        assert not validator.is_valid({**args, "max_tool_chars": cap})
    for extra in ({"after": 21}, {"before": True}, {"anchor_seq": 1}, {"include_tools": 1}):
        with pytest.raises(ToolValidationError):
            validate_arguments(schema, {**args, **extra})


def _result_card(store, value, *, call_id="call-1", status="completed", live=False):
    return store.append("web", "s1", "mixed", "tool_result", {
        "original_call": {"name": "file"},
        "result": value,
        "execution": {"status": status, "execution_live": live},
    }, tool_call_id=call_id)


def test_tool_preview_interleaves_only_the_original_text_window(root):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    lead = _result_card(store, {"ok": True, "result": "outside left"}, call_id="outside-left")
    before = store.append("web", "s1", "mixed", "user_text", {"text": "before"})
    args = {"query": "中文🙂" * 90}
    call = store.append("web", "s1", "mixed", "tool_call", {"name": "file", "arguments": args}, tool_call_id="call-1")
    running = _result_card(store, {"accepted": True, "ok": True}, status="running", live=True)
    anchor = store.append("web", "s1", "mixed", "agent_text", {"text": "anchor"})
    value = {"items": ["正文" * 150]}
    done = _result_card(store, {"ok": False, "result": value})
    missing_ok = _result_card(store, {"result": "plain text"}, call_id="call-2")
    non_boolean = _result_card(store, {"ok": 1, "result": None}, call_id="call-3")
    after = store.append("web", "s1", "mixed", "agent_text", {"text": "after"})
    tail = _result_card(store, {"ok": True, "result": "outside right"}, call_id="outside-right")

    opts = {"action": "read", "anchor_card_id": anchor.card_id}
    plain = search(root, **opts)
    assert search(root, **opts, include_tools=False, max_tool_chars=0) == plain
    page = run(**opts, include_tools=True, max_tool_chars=200,
               context={"root": str(root), "user": "alice", "source": "message"})
    messages = page["messages"]
    assert [row["card_id"] for row in messages] == [
        card.card_id for card in (before, call, running, anchor, done, missing_ok, non_boolean, after)]
    assert [row for row in messages if "kind" not in row] == plain["messages"]
    assert page["anchor"]["index"] == 3
    assert not page["window_clipped"] and not page["page_limited_by_chars"]

    assert messages[1] == {
        "card_id": call.card_id, "seq": call.seq, "kind": "tool_call", "tool_name": "file", "tool_call_id": "call-1",
        "arguments_preview": json.dumps(args, ensure_ascii=False, separators=(",", ":"))[:200],
        "arguments_truncated": True,
    }
    notice = messages[2]
    assert notice["ok"] is True and notice["execution_live"] is True and notice["execution_status"] == "running"
    assert json.loads(notice["result_preview"]) == {"accepted": True, "ok": True}
    assert not notice["result_truncated"]
    final = messages[4]
    assert final == {
        "card_id": done.card_id, "seq": done.seq, "kind": "tool_result", "tool_name": "file", "tool_call_id": "call-1",
        "ok": False, "execution_status": "completed", "execution_live": False,
        "result_preview": json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:200],
        "result_truncated": True, "raw_card_bytes": len(done.raw_payload_json.encode("utf-8")),
    }
    assert messages[5]["ok"] is None and messages[5]["result_preview"] == "plain text"
    assert messages[6]["ok"] is None and messages[6]["result_preview"] == "null"

    one_side = search(root, **opts, include_tools=True, before=0, after=1)
    assert [row["seq"] for row in one_side["messages"]] == [anchor.seq, done.seq, missing_ok.seq,
                                                            non_boolean.seq, after.seq]
    assert lead.seq < before.seq and after.seq < tail.seq
    alone = search(root, **opts, include_tools=True, before=0, after=0)
    assert [row["seq"] for row in alone["messages"]] == [anchor.seq]


def test_tool_preview_allocates_page_budget_after_anchor_before_neighbors(root):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    store.append("web", "s1", "mixed", "user_text", {"text": "x" * 3000})
    call = store.append("web", "s1", "mixed", "tool_call", {"name": "file", "arguments": {}}, tool_call_id="call-1")
    _result_card(store, {"ok": True, "result": "y" * 2000})
    anchor = store.append("web", "s1", "mixed", "agent_text", {"text": "anchor takes priority"})
    page = search(root, action="read", anchor_card_id=anchor.card_id, include_tools=True, page_char_limit=1200)
    assert [row["seq"] for row in page["messages"]] == [call.seq, anchor.seq]
    assert page["messages"][page["anchor"]["index"]]["content"] == "anchor takes priority"
    assert page["window_clipped"] and page["page_limited_by_chars"]
    assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 1200


def test_full_tool_probe_preserves_complete_arguments_and_result(root):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    anchor = store.append("web", "s1", "mixed", "user_text", {"text": "anchor"})
    args, text = {"query": "input" * 600}, '中文🙂"\n' * 650
    store.append("web", "s1", "mixed", "tool_call", {"name": "file", "arguments": args}, tool_call_id="call-1")
    _result_card(store, {"ok": True, "result": text})
    store.append("web", "s1", "mixed", "agent_text", {"text": "after"})

    page = search(root, action="read", anchor_card_id=anchor.card_id, include_tools=True, max_tool_chars=0)
    call, result = page["messages"][1:3]
    assert json.loads(call["arguments_preview"]) == args and not call["arguments_truncated"]
    assert result["result_preview"] == text and not result["result_truncated"]
    assert not page["window_clipped"]
    assert not (root / "users/alice/download/tool_results").exists()


@pytest.mark.parametrize(("values", "error"), [
    (['"\n' * 14_000] * 2, "超过十万字符上限"),
    (["中" * 500], "超过 page_char_limit"),
])
def test_full_tool_probe_errors_on_serialized_total_or_page_budget(root, values, error):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    anchor = store.append("web", "s1", "mixed", "user_text", {"text": "anchor"})
    for i, value in enumerate(values):
        _result_card(store, {"ok": True, "result": value}, call_id=f"call-{i}")
    store.append("web", "s1", "mixed", "agent_text", {"text": "after"})
    with pytest.raises(ValueError, match=error):
        search(root, action="read", anchor_card_id=anchor.card_id, include_tools=True,
               max_tool_chars=0, page_char_limit=1000)
    assert not (root / "users/alice/download/tool_results").exists()


def test_30mb_tool_preview_keeps_raw_payload_out_of_python_and_never_writes_artifact(root):
    store = CardStore(root, "alice")
    state = store.ensure_session("web", "s1")
    anchor = store.append("web", "s1", "mixed", "user_text", {"text": "anchor"})
    # Seed RAW directly: the normal execution writer would create a VIEW artifact.
    prefix = ('{"execution":{"status":"completed","execution_live":false},'
              '"original_call":{"name":"file"},"result":{"ok":true,"result":{"body":"')
    suffix = '"}}}'
    size = 31_866_927
    with store.db(write=True) as db:
        db.execute("INSERT INTO context_cards(card_id,session_key,integration_id,seq,raw_kind,raw_payload,"
                   "raw_tokens,tool_call_id,meter_key,created_at) VALUES "
                   "('synthetic-large',?,'mixed',2,'tool_result',?||printf('%.*c',?,'x')||?,0,'large',?,?)",
                   (anchor.session_key, prefix, size - len(prefix + suffix), suffix,
                    state["meter_key"], anchor.created_at))
        db.execute("UPDATE card_sessions SET last_seq=2 WHERE session_key=?", (anchor.session_key,))
    store.append("web", "s1", "mixed", "agent_text", {"text": "after"})
    opts = {"action": "read", "anchor_card_id": anchor.card_id, "include_tools": True}

    for cap in (2000, 0):
        tracemalloc.start()
        start = perf_counter()
        try:
            if cap:
                page = search(root, **opts)
            else:
                with pytest.raises(ValueError, match="超过十万字符上限"):
                    search(root, **opts, max_tool_chars=0)
            elapsed, peak = perf_counter() - start, tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        print(f"synthetic RAW {size} B, max_tool_chars={cap}: {elapsed:.3f} s; Python peak {peak:,} B")
        assert peak < 8 * 1024 * 1024  # full RAW returned by sqlite3 alone would allocate ~30 MiB
        assert not (root / "users/alice/download/tool_results").exists()

    tool = page["messages"][1]
    assert (tool["raw_card_bytes"], len(tool["result_preview"]), tool["result_truncated"]) == (size, 2000, True)
    assert tool["result_preview"].startswith('{"body":"xxx')


def test_tool_options_require_anchored_read_including_explicit_defaults(root, history_with_tools):
    archive_id = search(root, action="list")["archives"][0]["archive_id"]
    first = search(root, action="read", archive_id=archive_id, limit=1)
    assert first["next_cursor"]
    for opts in ({"include_tools": False}, {"include_tools": True}, {"max_tool_chars": 2000}, {"max_tool_chars": 0}):
        with pytest.raises(ValueError, match="仅适用于带 anchor_card_id 的 read"):
            search(root, action="read", archive_id=archive_id, cursor=first["next_cursor"], **opts)
        with pytest.raises(ValueError, match="仅适用于带 anchor_card_id 的 read"):
            search(root, query="needle", **opts)
    second = search(root, action="read", archive_id=archive_id, cursor=first["next_cursor"])
    assert second["messages"][0]["seq"] > first["messages"][0]["seq"]


@pytest.mark.parametrize(("opts", "field"), [
    ({"include_tools": 1}, "include_tools"),
    ({"include_tools": None}, "include_tools"),
    ({"max_tool_chars": True}, "max_tool_chars"),
    ({"max_tool_chars": -1}, "max_tool_chars"),
    ({"max_tool_chars": 1}, "max_tool_chars"),
    ({"max_tool_chars": 199}, "max_tool_chars"),
    ({"max_tool_chars": 20_001}, "max_tool_chars"),
])
def test_anchored_tool_options_reject_invalid_types_and_limits(root, history_with_tools, opts, field):
    _, bodies = history_with_tools
    with pytest.raises(ValueError, match=field):
        search(root, action="read", anchor_card_id=bodies[0].card_id, **opts)
