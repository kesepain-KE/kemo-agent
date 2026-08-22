from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from run.history import (
    commit_terminal_windows,
    empty_window,
    load_window,
    patch_archive_metadata,
    runtime_window_path,
)
from run.history import (
    _configure,
    _ensure_schema,
    database_path,
    read_registry_record,
    save_window,
)


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


def test_terminal_bundle_rolls_back_both_windows_together(tmp_path: Path) -> None:
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
    with pytest.raises(sqlite3.IntegrityError, match="runtime rejected"):
        commit_terminal_windows(
            archive,
            archive_window,
            runtime,
            runtime_window,
        )
    with sqlite3.connect(db_path) as database:
        assert database.execute(
            "SELECT COUNT(*) FROM history_windows WHERE window_name='conv_write'"
        ).fetchone()[0] == 0
        assert database.execute(
            "SELECT COUNT(*) FROM history_sessions WHERE session_id='conv_write'"
        ).fetchone()[0] == 0


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
    assert version == "3"
    assert compact["storage"] == "history_messages"
    assert count == 2
    assert round_count == 1
    restored = load_window(archive)
    assert restored["text"]["messages"] == window["text"]["messages"]
    assert restored["think"] == window["think"]
    assert restored["tool"] == window["tool"]
    assert restored["items"] == window["items"]
    assert restored["data"]["round_metrics"] == window["data"]["round_metrics"]
