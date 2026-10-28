"""Update snapshots precede *all* database initialization, including memory."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from run.infra import sqlite_migrations
from tests.persistence.test_sqlite_migration_safety import (
    KINDS, VERSIONS, db_path, seed_version, snapshot, stored_version,
)
from update import migrations
from update._utils import UpdateError


def setup_databases(root: Path) -> dict[str, tuple[str, str]]:
    config = root / "users/alice/user_config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")
    return {kind: snapshot(seed_version(root, kind, 0)) for kind in KINDS}


def test_update_saves_all_original_databases_before_first_migration(tmp_path):
    originals = setup_databases(tmp_path)
    backups = migrations.backup_runtime_databases(root=tmp_path, dry_run=False)
    assert backups.directory is not None
    manifest = json.loads((backups.directory / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "complete"
    assert len(manifest["databases"]) == 4
    for kind in KINDS:
        relative = db_path(tmp_path, kind).relative_to(tmp_path)
        assert snapshot(backups.directory / relative)[0] == originals[kind][0]
        assert stored_version(db_path(tmp_path, kind), kind) == 0
    migrations.initialize_user_memory_databases(root=tmp_path, dry_run=False, backups=backups)
    migrations.initialize_runtime_state_databases(root=tmp_path, dry_run=False, backups=backups)
    for kind in KINDS:
        assert stored_version(db_path(tmp_path, kind), kind) == VERSIONS[kind]
        assert snapshot(backups.directory / db_path(tmp_path, kind).relative_to(tmp_path))[0] == originals[kind][0]
    assert len(list((tmp_path / "runtime/db_backups").iterdir())) == 1


def test_core_cli_completes_database_backups_before_all_initializers(tmp_path):
    from unittest.mock import Mock
    from update import cli
    from update.constants import MODULES

    originals = setup_databases(tmp_path)
    version = {
        "version": "1.0.0",
        "components": {name: {"version": "1.0.0"} for name in MODULES},
    }
    events = []
    real_backup = migrations.backup_runtime_databases
    prepared = []

    def backup(**kwargs):
        result = real_backup(**kwargs)
        prepared.append(result)
        events.append("backup_complete")
        return result

    def initialize(name):
        def check(**kwargs):
            assert kwargs["backups"] is prepared[0]
            assert prepared[0].directory is not None
            for kind in KINDS:
                relative = db_path(tmp_path, kind).relative_to(tmp_path)
                assert snapshot(prepared[0].directory / relative)[0] == originals[kind][0]
            events.append(name)
        return check

    with (
        patch.multiple(
            cli,
            _print_platform_warning=Mock(),
            load_local_version_document=Mock(return_value=version),
            load_remote_version_document=Mock(return_value=version),
            should_update=Mock(return_value=True),
            require_commands=Mock(),
            clone_latest=Mock(return_value=Path(__file__).resolve().parents[2]),
            load_source_version_document=Mock(return_value=version),
            source_revision=Mock(return_value="offline-test"),
            run_modules=Mock(return_value=[{
                "module": "core", "status": "ok", "details": [], "warnings": [],
            }]),
            refresh_dependencies=Mock(),
            finalize_version_document=Mock(),
        ),
        patch.object(cli, "backup_runtime_databases", side_effect=backup),
        patch.object(cli, "migrate_user_skeletons", side_effect=lambda **_: events.append("skeleton")),
        patch.object(cli, "initialize_user_memory_databases", side_effect=initialize("memory")),
        patch.object(cli, "initialize_runtime_state_databases", side_effect=initialize("runtime")),
    ):
        assert cli.main(["--module", "core", "--yes"], root=tmp_path) == 0
    assert events == ["backup_complete", "skeleton", "memory", "runtime"]


def test_update_backup_dry_run_has_no_filesystem_side_effects(tmp_path):
    originals = setup_databases(tmp_path)
    result = migrations.backup_runtime_databases(root=tmp_path, dry_run=True)
    assert result.directory is None
    assert not (tmp_path / "runtime/db_backups").exists()
    for kind in KINDS:
        assert snapshot(db_path(tmp_path, kind)) == originals[kind]


def test_last_backup_failure_prevents_even_first_memory_migration(tmp_path):
    originals = setup_databases(tmp_path)
    real_backup = sqlite_migrations.backup_database

    def fail_logs(source, destination, **kwargs):
        if source.name == "logs.sqlite3":
            raise OSError("backup target full")
        return real_backup(source, destination, **kwargs)

    with patch.object(sqlite_migrations, "backup_database", side_effect=fail_logs):
        with pytest.raises(UpdateError, match="backup target full"):
            migrations.initialize_user_memory_databases(root=tmp_path, dry_run=False)
    for kind in KINDS:
        assert snapshot(db_path(tmp_path, kind)) == originals[kind]
    # Earlier successful copies remain; an incomplete set has no complete manifest.
    directory = next((tmp_path / "runtime/db_backups").iterdir())
    assert list(directory.rglob("*.sqlite3"))
    assert not (directory / "manifest.json").exists()


def test_update_migration_failure_reports_existing_recovery_set(tmp_path):
    setup_databases(tmp_path)
    path = db_path(tmp_path, "tasks")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE task_plan_meta SET value='9' WHERE key='schema_version'")
    originals = {kind: snapshot(db_path(tmp_path, kind))[0] for kind in KINDS}
    backups = migrations.backup_runtime_databases(root=tmp_path, dry_run=False)
    assert backups.directory is not None
    with pytest.raises(UpdateError) as error:
        migrations.initialize_runtime_state_databases(root=tmp_path, dry_run=False, backups=backups)
    assert str(backups.directory) in str(error.value)
    assert "manifest.json" in str(error.value)
    for kind in KINDS:
        assert snapshot(backups.directory / db_path(tmp_path, kind).relative_to(tmp_path))[0] == originals[kind]
    assert stored_version(path, "tasks") == 9


def test_standalone_runtime_initializer_also_backs_up_memory(tmp_path):
    originals = setup_databases(tmp_path)
    migrations.initialize_runtime_state_databases(root=tmp_path, dry_run=False)
    directory = next((tmp_path / "runtime/db_backups").iterdir())
    assert snapshot(directory / db_path(tmp_path, "memory").relative_to(tmp_path))[0] == originals["memory"][0]
    assert stored_version(db_path(tmp_path, "memory"), "memory") == 0


def test_update_never_prunes_previous_database_backups(tmp_path):
    setup_databases(tmp_path)
    first = migrations.backup_runtime_databases(root=tmp_path, dry_run=False)
    assert first.directory is not None
    before = (first.directory / "manifest.json").read_bytes()
    for _ in range(4):
        migrations.backup_runtime_databases(root=tmp_path, dry_run=False)
    assert len(list((tmp_path / "runtime/db_backups").iterdir())) == 5
    assert (first.directory / "manifest.json").read_bytes() == before


def test_prepared_backups_cannot_be_used_for_another_installation(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    setup_databases(left)
    originals = setup_databases(right)
    backups = migrations.backup_runtime_databases(root=left, dry_run=False)
    with pytest.raises(UpdateError, match="安装根不匹配"):
        migrations.initialize_runtime_state_databases(root=right, dry_run=False, backups=backups)
    for kind in KINDS:
        assert snapshot(db_path(right, kind)) == originals[kind]


def test_dry_run_backup_receipt_cannot_skip_real_backup(tmp_path):
    originals = setup_databases(tmp_path)
    backups = migrations.backup_runtime_databases(root=tmp_path, dry_run=True)
    with pytest.raises(UpdateError, match="dry-run"):
        migrations.initialize_runtime_state_databases(root=tmp_path, dry_run=False, backups=backups)
    for kind in KINDS:
        assert snapshot(db_path(tmp_path, kind)) == originals[kind]


def test_missing_backup_manifest_prevents_database_initialization(tmp_path):
    originals = setup_databases(tmp_path)
    backups = migrations.DatabaseBackups(tmp_path.resolve(), tmp_path / "incomplete-backup")
    with pytest.raises(UpdateError, match="不完整"):
        migrations.initialize_runtime_state_databases(root=tmp_path, dry_run=False, backups=backups)
    for kind in KINDS:
        assert snapshot(db_path(tmp_path, kind)) == originals[kind]


def test_backup_helper_cannot_overwrite_existing_backup(tmp_path):
    path = seed_version(tmp_path, "history", 0)
    target = tmp_path / "saved.sqlite3"
    sqlite_migrations.backup_database(path, target)
    before = target.read_bytes()
    with pytest.raises(RuntimeError, match="拒绝覆盖"):
        sqlite_migrations.backup_database(path, target)
    assert target.read_bytes() == before


def test_backup_helper_times_out_without_publishing_partial_copy(tmp_path):
    path = seed_version(tmp_path, "history", 0)
    target = tmp_path / "saved.sqlite3"
    with closing(sqlite3.connect(path)) as locked:
        locked.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(RuntimeError, match="超时"):
                sqlite_migrations.backup_database(path, target, timeout=0.05)
        finally:
            locked.rollback()
    assert not target.exists()
    assert list(tmp_path.glob("saved.sqlite3.*.partial"))


def test_transactional_schema_executor_handles_semicolons_inside_literals():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("BEGIN IMMEDIATE")
        sqlite_migrations.execute_schema(db, """
            -- comment with ; punctuation
            CREATE TABLE sample(body TEXT);
            INSERT INTO sample VALUES('a;b');
        """)
        assert db.in_transaction
        assert db.execute("SELECT body FROM sample").fetchone()[0] == "a;b"
        db.rollback()
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='sample'").fetchall()
