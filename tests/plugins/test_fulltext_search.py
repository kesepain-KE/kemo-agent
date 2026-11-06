"""Real simple/FTS5 integration against isolated history and memory databases."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import pytest

from plugins.history_search.tool import run as history_search
from plugins.memory_manage.memory_ops import edit_fragment, delete_fragment, search_by_content, search_many, search_by_title
from run.cards import CardStore
from run.history import delete_session
from run.infra.fulltext import NATIVE_ROOT, _library, load_simple
from run.memory import MemoryStore
from run.memory import store as memory_database
from tests.card_history_support import seed_history


@pytest.fixture
def root(tmp_path):
    for user in ("alice", "bob"):
        (tmp_path / "users" / user).mkdir(parents=True)
    return tmp_path


def history(root, query, **kwargs):
    return history_search(query, match_mode="fulltext", context={"root": str(root), "user": "alice"}, **kwargs)


def test_history_fulltext_uses_body_only_and_preserves_scope_then_erases_deleted_raw(root):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    with patch("run.cards.store.utc_now", return_value="2026-07-19T16:00:00Z"):
        user = store.append("web", "s1", "run", "user_text", {"text": "手动压缩完成，保存历史对话。"}, level="guidance")
        store.append("web", "s1", "run", "agent_text", {"text": "历史压缩方案", "reasoning": "秘密标记"})
        store.append("web", "s1", "run", "tool_call", {"name": "shell", "arguments": {"text": "秘密标记 历史 压缩"}}, tool_call_id="call-1")
        store.append("web", "s1", "run", "tool_result", {"text": "秘密标记 历史 压缩"}, tool_call_id="call-1")
    seed_history(root, "alice", "cli", "s1", [{"role": "user", "content": "历史压缩"}])
    seed_history(root, "bob", "web", "s1", [{"role": "user", "content": "秘密标记 历史压缩"}])
    before = store.raw_page("web", "s1")
    assert history(root, "秘密标记")["total_matches"] == 0
    result = history(root, "历史 压缩", source="web", session_id="s1", role="user",
                     since="2026-07-20", until="2026-07-20", context_messages=1, max_context_chars=50)
    assert result["engine"] == "sqlite-fts5/simple" and result["total_matches"] == 1
    assert result["matches"][0]["card_id"] == user.card_id
    assert all(item["raw_kind"] in {"user_text", "agent_text"} for item in result["matches"][0]["context"])
    assert "秘密标记" not in json.dumps(result, ensure_ascii=False)
    assert store.raw_page("web", "s1") == before
    delete_session(root, "alice", "web", "s1")
    assert history(root, "历史 压缩", source="web")["total_matches"] == 0
    assert store.raw_page("web", "s1") == ()


def test_history_fulltext_bm25_pagination_and_character_budget(root):
    texts = ["历史和其他事项 " * 30 + "压缩"] + [f"历史压缩方案 {n}" for n in range(10)]
    seed_history(root, "alice", "web", "s1", [{"role": "user", "content": text} for text in texts])
    first = history(root, "历史 压缩", limit=3)
    assert first["total_matches"] == 11 and first["has_more"]
    assert first["matches"][0]["seq"] != 1
    assert [hit["rank"] for hit in first["matches"]] == sorted(hit["rank"] for hit in first["matches"])
    seen, offset = set(), 0
    for _ in range(20):
        page = history(root, "历史 压缩", limit=10, offset=offset, max_snippet=80, page_char_limit=1400)
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= 1400
        ids = {hit["card_id"] for hit in page["matches"]}
        assert ids and not (ids & seen)
        seen |= ids
        if not page["has_more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert len(seen) == 11
    assert history(root, "历史 压缩", offset=50)["total_matches"] == 11
    assert history(root, "历史 压缩", offset=50)["matches"] == []


def test_memory_fulltext_batch_tiers_limits_and_no_weight_changes(root):
    store = MemoryStore(root, "alice", {})
    for tier, name in zip(("seven_days", "one_month", "half_year", "permanent"), ("甲", "乙", "丙", "丁")):
        store.create_fragment(tier, f"树莓派{name}.md", f"树莓派{name}使用 SQLite，负责历史和记忆检索。")
    MemoryStore(root, "bob", {}).create_fragment("seven_days", "private.md", "只有鲍勃的秘密标记")
    before = store.load_all()
    result = search_many(root, "alice", {}, "all", [{"title": "树莓派"}, {"content": "记忆 检索"}],
                         match_mode="fulltext", limit=2, include_content=True)
    assert result["engine"] == "sqlite-fts5/simple"
    for page in result["results"]:
        assert page["total_matches"] == 4 and len(page["matches"]) == 2 and page["truncated"]
        for hit in page["matches"]:
            assert hit["content"] and hit["memory_ref"] == f"{hit['tier']}:{hit['filename']}"
            assert hit["matched_terms"] and hit["match_score"] == -hit["rank"]
            assert "match_coverage" not in hit
    assert search_by_content(root, "alice", {}, "seven_days", "记忆 检索", match_mode="fulltext")["total_matches"] == 1
    assert search_by_content(root, "alice", {}, "all", "秘密标记", match_mode="fulltext")["total_matches"] == 0
    assert store.load_all() == before


def test_memory_index_tracks_rename_edit_tier_change_delete_and_rollback(root):
    store = MemoryStore(root, "alice", {})
    store.create_fragment("seven_days", "设备配置.md", "设备使用树莓派进行检索。")
    edit_fragment(root, "alice", {}, "seven_days", "设备配置.md", "设备使用工作站进行检索。", new_filename="检索方案.md")
    assert search_by_title(root, "alice", {}, "all", "检索 方案", match_mode="fulltext")["total_matches"] == 1
    assert search_by_content(root, "alice", {}, "all", "树莓派", match_mode="fulltext")["total_matches"] == 0
    with pytest.raises(RuntimeError, match="rollback"):
        with memory_database.connection(root, "alice", write=True) as db:
            db.execute("UPDATE memory_fragments SET content='失败的临时正文'")
            raise RuntimeError("rollback")
    assert search_by_content(root, "alice", {}, "all", "工作站 检索", match_mode="fulltext")["total_matches"] == 1
    with memory_database.connection(root, "alice", write=True) as db:
        db.execute("UPDATE memory_fragments SET tier='one_month'")
    assert search_by_content(root, "alice", {}, "seven_days", "工作站", match_mode="fulltext")["total_matches"] == 0
    assert search_by_content(root, "alice", {}, "one_month", "工作站", match_mode="fulltext")["total_matches"] == 1
    delete_fragment(root, "alice", {}, "one_month", "检索方案.md")
    assert search_by_content(root, "alice", {}, "all", "工作站", match_mode="fulltext")["total_matches"] == 0


@pytest.mark.parametrize("kind", ["cards", "memory"])
def test_existing_store_migration_backfills_index_and_keeps_pre_migration_backup(root, kind):
    if kind == "cards":
        seed_history(root, "alice", "web", "s1", [{"role": "user", "content": "保存历史压缩正文"}])
        path = root / "users/alice/history/cards.sqlite3"
        index, version, triggers = "card_text_fts", 4, ["card_text_fts_insert"]
    else:
        MemoryStore(root, "alice", {}).create_fragment("seven_days", "历史配置.md", "保存历史压缩正文")
        path = memory_database.database_path(root, "alice")
        index, version = "memory_text_fts", 3
        triggers = ["memory_text_fts_insert", "memory_text_fts_update", "memory_text_fts_delete"]
    with closing(sqlite3.connect(path)) as db, db:
        load_simple(db)
        for trigger in triggers:
            db.execute(f"DROP TRIGGER {trigger}")
        db.execute(f"DROP TABLE {index}")
        if kind == "cards":
            db.execute("DROP VIEW card_text_content")
            db.execute(f"PRAGMA user_version={version}")
            original = db.execute("SELECT card_id,raw_payload FROM context_cards").fetchall()
        else:
            db.execute("UPDATE memory_meta SET value=? WHERE key='schema_version'", (str(version),))
            original = db.execute("SELECT id,filename,content,weight FROM memory_fragments").fetchall()
    if kind == "cards":
        with CardStore(root, "alice").db():
            pass
        assert history(root, "历史 压缩")["total_matches"] == 1
    else:
        memory_database._READY_DATABASES.discard(str(path.resolve()).casefold())
        assert search_by_content(root, "alice", {}, "all", "历史 压缩", match_mode="fulltext")["total_matches"] == 1
    backups = list(path.parent.glob(f"{path.name}.bak.v{version}.*"))
    assert len(backups) == 1
    for candidate in (path, backups[0]):
        with closing(sqlite3.connect(candidate)) as db:
            table, columns = ("context_cards", "card_id,raw_payload") if kind == "cards" else ("memory_fragments", "id,filename,content,weight")
            assert db.execute(f"SELECT {columns} FROM {table}").fetchall() == original
    with closing(sqlite3.connect(backups[0])) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (index,)).fetchone() is None


def test_fulltext_native_query_boundaries_and_empty_history_have_no_side_effect(root):
    assert history(root, "历史 压缩")["total_matches"] == 0
    assert not (root / "users/alice/history/cards.sqlite3").exists()
    seed_history(root, "alice", "web", "s1", [{"role": "user", "content": "SQLite 记忆检索"}])
    assert history(root, '" OR * NOT')["total_matches"] == 0
    assert history(root, "SQLITE 检索")["total_matches"] == 1
    for query in ("x" * 1001, "query\0truncated"):
        with pytest.raises(ValueError, match="1000"):
            history(root, query)
    with pytest.raises(ValueError, match="大小写"):
        search_by_content(root, "alice", {}, "all", "SQLite", match_mode="fulltext", case_sensitive=True)
    with pytest.raises(ValueError, match="important"):
        search_by_content(root, "alice", {}, "important", "SQLite", match_mode="fulltext")


def test_fulltext_unicode_snippet_and_concurrent_readers(root):
    text = "🙂" + "前缀" * 100 + "树莓派负责历史检索" + "后缀" * 100
    seed_history(root, "alice", "web", "s1", [{"role": "user", "content": text}])
    MemoryStore(root, "alice", {}).create_fragment("seven_days", "设备方案.md", "树莓派负责历史检索")

    def read_pair(_):
        page = history(root, "树莓派 检索", max_snippet=40)
        memory = search_by_content(root, "alice", {}, "all", "树莓派 检索", match_mode="fulltext")
        return page, memory

    with ThreadPoolExecutor(max_workers=4) as pool:
        for page, memory in pool.map(read_pair, range(12)):
            assert page["total_matches"] == memory["total_matches"] == 1
            assert "树莓派" in page["matches"][0]["snippet"]
            assert len(page["matches"][0]["snippet"]) <= 40


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native path encoding boundary")
def test_windows_unicode_dependency_path_does_not_abort_native_process(root):
    target = root / "中文路径🙂"
    library = Path(str(_library()._name))
    (target / library.parent.name).mkdir(parents=True)
    shutil.copy2(library, target / library.parent.name / library.name)
    shutil.copytree(NATIVE_ROOT / "dict", target / "dict")
    source = """
import sqlite3, sys
from pathlib import Path
from run.infra import fulltext
fulltext.NATIVE_ROOT = Path(sys.argv[1])
db = sqlite3.connect(':memory:')
fulltext.load_simple(db)
assert fulltext.compile_fulltext(db, '历史 压缩') == '"历史" AND "压缩"'
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", source, str(target)], cwd=NATIVE_ROOT.parents[2],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    # On filesystems without usable short names, an explicit Python error is
    # acceptable; a cppjieba FATAL/Windows fast-fail exit is never acceptable.
    assert result.returncode == 0 or (
        result.returncode == 1 and "RuntimeError: simple 词典路径无法表示" in result.stderr
    ), result.stderr
