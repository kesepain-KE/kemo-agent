import sqlite3

import pytest

from run.history import commit_window, empty_window
from run.history.store_core import connection
from run.history.registry_store import read_registry_metadata
from run.history.window_deletion import (
    delete_window, delete_session_windows, delete_source_windows,
)
from run.history.delete_orchestrator import delete_history_sessions
from run.history.web_lifecycle import touch_session_lease, active_session_lease_clients


def seed(root, source="web", name="archive", session="shared"):
    (root / "users/alice").mkdir(parents=True, exist_ok=True)
    path = root / "users/alice/history" / name
    window = empty_window("alice", source, session)
    window["text"]["messages"] = [{"role": "user", "content": f"keep {source}"}]
    window["data"]["rounds"] = 1
    commit_window(path, window)
    return path


def revision(root):
    return int(read_registry_metadata(root, "alice")["registry_revision"])


def test_delete_last_archive_removes_session_bindings_marks_and_bumps_revision(tmp_path):
    path = seed(tmp_path)
    with connection(tmp_path, "alice", write=True) as db:
        db.execute("INSERT INTO history_active_sessions VALUES('active','web','shared')")
        db.execute("INSERT INTO history_session_marks VALUES('web','shared','',1)")
    assert touch_session_lease(tmp_path, "alice", "web", "shared", "browser")
    before = revision(tmp_path)
    assert delete_window(path)
    assert revision(tmp_path) > before
    with connection(tmp_path, "alice") as db:
        for table in ("history_sessions", "history_windows", "history_active_sessions",
                      "history_messages", "history_session_marks", "history_web_leases"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM history_deleted_sessions").fetchone()[0] == 1


def test_delete_one_of_multiple_archives_preserves_registry_and_repairs_pointer(tmp_path):
    first = seed(tmp_path, name="first")
    second = seed(tmp_path, name="second")
    assert delete_window(second)
    with connection(tmp_path, "alice") as db:
        record = db.execute("SELECT archive_window FROM history_sessions").fetchone()
        assert record["archive_window"] == first.name
        assert db.execute("SELECT COUNT(*) FROM history_messages").fetchone()[0] == 1


def test_runtime_only_window_delete_does_not_delete_logical_session(tmp_path):
    seed(tmp_path)
    from run.history.window_store import save_window
    runtime = tmp_path / "users/alice/history/temp/runtime"
    save_window(runtime, empty_window("alice", "web", "shared"))
    assert delete_window(runtime)
    with connection(tmp_path, "alice") as db:
        assert db.execute("SELECT COUNT(*) FROM history_sessions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM history_messages").fetchone()[0] == 1


def test_low_level_delete_is_one_transaction_even_if_registry_delete_fails(tmp_path):
    path = seed(tmp_path)
    with connection(tmp_path, "alice", write=True) as db:
        db.execute("""CREATE TRIGGER reject_registry_delete BEFORE DELETE ON history_sessions
                   BEGIN SELECT RAISE(ABORT, 'audit block'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="audit block"):
        delete_window(path)
    with connection(tmp_path, "alice") as db:
        assert db.execute("SELECT COUNT(*) FROM history_messages").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM history_windows").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM history_deleted_windows").fetchone()[0] == 0


@pytest.mark.parametrize("source", ["web", "app"])
def test_delete_only_own_source_leases(tmp_path, source):
    seed(tmp_path, "web", "web-archive")
    seed(tmp_path, "app", "app-archive")
    for channel in ("web", "app"):
        assert touch_session_lease(tmp_path, "alice", channel, "shared", "same-client")
    other = "app" if source == "web" else "web"
    delete_history_sessions(tmp_path, "alice", source, "shared")
    assert active_session_lease_clients(tmp_path, "alice", source, "shared") == []
    assert active_session_lease_clients(tmp_path, "alice", other, "shared") == ["same-client"]


@pytest.mark.parametrize("entry", ["session", "source"])
def test_old_public_entries_delegate_to_full_transaction(tmp_path, entry):
    seed(tmp_path)
    if entry == "session":
        assert delete_session_windows(tmp_path, "alice", "web", "shared") == 1
    else:
        assert delete_source_windows(tmp_path, "alice", "web") == (1, 1)
    with connection(tmp_path, "alice") as db:
        assert db.execute("SELECT COUNT(*) FROM history_sessions").fetchone()[0] == 0


def test_source_identity_uses_exact_prefix_not_like_wildcards(tmp_path):
    seed(tmp_path, "x_y", "one")
    seed(tmp_path, "xay", "two")
    for source in ("x_y", "xay"):
        assert touch_session_lease(tmp_path, "alice", source, "shared", "client")
    delete_history_sessions(tmp_path, "alice", "x_y", "shared")
    assert active_session_lease_clients(tmp_path, "alice", "xay", "shared") == ["client"]
