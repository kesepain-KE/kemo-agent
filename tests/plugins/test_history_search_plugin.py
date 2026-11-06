"""History search exposes RAW identities, bounded pages and full original text."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from plugins.history_search.tool import run
from plugins.manifest import discover_plugin_manifests
from run.cards import CardStore
from run.history import delete_session
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


def test_hidden_session_is_not_searchable_but_raw_remains(root):
    history = seed(root)
    before = history.cards
    assert search(root, "needle")["total_matches"] == 1
    delete_session(root, "alice", "web", "s1")
    assert search(root, "needle")["total_matches"] == 0 and history.cards == before


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
    assert not (root / "users/alice/cards.sqlite3").exists()
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
