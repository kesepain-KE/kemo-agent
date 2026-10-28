from __future__ import annotations

import pytest

from run.history.registry_store import read_registry, write_registry, read_registry_metadata


@pytest.fixture(autouse=True)
def user(tmp_path):
    (tmp_path / "users" / "alice").mkdir(parents=True)


def record(key, **extra):
    return {"source": "web", "session_id": key, "title": key, **extra}


def test_stale_snapshot_does_not_delete_concurrent_session(tmp_path):
    write_registry(tmp_path, "alice", {"a": record("a")}, {})
    old, active = read_registry(tmp_path, "alice")
    write_registry(tmp_path, "alice", {"b": record("b")}, {})
    old["web\x1fa"]["title"] = "changed"
    write_registry(tmp_path, "alice", old, active)
    stored, _ = read_registry(tmp_path, "alice")
    assert set(stored) == {"web\x1fa", "web\x1fb"}


def test_revision_never_regresses(tmp_path):
    write_registry(tmp_path, "alice", {"a": record("a")}, {})
    before = int(read_registry_metadata(tmp_path, "alice")["registry_revision"])
    write_registry(tmp_path, "alice", {"b": record("b")}, {})
    assert int(read_registry_metadata(tmp_path, "alice")["registry_revision"]) > before


def test_same_row_stale_mutation_is_rejected(tmp_path):
    write_registry(tmp_path, "alice", {"a": record("a")}, {})
    one, active = read_registry(tmp_path, "alice")
    two, active_two = read_registry(tmp_path, "alice")
    one["web\x1fa"]["title"] = "first"
    write_registry(tmp_path, "alice", one, active)
    two["web\x1fa"]["title"] = "second"
    with pytest.raises(RuntimeError, match="conflict|冲突"):
        write_registry(tmp_path, "alice", two, active_two)


def test_concurrent_insert_of_same_identity_is_not_overwritten(tmp_path):
    one, active = read_registry(tmp_path, "alice")
    write_registry(tmp_path, "alice", {"web\x1fa": record("a", title="winner")}, {})
    one["web\x1fa"] = record("a", title="stale")
    with pytest.raises(RuntimeError, match="冲突"):
        write_registry(tmp_path, "alice", one, active)
    assert read_registry(tmp_path, "alice")[0]["web\x1fa"]["title"] == "winner"


def test_concurrent_new_active_binding_is_not_overwritten(tmp_path):
    one, active = read_registry(tmp_path, "alice")
    write_registry(tmp_path, "alice", {"web\x1fa": record("a")}, {
        "web": {"source": "web", "session_id": "a"},
    })
    active["web"] = {"source": "web", "session_id": "b"}
    with pytest.raises(RuntimeError, match="冲突"):
        write_registry(tmp_path, "alice", one, active)
    assert read_registry(tmp_path, "alice")[1]["web"]["session_id"] == "a"


@pytest.mark.parametrize("source", ["web", "cli", "cron"])
def test_delete_registry_failure_rolls_back_transcript_and_fences(tmp_path, monkeypatch, source):
    from run.history import commit_window, empty_window, connection, delete_session
    directory = tmp_path / "users/alice/history/test_transaction"
    window = empty_window("alice", source, "txn")
    window["text"]["messages"] = [{"role": "user", "content": "keep me"}]
    window["data"]["rounds"] = 1
    commit_window(directory, window)
    from run.history import delete_orchestrator
    original = delete_orchestrator._delete_session_windows

    def delete_then_fail(db, channel, session):
        original(db, channel, session)
        raise RuntimeError("injected deletion failure")

    monkeypatch.setattr(delete_orchestrator, "_delete_session_windows", delete_then_fail)
    with pytest.raises(RuntimeError, match="injected"):
        delete_session(tmp_path, "alice", source, "txn")
    with connection(tmp_path, "alice") as db:
        assert db.execute("SELECT COUNT(*) FROM history_messages").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM history_sessions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM history_deleted_sessions").fetchone()[0] == 0


def test_window_and_registry_commit_roll_back_together(tmp_path, monkeypatch):
    from run.history import commit_window, empty_window, connection
    from run.history import window_store
    directory = tmp_path / "users/alice/history/test_commit"

    def fail(*args, **kwargs):
        raise RuntimeError("injected registry failure")

    monkeypatch.setattr(window_store, "_write_registry_state", fail)
    with pytest.raises(RuntimeError, match="injected"):
        commit_window(directory, empty_window("alice", "web", "txn"))
    with connection(tmp_path, "alice") as db:
        assert db.execute("SELECT COUNT(*) FROM history_windows").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM history_sessions").fetchone()[0] == 0


@pytest.mark.parametrize("same_row", [True, False])
def test_real_threads_use_separate_transactions_without_lost_updates(tmp_path, same_row):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from run.history.registry_store import RegistryConflictError
    write_registry(tmp_path, "alice", {"a": record("a"), "b": record("b")}, {})
    before = int(read_registry_metadata(tmp_path, "alice")["registry_revision"])
    barrier = threading.Barrier(2)

    def mutate(number):
        sessions, active = read_registry(tmp_path, "alice")
        key = "web\x1fa" if same_row or number == 0 else "web\x1fb"
        sessions[key]["title"] = f"writer-{number}"
        barrier.wait(timeout=5)
        try:
            write_registry(tmp_path, "alice", sessions, active)
            return "ok"
        except RegistryConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(mutate, number) for number in range(2)]
        outcomes = [future.result(timeout=10) for future in futures]
    assert sorted(outcomes) == (["conflict", "ok"] if same_row else ["ok", "ok"])
    stored, _ = read_registry(tmp_path, "alice")
    if not same_row:
        assert stored["web\x1fa"]["title"] == "writer-0"
        assert stored["web\x1fb"]["title"] == "writer-1"
    assert int(read_registry_metadata(tmp_path, "alice")["registry_revision"]) == before + outcomes.count("ok")


@pytest.mark.parametrize("persistent", [False, True])
def test_read_repair_reloads_on_conflict_and_has_a_bounded_budget(tmp_path, monkeypatch, persistent):
    from run.history import index_core
    from run.history.registry_store import RegistryConflictError
    write_registry(tmp_path, "alice", {"a": record(
        "a", memory_claim_id="old", memory_processed_round=1, last_committed_round=1,
    )}, {})
    original = index_core._write_index_unlocked
    attempts = []

    def interleave(root, user, index):
        attempts.append(1)
        if persistent or len(attempts) == 1:
            other, active = read_registry(root, user)
            other["web\x1fa"]["title"] = f"other-{len(attempts)}"
            write_registry(root, user, other, active)
        return original(root, user, index)

    monkeypatch.setattr(index_core, "_write_index_unlocked", interleave)
    if persistent:
        with pytest.raises(RegistryConflictError):
            index_core.load_index(tmp_path, "alice")
        assert len(attempts) == 3
    else:
        result = index_core.load_index(tmp_path, "alice")
        assert result["sessions"]["web\x1fa"]["title"] == "other-1"
        assert "memory_claim_id" not in result["sessions"]["web\x1fa"]
        assert len(attempts) == 2
