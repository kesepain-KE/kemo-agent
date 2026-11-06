"""Migration contracts use isolated databases, never a live installation."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from run.cards import CardStore
from run.cards import schema as cards
from run.infra import log_store as logs
from run.infra.sqlite_migrations import SchemaMigrationError
from run.memory import store as memory
from run.tasks import store as tasks


KINDS = ("cards", "tasks", "logs", "memory")
VERSIONS = {"cards": cards.SCHEMA_VERSION, "tasks": 1, "logs": 3, "memory": memory.MEMORY_DB_SCHEMA_VERSION}
META = {
    "tasks": "task_plan_meta", "memory": "memory_meta",
}


def db_path(root: Path, kind: str) -> Path:
    return {
        "cards": root / "users/alice/cards.sqlite3",
        "tasks": root / "users/alice/task_plan/task_plans.sqlite3",
        "logs": root / "runtime/logs.sqlite3",
        "memory": root / "users/alice/improve/memory.sqlite3",
    }[kind]


def open_store(root: Path, kind: str) -> None:
    path = db_path(root, kind)
    if kind == "cards":
        path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve()).casefold()
    if kind == "cards":
        with CardStore(root, "alice").db():
            pass
    elif kind == "tasks":
        tasks._READY_DATABASES.discard(key)
        tasks.PlanStore(root, "alice")._ensure_database()
    elif kind == "memory":
        memory._READY_DATABASES.discard(key)
        memory._ensure_database(path)
    else:
        logs._READY_PATHS.discard(key)
        with logs.LogStore(root)._connection():
            pass


def seed_version(root: Path, kind: str, version: object) -> Path:
    path = db_path(root, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE audit_sentinel(body TEXT NOT NULL)")
        db.execute("INSERT INTO audit_sentinel VALUES('keep original data')")
        if kind in {"cards", "logs"}:
            db.execute(f"PRAGMA user_version={int(str(version))}")
        else:
            db.execute(f"CREATE TABLE {META[kind]}(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute(
                f"INSERT INTO {META[kind]} VALUES('schema_version', ?)", (str(version),)
            )
    return path


def snapshot(path: Path) -> tuple[str, str]:
    with closing(sqlite3.connect(path)) as db:
        return "\n".join(db.iterdump()), str(db.execute("PRAGMA journal_mode").fetchone()[0])


def stored_version(path: Path, kind: str) -> int:
    with closing(sqlite3.connect(path)) as db:
        if kind in {"cards", "logs"}:
            return db.execute("PRAGMA user_version").fetchone()[0]
        return int(db.execute(
            f"SELECT value FROM {META[kind]} WHERE key='schema_version'"
        ).fetchone()[0])


@pytest.mark.parametrize("kind", KINDS)
def test_future_schema_is_rejected_before_ddl_or_journal_changes(tmp_path, kind):
    path = seed_version(tmp_path, kind, 9)
    before = snapshot(path)
    with pytest.raises(RuntimeError, match="schema"):
        open_store(tmp_path, kind)
    assert snapshot(path) == before
    assert stored_version(path, kind) == 9
    assert not list(path.parent.glob(path.name + ".bak.*"))


@pytest.mark.parametrize("kind", ("tasks", "memory"))
@pytest.mark.parametrize("version", ("broken", "-1"))
def test_invalid_schema_is_not_silently_treated_as_unversioned(tmp_path, kind, version):
    path = seed_version(tmp_path, kind, version)
    before = snapshot(path)
    with pytest.raises(RuntimeError, match="schema"):
        open_store(tmp_path, kind)
    assert snapshot(path) == before


@pytest.mark.parametrize("kind", KINDS)
def test_existing_database_has_complete_pre_migration_backup(tmp_path, kind):
    path = seed_version(tmp_path, kind, 0)
    before, _ = snapshot(path)
    open_store(tmp_path, kind)
    backups = list(path.parent.glob(path.name + ".bak.v0.*"))
    assert len(backups) == 1
    assert snapshot(backups[0])[0] == before
    assert stored_version(path, kind) == VERSIONS[kind]
    with closing(sqlite3.connect(backups[0])) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # Reinitialization must not overwrite or multiply a successful backup.
    open_store(tmp_path, kind)
    assert list(path.parent.glob(path.name + ".bak.v0.*")) == backups


@pytest.mark.parametrize("kind", KINDS)
def test_brand_new_database_needs_no_empty_backup(tmp_path, kind):
    open_store(tmp_path, kind)
    path = db_path(tmp_path, kind)
    assert stored_version(path, kind) == VERSIONS[kind]
    assert not list(path.parent.glob(path.name + ".bak.*"))


def test_task_plan_legacy_columns_are_added_without_losing_plan(tmp_path):
    path = seed_version(tmp_path, "tasks", 1)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE task_plans(
                plan_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
                title TEXT NOT NULL, description TEXT NOT NULL, user TEXT NOT NULL,
                source TEXT NOT NULL, session_id TEXT NOT NULL, status TEXT NOT NULL,
                auto_accept INTEGER NOT NULL, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO task_plans VALUES(
                'old-plan',1,'original title','original body','alice','cli','default',
                'draft',0,1,'2026-01-01','2026-01-01'
            );
        """)
    before, _ = snapshot(path)
    open_store(tmp_path, "tasks")
    plan = tasks.PlanStore(tmp_path, "alice").read("old-plan")
    assert plan["title"] == "original title"
    assert plan["reminder"] == plan["current_step"] == ""
    backups = list(path.parent.glob(path.name + ".bak.v1.*"))
    assert len(backups) == 1
    assert snapshot(backups[0])[0] == before


def test_existing_plan_missing_revision_snapshot_is_backed_up_before_data_repair(tmp_path):
    open_store(tmp_path, "tasks")
    path = db_path(tmp_path, "tasks")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""
            INSERT INTO task_plans(
                plan_id,schema_version,title,description,user,source,session_id,status,
                auto_accept,revision,created_at,updated_at)
            VALUES('old',1,'original','body','alice','cli','default','draft',0,1,'old','old')
        """)
    before, _ = snapshot(path)
    open_store(tmp_path, "tasks")
    backup = next(path.parent.glob(path.name + ".bak.v1.*"))
    assert snapshot(backup)[0] == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT plan_id FROM task_plan_revisions").fetchone()[0] == "old"


def test_log_legacy_input_state_columns_are_added_without_losing_state(tmp_path):
    path = seed_version(tmp_path, "logs", 2)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE message_route_state(
                machine_id TEXT PRIMARY KEY, user TEXT NOT NULL, platform TEXT NOT NULL,
                schema_version INTEGER NOT NULL, health TEXT NOT NULL,
                last_check TEXT, last_message_at TEXT, error TEXT, latency_ms INTEGER,
                messages_received_today INTEGER NOT NULL DEFAULT 0,
                messages_sent_today INTEGER NOT NULL DEFAULT 0,
                extra_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
            );
            INSERT INTO message_route_state(machine_id,user,platform,schema_version,health,updated_at)
            VALUES('old-route','alice','test',1,'healthy','2026-01-01');
        """)
    open_store(tmp_path, "logs")
    state = logs.LogStore(tmp_path).read_message_route_state("old-route")
    assert state is not None
    assert state["health"] == "healthy"
    assert state["input_status"] == "unknown"
    assert state["input_restart_count"] == 0


def test_cardstream_initialization_never_reads_or_migrates_legacy_history(tmp_path):
    legacy = tmp_path / "users/alice/history/history.sqlite3"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"opaque legacy history; leave untouched")
    open_store(tmp_path, "cards")
    assert legacy.read_bytes() == b"opaque legacy history; leave untouched"
    assert db_path(tmp_path, "cards").is_file()
    assert not list(legacy.parent.glob("*.bak.*"))


@pytest.mark.parametrize("kind", KINDS)
def test_backup_failure_stops_all_schema_changes_and_is_retryable(tmp_path, kind):
    path = seed_version(tmp_path, kind, 0)
    before = snapshot(path)
    with patch("run.infra.sqlite_migrations.backup_database", side_effect=SchemaMigrationError("disk blocked")):
        with pytest.raises(RuntimeError, match="disk blocked"):
            open_store(tmp_path, kind)
    assert snapshot(path) == before
    open_store(tmp_path, kind)
    assert stored_version(path, kind) == VERSIONS[kind]


@pytest.mark.parametrize("kind", KINDS)
def test_migration_failure_rolls_back_ddl_and_version_then_can_retry(tmp_path, kind):
    path = seed_version(tmp_path, kind, 0)
    before = snapshot(path)
    module = {"cards": cards, "tasks": tasks, "logs": logs, "memory": memory}[kind]

    def fail_after_ddl(database, script):
        database.execute("CREATE TABLE must_rollback(body TEXT)")
        database.execute("INSERT INTO must_rollback VALUES('not committed')")
        raise sqlite3.OperationalError("injected migration error")

    with patch.object(module, "execute_schema", side_effect=fail_after_ddl):
        with pytest.raises(RuntimeError, match="injected migration error") as failure:
            open_store(tmp_path, kind)
    assert "备份" in str(failure.value)
    assert snapshot(path) == before
    backups = list(path.parent.glob(path.name + ".bak.v0.*"))
    assert len(backups) == 1
    assert snapshot(backups[0]) == before
    open_store(tmp_path, kind)
    assert stored_version(path, kind) == VERSIONS[kind]


@pytest.mark.parametrize("kind", ("tasks", "logs", "memory"))
def test_current_version_with_missing_structures_is_backed_up_before_repair(tmp_path, kind):
    path = seed_version(tmp_path, kind, VERSIONS[kind])
    before, _ = snapshot(path)
    open_store(tmp_path, kind)
    backups = list(path.parent.glob(path.name + f".bak.v{VERSIONS[kind]}.*"))
    assert len(backups) == 1
    assert snapshot(backups[0])[0] == before
    open_store(tmp_path, kind)
    assert len(list(path.parent.glob(path.name + ".bak.*"))) == 1


def test_cardstream_current_incomplete_schema_is_rejected_without_repair(tmp_path):
    path = seed_version(tmp_path, "cards", VERSIONS["cards"])
    before = snapshot(path)
    with pytest.raises(SchemaMigrationError, match="不完整"):
        open_store(tmp_path, "cards")
    assert snapshot(path) == before
    assert not list(path.parent.glob(path.name + ".bak.*"))


@pytest.mark.parametrize("kind", KINDS)
def test_pre_migration_backup_includes_committed_wal_data(tmp_path, kind):
    path = seed_version(tmp_path, kind, 0)
    with closing(sqlite3.connect(path)) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA wal_autocheckpoint=0")
        live.execute("INSERT INTO audit_sentinel VALUES('committed in WAL')")
        live.commit()
        assert Path(str(path) + "-wal").is_file()
        open_store(tmp_path, kind)
        backup = next(path.parent.glob(path.name + ".bak.v0.*"))
        with closing(sqlite3.connect(backup)) as reader:
            assert reader.execute("SELECT body FROM audit_sentinel ORDER BY rowid").fetchall() == [
                ("keep original data",), ("committed in WAL",),
            ]


@pytest.mark.parametrize("kind", KINDS)
def test_two_processes_serialize_schema_check_and_backup(tmp_path, kind):
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    path = seed_version(tmp_path, kind, 0)
    with ProcessPoolExecutor(2, mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(open_store, tmp_path, kind) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    assert stored_version(path, kind) == VERSIONS[kind]
    assert len(list(path.parent.glob(path.name + ".bak.v0.*"))) == 1
