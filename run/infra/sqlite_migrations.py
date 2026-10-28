"""Small, transactional SQLite migration and online-backup primitives."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterator, Mapping, Sequence
from uuid import uuid4


class SchemaMigrationError(RuntimeError):
    """An unsupported schema or an unsafe migration must stop initialization."""


def backup_database(source: Path, destination: Path, *, timeout: float = 30.0) -> Path:
    """Publish a standalone backup only after SQLite has copied and checked it.

    Read the source through a separate read-only connection. In particular, do
    not call backup() on a connection holding its own write transaction: SQLite
    would wait for that same transaction indefinitely. Failed partial files are
    retained for diagnosis, never advertised as usable backups or auto-pruned.
    """

    source = source.resolve()
    destination = destination.resolve()
    if source == destination or destination.exists():
        raise SchemaMigrationError(f"拒绝覆盖 SQLite 备份目标：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + f".{uuid4().hex}.partial")
    deadline = time.monotonic() + timeout

    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError("SQLite 在线备份超时")

    try:
        fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(
            source.as_uri() + "?mode=ro", uri=True, timeout=5.0
        )) as reader, closing(sqlite3.connect(partial)) as writer:
            reader.backup(writer, pages=256, progress=progress, sleep=0.05)
            check = writer.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise SchemaMigrationError("SQLite 备份完整性检查失败")
        with partial.open("r+b") as stream:
            os.fsync(stream.fileno())
        # Windows rename is no-replace. POSIX rename would overwrite a target
        # created after the initial check, so publish with an exclusive link.
        if os.name == "nt":
            partial.rename(destination)
        else:
            os.link(partial, destination)
            partial.unlink()  # Only this invocation's new, fully copied staging file.
        return destination
    except Exception as exc:
        raise SchemaMigrationError(
            f"SQLite 备份失败，已停止迁移；源库：{source}；"
            f"目标：{destination}；不完整文件（若存在）：{partial}；原因：{exc}"
        ) from exc


def schema_needs_repair(
    database: sqlite3.Connection, required: Mapping[str, Sequence[str]]
) -> bool:
    """Inspect only known additive structures, without changing the database."""

    for table, columns in required.items():
        actual = {str(row[1]) for row in database.execute(f'PRAGMA table_info("{table}")')}
        if not actual or not set(columns) <= actual:
            return True
    return False


def add_missing_columns(
    database: sqlite3.Connection, table: str, definitions: Mapping[str, str]
) -> None:
    """Identifiers/definitions are source constants, never user input."""

    actual = {str(row[1]) for row in database.execute(f'PRAGMA table_info("{table}")')}
    for column, definition in definitions.items():
        if column not in actual:
            database.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


def execute_schema(database: sqlite3.Connection, script: str) -> None:
    """Execute DDL without executescript()'s implicit pre-transaction COMMIT."""

    statement = ""
    for character in script:
        statement += character
        if character == ";" and sqlite3.complete_statement(statement):
            database.execute(statement)
            statement = ""
    if statement.strip():
        database.execute(statement)


def _schema_version(database: sqlite3.Connection, meta_table: str | None) -> int:
    if meta_table is None:
        return int(database.execute("PRAGMA user_version").fetchone()[0])
    exists = database.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (meta_table,)
    ).fetchone()
    if exists is None:
        return 0
    row = database.execute(
        f'SELECT value FROM "{meta_table}" WHERE key=?', ("schema_version",)
    ).fetchone()
    if row is None:
        return 0
    raw = str(row[0])
    if not raw.isascii() or not raw.isdecimal():
        raise SchemaMigrationError(f"无效的 SQLite schema 版本：{raw!r}（{meta_table}）")
    return int(raw)


@contextmanager
def schema_migration(
    database: sqlite3.Connection,
    *,
    target: int,
    meta_table: str | None,
    required: Mapping[str, Sequence[str]],
    data_migration: Callable[[sqlite3.Connection], bool] | None = None,
) -> Iterator[int]:
    """Serialize version check, pre-DDL backup and migration across processes.

    The caller must not change journal mode before entering. All migration DDL,
    data changes and the final version stamp commit together. A missing table or
    known column in an already-stamped database also requires a backup.
    """

    if database.in_transaction:
        raise SchemaMigrationError("schema 迁移必须使用独立、尚未开启事务的连接")
    database.execute("PRAGMA busy_timeout=5000")
    database.execute("BEGIN IMMEDIATE")
    backup: Path | None = None
    try:
        current = _schema_version(database, meta_table)
        if current < 0 or current > target:
            raise SchemaMigrationError(
                f"不支持的 SQLite schema：{current}，当前支持至 {target}；拒绝修改数据库"
            )
        has_tables = database.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone() is not None
        needs_migration = (
            current < target or schema_needs_repair(database, required)
            or (data_migration is not None and data_migration(database))
        )
        if has_tables and needs_migration:
            main = next(row for row in database.execute("PRAGMA database_list") if row[1] == "main")
            if main[2]:  # In-memory databases have no durable file to back up.
                path = Path(main[2])
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
                backup = backup_database(
                    path, path.with_name(f"{path.name}.bak.v{current}.{stamp}-{uuid4().hex[:8]}")
                )
        yield current
        if current != target:
            if meta_table is None:
                database.execute(f"PRAGMA user_version={target}")
            else:
                database.execute(
                    f'INSERT INTO "{meta_table}"(key,value) VALUES(?,?) '
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("schema_version", str(target)),
                )
        database.commit()
    except BaseException as exc:
        database.rollback()
        if backup is not None and isinstance(exc, Exception):
            raise SchemaMigrationError(
                f"SQLite schema 迁移失败，已回滚；迁移前备份：{backup}。"
                "恢复前须停止所有框架进程，保留原库及其 WAL/SHM，再用备份恢复；"
                f"原因：{exc}"
            ) from exc
        raise
