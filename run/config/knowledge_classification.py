"""Portable SQLite classification metadata for the three knowledge roots.

Each knowledge root owns one ``classify.sqlite3`` database.  The database
contains a real ``classify`` table and is intentionally not a knowledge
document; it is never injected into prompts.  It only binds a
knowledge-relative path to a logical category.  Categories are therefore
portable with the directory while the document itself keeps its original
path.

Older development builds wrote a SQL text dump named ``classify.sql``.  That
format is accepted once for migration, then converted to the real SQLite
table and removed.  New writes never recreate the legacy file.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
import os
import re
import sqlite3
import tempfile
import threading
from typing import Iterator


CLASSIFICATION_FILENAME = "classify.sqlite3"
LEGACY_CLASSIFICATION_FILENAME = "classify.sql"
UNCATEGORIZED = "未分类"
MAX_CATEGORY_LENGTH = 80
MAX_CLASSIFICATION_FILE_BYTES = 2 * 1024 * 1024

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS classify (
  relative_path TEXT PRIMARY KEY NOT NULL,
  category TEXT NOT NULL
);
"""
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class KnowledgeClassificationError(ValueError):
    """Raised when a classification sidecar cannot be safely read or written."""


def classification_path(root: Path) -> Path:
    return Path(root) / CLASSIFICATION_FILENAME


def _legacy_classification_path(root: Path) -> Path:
    return Path(root) / LEGACY_CLASSIFICATION_FILENAME


def normalize_category(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise KnowledgeClassificationError("分类名必须是字符串")
    category = value.strip()
    if not category or category == UNCATEGORIZED:
        return ""
    if len(category) > MAX_CATEGORY_LENGTH:
        raise KnowledgeClassificationError(
            f"分类名不能超过 {MAX_CATEGORY_LENGTH} 个字符"
        )
    if _CONTROL_CHARACTER_RE.search(category):
        raise KnowledgeClassificationError("分类名不能包含控制字符")
    if category in {".", ".."} or "/" in category or "\\" in category:
        raise KnowledgeClassificationError("分类名不能包含路径分隔符")
    return category


def normalize_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeClassificationError("知识文件路径不能为空")
    normalized = value.strip().replace("\\", "/")
    parts = normalized.split("/")
    if (
        normalized.startswith("/")
        or "\x00" in normalized
        or any(part in {"", ".", ".."} for part in parts)
        or (parts and ":" in parts[0])
    ):
        raise KnowledgeClassificationError("分类绑定路径必须是安全的相对路径")
    return "/".join(parts)


def _lock_for(root: Path) -> threading.RLock:
    key = os.path.normcase(str(Path(root).resolve()))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def classification_lock(root: Path) -> Iterator[None]:
    lock = _lock_for(root)
    with lock:
        yield


_LEGACY_STATEMENT_PREFIXES = (
    "CREATE TABLE CLASSIFY",
    "CREATE TABLE IF NOT EXISTS CLASSIFY",
    "CREATE INDEX",
    "CREATE UNIQUE INDEX",
    "INSERT INTO CLASSIFY",
    "INSERT OR REPLACE INTO CLASSIFY",
)


def _legacy_statements(script: str) -> list[str]:
    """按 SQLite 语法完整性切分脚本，供语句白名单逐条校验。"""

    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        statements.append(buffer.strip())
    return [item for item in statements if item.strip(" ;\t\r\n")]


def _allowed_legacy_statement(statement: str) -> bool:
    head = " ".join(statement.split()).upper()
    return any(head.startswith(prefix) for prefix in _LEGACY_STATEMENT_PREFIXES)


def _read_legacy_file(path: Path) -> dict[str, str]:
    try:
        if path.stat().st_size > MAX_CLASSIFICATION_FILE_BYTES:
            raise KnowledgeClassificationError(
                f"{LEGACY_CLASSIFICATION_FILENAME} 超过允许大小"
            )
        script = path.read_text("utf-8")
    except UnicodeDecodeError:
        raise KnowledgeClassificationError(
            f"{LEGACY_CLASSIFICATION_FILENAME} 不是有效 UTF-8 文本"
        ) from None
    except OSError as exc:
        raise KnowledgeClassificationError(
            f"{LEGACY_CLASSIFICATION_FILENAME} 无法读取：{exc}"
        ) from exc

    connection = sqlite3.connect(":memory:")
    try:
        # 只接受最小元数据 schema：逐条按语句白名单校验后再执行，不依赖
        # sqlite3 授权回调，避免 Python 3.10 与新版在回调清空语义上的差异。
        for statement in _legacy_statements(script):
            if not _allowed_legacy_statement(statement):
                raise KnowledgeClassificationError(
                    f"{LEGACY_CLASSIFICATION_FILENAME} 含不支持的语句，"
                    "只接受 classify 表的建表与插入"
                )
            try:
                connection.execute(statement)
            except sqlite3.DatabaseError as exc:
                raise KnowledgeClassificationError(
                    f"{LEGACY_CLASSIFICATION_FILENAME} 格式无效：{exc}"
                ) from None
        try:
            rows = connection.execute(
                "SELECT relative_path, category FROM classify"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise KnowledgeClassificationError(
                "classify.sql 缺少 classify(relative_path, category) 表或列："
                f"{exc}"
            ) from None
    finally:
        connection.close()

    result: dict[str, str] = {}
    for relative_path, category in rows:
        normalized_path = normalize_relative_path(relative_path)
        normalized_category = normalize_category(category)
        if normalized_category:
            result[normalized_path] = normalized_category
        else:
            result.pop(normalized_path, None)
    return result


def _read_database(path: Path) -> dict[str, str]:
    try:
        if path.stat().st_size > MAX_CLASSIFICATION_FILE_BYTES:
            raise KnowledgeClassificationError(
                f"{CLASSIFICATION_FILENAME} 超过允许大小"
            )
    except OSError as exc:
        raise KnowledgeClassificationError(
            f"{CLASSIFICATION_FILENAME} 无法读取：{exc}"
        ) from exc

    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError as exc:
        raise KnowledgeClassificationError(
            f"{CLASSIFICATION_FILENAME} 格式无效：{exc}"
        ) from None
    try:
        try:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(classify)").fetchall()
            }
            if not {"relative_path", "category"}.issubset(columns):
                raise KnowledgeClassificationError(
                    f"{CLASSIFICATION_FILENAME} 缺少 classify(relative_path, category) 表"
                )
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise KnowledgeClassificationError(
                    f"{CLASSIFICATION_FILENAME} 完整性检查失败"
                )
            rows = connection.execute(
                "SELECT relative_path, category FROM classify"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise KnowledgeClassificationError(
                f"{CLASSIFICATION_FILENAME} 格式无效：{exc}"
            ) from None
    finally:
        connection.close()

    result: dict[str, str] = {}
    for relative_path, category in rows:
        normalized_path = normalize_relative_path(relative_path)
        normalized_category = normalize_category(category)
        if normalized_category:
            if normalized_path in result:
                raise KnowledgeClassificationError(
                    f"{CLASSIFICATION_FILENAME} 存在重复路径绑定：{normalized_path}"
                )
            result[normalized_path] = normalized_category
        else:
            result.pop(normalized_path, None)
    return result


def _read_unlocked(root: Path) -> dict[str, str]:
    path = classification_path(root)
    if path.is_file():
        return _read_database(path)
    legacy_path = _legacy_classification_path(root)
    if legacy_path.is_file():
        return _read_legacy_file(legacy_path)
    return {}


def read_classifications(root: Path) -> dict[str, str]:
    with classification_lock(root):
        return _read_unlocked(Path(root))


def _normalized_mapping(mapping: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for relative_path, category in mapping.items():
        path = normalize_relative_path(relative_path)
        value = normalize_category(category)
        if value:
            normalized[path] = value
    return normalized


def write_classifications(root: Path, mapping: Mapping[str, str]) -> None:
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    normalized = _normalized_mapping(mapping)
    path = classification_path(base)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            prefix=f".{CLASSIFICATION_FILENAME}.",
            suffix=".tmp",
            dir=base,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        connection = sqlite3.connect(temporary, timeout=5.0)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(_SCHEMA)
            records = sorted(normalized.items(), key=lambda record: (record[0].casefold(), record[0]))
            connection.executemany(
                "INSERT INTO classify(relative_path, category) VALUES (?, ?)",
                records,
            )
            connection.commit()
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise KnowledgeClassificationError(
                f"{CLASSIFICATION_FILENAME} 写入失败：{exc}"
            ) from exc
        finally:
            connection.close()
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _migrate_legacy_unlocked(root: Path) -> None:
    base = Path(root)
    path = classification_path(base)
    legacy_path = _legacy_classification_path(base)
    if path.exists() or not legacy_path.is_file():
        return
    mapping = _read_legacy_file(legacy_path)
    write_classifications(base, mapping)
    try:
        legacy_path.unlink()
    except OSError as exc:
        raise KnowledgeClassificationError(
            f"{LEGACY_CLASSIFICATION_FILENAME} 迁移后无法删除：{exc}"
        ) from exc


def _reconcile_legacy_unlocked(root: Path) -> None:
    """Merge/remove a legacy SQL dump after the SQLite database exists."""

    base = Path(root)
    path = classification_path(base)
    legacy_path = _legacy_classification_path(base)
    if not path.is_file() or not legacy_path.is_file():
        return
    legacy_mapping = _read_legacy_file(legacy_path)
    current_mapping = _read_database(path)
    merged = dict(legacy_mapping)
    merged.update(current_mapping)
    if merged != current_mapping:
        write_classifications(base, merged)
    try:
        legacy_path.unlink()
    except OSError as exc:
        raise KnowledgeClassificationError(
            f"{LEGACY_CLASSIFICATION_FILENAME} 无法清理：{exc}"
        ) from exc


def ensure_classification_file(root: Path) -> Path:
    base = Path(root)
    path = classification_path(base)
    with classification_lock(base):
        if not path.exists() and _legacy_classification_path(base).is_file():
            _migrate_legacy_unlocked(base)
        if not path.exists():
            write_classifications(base, {})
        _reconcile_legacy_unlocked(base)
    return path


def set_classification(root: Path, relative_path: str, category: object) -> str:
    base = Path(root)
    normalized_path = normalize_relative_path(relative_path)
    normalized_category = normalize_category(category)
    with classification_lock(base):
        ensure_classification_file(base)
        mapping = _read_unlocked(base)
        if normalized_category:
            mapping[normalized_path] = normalized_category
        else:
            mapping.pop(normalized_path, None)
        write_classifications(base, mapping)
    return normalized_category or UNCATEGORIZED


def remove_classification(root: Path, relative_path: str) -> None:
    base = Path(root)
    normalized_path = normalize_relative_path(relative_path)
    with classification_lock(base):
        ensure_classification_file(base)
        mapping = _read_unlocked(base)
        if normalized_path in mapping:
            mapping.pop(normalized_path)
            write_classifications(base, mapping)


def rename_classification(root: Path, old_path: str, new_path: str) -> None:
    base = Path(root)
    old_relative = normalize_relative_path(old_path)
    new_relative = normalize_relative_path(new_path)
    with classification_lock(base):
        ensure_classification_file(base)
        mapping = _read_unlocked(base)
        category = mapping.pop(old_relative, None)
        if category:
            mapping[new_relative] = category
        write_classifications(base, mapping)


__all__ = [
    "CLASSIFICATION_FILENAME",
    "LEGACY_CLASSIFICATION_FILENAME",
    "KnowledgeClassificationError",
    "MAX_CATEGORY_LENGTH",
    "UNCATEGORIZED",
    "classification_path",
    "ensure_classification_file",
    "normalize_category",
    "normalize_relative_path",
    "read_classifications",
    "remove_classification",
    "rename_classification",
    "set_classification",
    "write_classifications",
]
