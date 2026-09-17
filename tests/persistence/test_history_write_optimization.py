from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from run.context import read_summary_cache
from run.history import (
    _trim_to_max_rounds,
    clear_session,
    commit_terminal_windows,
    delete_session,
    empty_window,
    find_window,
    load_runtime_window,
    load_window,
    patch_archive_metadata,
    runtime_window_path,
    update_run_state,
)
from run.history import (
    _configure,
    _ensure_schema,
    database_path,
    get_active,
    reserve_session,
    read_registry_record,
    save_window,
)
import run.history.runtime_cache as runtime_cache


def test_late_terminal_commit_does_not_overwrite_newer_active_session(tmp_path: Path) -> None:
    (tmp_path / "users" / "alice").mkdir(parents=True)
    reserve_session(
        tmp_path,
        "alice",
        "web",
        "session-a",
        active_key="interactive:alice",
    )
    reserve_session(
        tmp_path,
        "alice",
        "web",
        "session-b",
        active_key="interactive:alice",
    )
    archive = _archive(tmp_path, session_id="session-a")
    archive_window = empty_window("alice", "web", "session-a")
    _append_round(archive_window, 1)
    commit_terminal_windows(
        archive,
        archive_window,
        runtime_window_path(archive),
        copy.deepcopy(archive_window),
        active_key="interactive:alice",
    )
    assert get_active(tmp_path, "alice", "interactive:alice")["session_id"] == "session-b"


def test_late_terminal_commit_after_delete_cannot_recreate_session(tmp_path: Path) -> None:
    archive = _archive(tmp_path, session_id="session-deleted")
    archive_window = empty_window("alice", "web", "session-deleted")
    _append_round(archive_window, 1)
    commit_terminal_windows(
        archive,
        archive_window,
        runtime_window_path(archive),
        copy.deepcopy(archive_window),
        active_key="interactive:alice",
    )
    reserve_session(
        tmp_path,
        "alice",
        "web",
        "session-deleted",
        active_key="interactive:alice",
    )

    delete_session(tmp_path, "alice", "web", "session-deleted")

    commit_terminal_windows(
        archive,
        archive_window,
        runtime_window_path(archive),
        copy.deepcopy(archive_window),
        active_key="interactive:alice",
    )

    assert read_registry_record(tmp_path, "alice", "web", "session-deleted") is None
    assert get_active(tmp_path, "alice", "interactive:alice") is None
    assert find_window(tmp_path, "alice", "web", "session-deleted") is None


def _archive(root: Path, user: str = "alice", session_id: str = "conv_write") -> Path:
    (root / "users" / user).mkdir(parents=True, exist_ok=True)
    return root / "users" / user / "history" / session_id


def _append_round(window: dict, number: int) -> None:
    window["text"]["messages"].extend(
        [
            {"role": "user", "content": f"question-{number}"},
            {"role": "assistant", "content": f"answer-{number}"},
        ]
    )
    window["think"]["rounds"].append({"round": number, "content": f"think-{number}"})
    window["tool"]["rounds"].append({"round": number, "calls": []})
    window["items"]["items"].extend(
        [
            {"id": f"u-{number}", "type": "message", "role": "user", "metadata": {"round": number}},
            {"id": f"a-{number}", "type": "message", "role": "assistant", "metadata": {"round": number}},
        ]
    )
    window["data"]["round_metrics"].append(
        {"round": number, "elapsed_ms": number * 10}
    )
    window["data"]["rounds"] = number


def test_archive_append_inserts_only_new_message_suffix(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    window = empty_window("alice", "web", "conv_write")
    _append_round(window, 1)
    save_window(archive, window)

    db_path = database_path(tmp_path, "alice")
    with sqlite3.connect(db_path) as database:
        database.executescript(
            """
            CREATE TABLE write_audit(kind TEXT NOT NULL);
            CREATE TRIGGER audit_message_insert AFTER INSERT ON history_messages
            BEGIN INSERT INTO write_audit(kind) VALUES('insert'); END;
            CREATE TRIGGER audit_message_delete AFTER DELETE ON history_messages
            BEGIN INSERT INTO write_audit(kind) VALUES('delete'); END;
            CREATE TRIGGER audit_round_insert AFTER INSERT ON history_rounds
            BEGIN INSERT INTO write_audit(kind) VALUES('round_insert'); END;
            CREATE TRIGGER audit_round_delete AFTER DELETE ON history_rounds
            BEGIN INSERT INTO write_audit(kind) VALUES('round_delete'); END;
            """
        )
    _append_round(window, 2)
    save_window(archive, window)

    with sqlite3.connect(db_path) as database:
        audit = database.execute(
            "SELECT kind, COUNT(*) FROM write_audit GROUP BY kind"
        ).fetchall()
        compact_text = json.loads(
            database.execute(
                "SELECT text_json FROM history_windows "
                "WHERE window_kind='archive' AND window_name='conv_write'"
            ).fetchone()[0]
        )
    assert dict(audit) == {"insert": 2, "round_insert": 1}
    assert compact_text == {"schema_version": 1, "storage": "history_messages"}
    assert load_window(archive)["text"]["messages"] == window["text"]["messages"]


def test_archive_edit_uses_explicit_rebuild_fallback(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    window = empty_window("alice", "web", "conv_write")
    _append_round(window, 1)
    _append_round(window, 2)
    save_window(archive, window)
    db_path = database_path(tmp_path, "alice")
    with sqlite3.connect(db_path) as database:
        database.executescript(
            """
            CREATE TABLE write_audit(kind TEXT NOT NULL);
            CREATE TRIGGER audit_message_insert AFTER INSERT ON history_messages
            BEGIN INSERT INTO write_audit(kind) VALUES('insert'); END;
            CREATE TRIGGER audit_message_delete AFTER DELETE ON history_messages
            BEGIN INSERT INTO write_audit(kind) VALUES('delete'); END;
            """
        )
    window["text"]["messages"][0]["content"] = "edited"
    save_window(archive, window)
    with sqlite3.connect(db_path) as database:
        audit = dict(
            database.execute(
                "SELECT kind, COUNT(*) FROM write_audit GROUP BY kind"
            ).fetchall()
        )
    assert audit == {"delete": 4, "insert": 4}
    assert load_window(archive)["text"]["messages"][0]["content"] == "edited"


def test_terminal_commit_does_not_write_runtime_window(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    runtime = runtime_window_path(archive)
    # Initialize the database before installing the failure trigger.
    seed = empty_window("alice", "web", "conv_seed")
    save_window(_archive(tmp_path, session_id="conv_seed"), seed)
    db_path = database_path(tmp_path, "alice")
    with sqlite3.connect(db_path) as database:
        database.execute(
            """
            CREATE TRIGGER reject_runtime BEFORE INSERT ON history_windows
            WHEN NEW.window_kind='runtime' AND NEW.window_name='conv_write'
            BEGIN SELECT RAISE(ABORT, 'runtime rejected'); END
            """
        )
    archive_window = empty_window("alice", "web", "conv_write")
    _append_round(archive_window, 1)
    runtime_window = copy.deepcopy(archive_window)
    commit_terminal_windows(
        archive,
        archive_window,
        runtime,
        runtime_window,
    )
    with sqlite3.connect(db_path) as database:
        assert database.execute(
            "SELECT COUNT(*) FROM history_windows WHERE window_name='conv_write'"
        ).fetchone()[0] == 1
        assert database.execute(
            "SELECT COUNT(*) FROM history_windows "
            "WHERE window_kind='runtime' AND window_name='conv_write'"
        ).fetchone()[0] == 0
        assert database.execute(
            "SELECT COUNT(*) FROM history_sessions WHERE session_id='conv_write'"
        ).fetchone()[0] == 1
    _path, cached = load_runtime_window(archive, archive_window)
    assert cached["text"]["messages"] == runtime_window["text"]["messages"]


def test_legacy_runtime_snapshot_is_not_rewritten_per_round(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    runtime = runtime_window_path(archive)
    legacy = empty_window("alice", "web", "conv_write")
    _append_round(legacy, 1)
    save_window(runtime, copy.deepcopy(legacy))
    db_path = database_path(tmp_path, "alice")
    with sqlite3.connect(db_path) as database:
        before = database.execute(
            "SELECT text_json, updated_at FROM history_windows "
            "WHERE window_kind='runtime' AND window_name='conv_write'"
        ).fetchone()
        database.executescript(
            """
            CREATE TABLE runtime_write_audit(kind TEXT NOT NULL);
            CREATE TRIGGER audit_runtime_update AFTER UPDATE ON history_windows
            WHEN NEW.window_kind='runtime' AND NEW.window_name='conv_write'
            BEGIN INSERT INTO runtime_write_audit(kind) VALUES('update'); END;
            """
        )
    archive_window = empty_window("alice", "web", "conv_write")
    _append_round(archive_window, 1)
    commit_terminal_windows(
        archive, archive_window, runtime, copy.deepcopy(archive_window)
    )
    _append_round(archive_window, 2)
    commit_terminal_windows(
        archive, archive_window, runtime, copy.deepcopy(archive_window)
    )
    with sqlite3.connect(db_path) as database:
        after = database.execute(
            "SELECT text_json, updated_at FROM history_windows "
            "WHERE window_kind='runtime' AND window_name='conv_write'"
        ).fetchone()
        writes = database.execute(
            "SELECT COUNT(*) FROM runtime_write_audit"
        ).fetchone()[0]
    assert after == before
    assert writes == 0


def test_terminal_commit_persists_summary_without_runtime_row(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    runtime = runtime_window_path(archive)
    window = empty_window("alice", "web", "conv_write")
    _append_round(window, 1)
    summary = {
        "schema_version": 3,
        "source_hash": "summary-hash",
        "previous_source_hash": None,
        "covered_rounds": [1],
        "covered_through_round": 1,
        "created_at": "2026-09-16T00:00:00+00:00",
        "summary": {"narrative": "compressed"},
        "memory_extractions": [],
    }
    commit_terminal_windows(
        archive,
        window,
        runtime,
        copy.deepcopy(window),
        summary_cache=summary,
    )
    assert read_summary_cache(runtime)["source_hash"] == "summary-hash"
    with sqlite3.connect(database_path(tmp_path, "alice")) as database:
        assert database.execute(
            "SELECT COUNT(*) FROM history_windows WHERE window_kind='runtime'"
        ).fetchone()[0] == 0


def test_runtime_cache_is_deep_copied_and_lru_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_cache.clear()
    monkeypatch.setattr(runtime_cache, "MAX_ENTRIES", 2)
    windows = []
    for index in range(3):
        path = runtime_window_path(_archive(tmp_path, session_id=f"conv-{index}"))
        window = empty_window("alice", "web", f"conv-{index}")
        window["data"]["updated_at"] = f"2026-09-16T00:00:0{index}+00:00"
        runtime_cache.store(
            path, window, version=runtime_cache.archive_version(window)
        )
        windows.append((path, window))
    assert runtime_cache.stats()["entries"] == 2
    assert runtime_cache.load(
        windows[0][0], version=runtime_cache.archive_version(windows[0][1])
    ) is None
    loaded = runtime_cache.load(
        windows[2][0], version=runtime_cache.archive_version(windows[2][1])
    )
    loaded["data"]["title"] = "mutated"
    reloaded = runtime_cache.load(
        windows[2][0], version=runtime_cache.archive_version(windows[2][1])
    )
    assert reloaded["data"].get("title") != "mutated"


def test_runtime_cache_invalidates_after_clear_and_delete(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    runtime = runtime_window_path(archive)
    window = empty_window("alice", "web", "conv_write")
    _append_round(window, 1)
    commit_terminal_windows(archive, window, runtime, copy.deepcopy(window))
    assert runtime_cache.stats()["entries"] >= 1

    clear_session(tmp_path, "alice", "web", "conv_write")
    _path, cleared = load_runtime_window(archive)
    assert cleared["data"]["rounds"] == 0
    assert cleared["text"]["messages"] == []

    delete_session(tmp_path, "alice", "web", "conv_write")
    assert runtime_cache.load(
        runtime,
        version=runtime_cache.archive_version(cleared),
    ) is None


def test_runtime_cache_version_change_rebuilds_cross_process_archive(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    runtime = runtime_window_path(archive)
    first = empty_window("alice", "web", "conv_write")
    _append_round(first, 1)
    commit_terminal_windows(archive, first, runtime, copy.deepcopy(first))

    external = load_window(archive)
    _append_round(external, 2)
    save_window(archive, external)

    _path, rebuilt = load_runtime_window(archive, max_rounds=80)
    assert rebuilt["data"]["rounds"] == 2
    assert rebuilt["text"]["messages"][-1]["content"] == "answer-2"


def test_runtime_rebuild_reads_only_archive_tail_and_rebases_rounds(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    window = empty_window("alice", "web", "conv_write")
    for number in range(1, 101):
        _append_round(window, number)
    save_window(archive, window)

    _path, rebuilt = load_runtime_window(archive, max_rounds=10)

    assert rebuilt["data"]["rounds"] == 10
    assert rebuilt["data"]["context"]["round_offset"] == 90
    assert [item["round"] for item in rebuilt["think"]["rounds"]] == list(
        range(1, 11)
    )
    assert rebuilt["text"]["messages"][0]["content"] == "question-91"
    assert rebuilt["text"]["messages"][-1]["content"] == "answer-100"


def test_same_idle_run_state_skips_registry_write(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    runtime = runtime_window_path(archive)
    window = empty_window("alice", "web", "conv_write")
    _append_round(window, 1)
    commit_terminal_windows(archive, window, runtime, copy.deepcopy(window))
    db_path = database_path(tmp_path, "alice")
    with sqlite3.connect(db_path) as database:
        database.executescript(
            """
            CREATE TABLE state_write_audit(kind TEXT NOT NULL);
            CREATE TRIGGER audit_state_update AFTER UPDATE ON history_sessions
            WHEN NEW.session_id='conv_write'
            BEGIN INSERT INTO state_write_audit(kind) VALUES('update'); END;
            """
        )
    update_run_state(
        tmp_path,
        "alice",
        "web",
        "conv_write",
        run_state="idle",
    )
    with sqlite3.connect(db_path) as database:
        assert database.execute(
            "SELECT COUNT(*) FROM state_write_audit"
        ).fetchone()[0] == 0


def test_trimmed_runtime_round_rows_use_shifted_incremental_rewrite(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    runtime = runtime_window_path(archive)
    full = empty_window("alice", "web", "conv_write")
    for number in range(1, 11):
        _append_round(full, number)
    save_window(runtime, copy.deepcopy(full))
    db_path = database_path(tmp_path, "alice")
    with sqlite3.connect(db_path) as database:
        database.executescript(
            """
            CREATE TABLE shifted_round_audit(kind TEXT NOT NULL);
            CREATE TRIGGER audit_shifted_round_insert AFTER INSERT ON history_rounds
            WHEN NEW.window_kind='runtime' AND NEW.window_name='conv_write'
            BEGIN INSERT INTO shifted_round_audit(kind) VALUES('insert'); END;
            CREATE TRIGGER audit_shifted_round_delete AFTER DELETE ON history_rounds
            WHEN OLD.window_kind='runtime' AND OLD.window_name='conv_write'
            BEGIN INSERT INTO shifted_round_audit(kind) VALUES('delete'); END;
            """
        )
    _append_round(full, 11)
    shifted = _trim_to_max_rounds(full, 10)
    save_window(runtime, shifted)
    with sqlite3.connect(db_path) as database:
        audit = dict(
            database.execute(
                "SELECT kind, COUNT(*) FROM shifted_round_audit GROUP BY kind"
            ).fetchall()
        )
        round_numbers = [
            row[0]
            for row in database.execute(
                "SELECT round_number FROM history_rounds "
                "WHERE window_kind='runtime' AND window_name='conv_write' "
                "ORDER BY round_number"
            ).fetchall()
        ]
    assert audit == {"delete": 1, "insert": 1}
    assert round_numbers == list(range(1, 11))


def test_memory_metadata_patch_does_not_touch_transcript_rows(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    runtime = runtime_window_path(archive)
    window = empty_window("alice", "web", "conv_write")
    _append_round(window, 1)
    commit_terminal_windows(archive, window, runtime, copy.deepcopy(window))
    db_path = database_path(tmp_path, "alice")
    with sqlite3.connect(db_path) as database:
        before = database.execute(
            "SELECT text_json, think_json, tool_json, items_json "
            "FROM history_windows WHERE window_kind='archive' AND window_name='conv_write'"
        ).fetchone()
        before_messages = database.execute(
            "SELECT message_index, message_json FROM history_messages "
            "WHERE window_name='conv_write' ORDER BY message_index"
        ).fetchall()
    patch_archive_metadata(
        archive,
        window,
        updates={"memory_status": "completed", "memory_processed_round": 1},
    )
    with sqlite3.connect(db_path) as database:
        after = database.execute(
            "SELECT text_json, think_json, tool_json, items_json "
            "FROM history_windows WHERE window_kind='archive' AND window_name='conv_write'"
        ).fetchone()
        after_messages = database.execute(
            "SELECT message_index, message_json FROM history_messages "
            "WHERE window_name='conv_write' ORDER BY message_index"
        ).fetchall()
    assert after == before
    assert after_messages == before_messages
    assert load_window(archive)["data"]["memory_status"] == "completed"


def test_memory_metadata_patch_merges_the_latest_database_snapshot(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    runtime = runtime_window_path(archive)
    window = empty_window("alice", "web", "conv_write")
    _append_round(window, 1)
    commit_terminal_windows(archive, window, runtime, copy.deepcopy(window))
    db_path = database_path(tmp_path, "alice")

    # Simulate a newer round committed by another process after the caller
    # loaded ``window`` but before its maintenance metadata transition.
    with sqlite3.connect(db_path) as database:
        data = json.loads(
            database.execute(
                "SELECT data_json FROM history_windows "
                "WHERE window_kind='archive' AND window_name='conv_write'"
            ).fetchone()[0]
        )
        data.update({"rounds": 2, "token_usage": {"total_tokens": 99}})
        database.execute(
            "UPDATE history_windows SET rounds=2, data_json=? "
            "WHERE window_kind='archive' AND window_name='conv_write'",
            (json.dumps(data, ensure_ascii=False, separators=(",", ":")),),
        )
        record = json.loads(
            database.execute(
                "SELECT record_json FROM history_sessions "
                "WHERE source='web' AND session_id='conv_write'"
            ).fetchone()[0]
        )
        record.update({"rounds": 2, "summary": "newer summary", "future_marker": "keep"})
        database.execute(
            "UPDATE history_sessions SET rounds=2, summary=?, record_json=? "
            "WHERE source='web' AND session_id='conv_write'",
            (
                "newer summary",
                json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    patch_archive_metadata(
        archive,
        window,
        updates={"memory_status": "completed"},
    )

    merged = load_window(archive)
    assert merged["data"]["rounds"] == 2
    assert merged["data"]["token_usage"] == {"total_tokens": 99}
    assert merged["data"]["memory_status"] == "completed"
    indexed = read_registry_record(tmp_path, "alice", "web", "conv_write")
    assert indexed is not None
    assert indexed["rounds"] == 2
    assert indexed["summary"] == "newer summary"
    assert indexed["future_marker"] == "keep"
    assert indexed["memory_status"] == "completed"


def test_schema_v3_migrates_legacy_partition_blobs_without_losing_rounds(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    window = empty_window("alice", "web", "conv_write")
    _append_round(window, 1)
    save_window(archive, window)
    db_path = database_path(tmp_path, "alice")
    legacy_text = json.dumps(window["text"], ensure_ascii=False, separators=(",", ":"))
    legacy_data = json.dumps(window["data"], ensure_ascii=False, separators=(",", ":"))
    legacy_think = json.dumps(window["think"], ensure_ascii=False, separators=(",", ":"))
    legacy_tool = json.dumps(window["tool"], ensure_ascii=False, separators=(",", ":"))
    legacy_items = json.dumps(window["items"], ensure_ascii=False, separators=(",", ":"))
    with sqlite3.connect(db_path) as database:
        _configure(database)
        database.execute("DELETE FROM history_messages WHERE window_name='conv_write'")
        database.execute("DELETE FROM history_rounds WHERE window_name='conv_write'")
        database.execute(
            "UPDATE history_windows SET data_json=?, text_json=?, think_json=?, tool_json=?, items_json=? "
            "WHERE window_kind='archive' AND window_name='conv_write'",
            (legacy_data, legacy_text, legacy_think, legacy_tool, legacy_items),
        )
        database.execute(
            "UPDATE history_meta SET value='1' WHERE key='schema_version'"
        )
        _ensure_schema(database)
        database.commit()
        version = database.execute(
            "SELECT value FROM history_meta WHERE key='schema_version'"
        ).fetchone()[0]
        compact = json.loads(
            database.execute(
                "SELECT text_json FROM history_windows "
                "WHERE window_kind='archive' AND window_name='conv_write'"
            ).fetchone()[0]
        )
        count = database.execute(
            "SELECT COUNT(*) FROM history_messages WHERE window_name='conv_write'"
        ).fetchone()[0]
        round_count = database.execute(
            "SELECT COUNT(*) FROM history_rounds "
            "WHERE window_kind='archive' AND window_name='conv_write'"
        ).fetchone()[0]
    assert version == "6"
    assert compact["storage"] == "history_messages"
    assert count == 2
    assert round_count == 1
    restored = load_window(archive)
    assert restored["text"]["messages"] == window["text"]["messages"]
    assert restored["think"] == window["think"]
    assert restored["tool"] == window["tool"]
    assert restored["items"] == window["items"]
    assert restored["data"]["round_metrics"] == window["data"]["round_metrics"]
