"""SQLite-backed structured runtime logs.

The human-readable JSONL/Markdown files remain a compatibility and export
format.  New records are written to this store as well, while the web layer
can import legacy files once (or when their file fingerprint changes).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterator
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
LOG_DB_RELATIVE = Path("runtime") / "logs.sqlite3"
DEFAULT_RETENTION_DAYS = 90
_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()
_READY_PATHS: set[str] = set()

_MESSAGE_HEADING = re.compile(
    r"(?m)^##\s+(?P<timestamp>[^|\r\n]+)\s*\|\s*"
    r"(?P<chat_type>[^|\r\n]+)\s*\|\s*(?P<chat_id>[^\r\n]+)\s*$"
)
_MESSAGE_INBOUND = re.compile(
    r"\*\*入站\*\*：(?P<content>.*?)(?=\n\s*-\s*附件：|\n\*\*出站\*\*：|\n---|\Z)",
    re.DOTALL,
)
_MESSAGE_OUTBOUND = re.compile(r"\*\*出站\*\*：(?P<content>.*?)(?=\n\s*-\s*出站附件：|\n---|\Z)", re.DOTALL)
_MESSAGE_ATTACHMENT = re.compile(
    r"(?m)^\s*-\s*附件：(?P<name>.+?)\s+\((?P<mime>[^,]+),\s*(?P<size>\d+)\s+bytes\)\s*$"
)
_MESSAGE_OUTBOUND_ATTACHMENT = re.compile(
    r"(?m)^\s*-\s*出站附件：(?P<name>.+?)\s+\((?P<path>[^\r\n]+)\)\s*$"
)


class LogStoreError(RuntimeError):
    """Structured log persistence failed."""


def _store_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _epoch_ms(value: Any) -> int:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING)
    return int(parsed.timestamp() * 1000)


def _fingerprint(*values: Any) -> str:
    payload = "\x1f".join(str(value or "") for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LogStore:
    """Cross-platform SQLite store for structured runtime events."""

    def __init__(self, root: Path, *, retention_days: int | None = None) -> None:
        self.root = root.resolve()
        self.path = self.root / LOG_DB_RELATIVE
        self._lock = _store_lock(self.path)
        self.retention_days = self._configured_retention(retention_days)

    @staticmethod
    def _configured_retention(value: int | None) -> int:
        if value is not None:
            return max(0, int(value))
        raw = os.environ.get("KEMO_LOG_RETENTION_DAYS", "90")
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return DEFAULT_RETENTION_DAYS

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            database_existed = self.path.is_file()
            connection = sqlite3.connect(self.path, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            with self._lock:
                ready_key = str(self.path.resolve()).casefold()
                if not database_existed:
                    _READY_PATHS.discard(ready_key)
                if ready_key not in _READY_PATHS:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS log_meta (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS cron_execution_logs (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            event_key TEXT NOT NULL UNIQUE,
                            occurred_at TEXT NOT NULL,
                            occurred_at_ms INTEGER NOT NULL,
                            user TEXT NOT NULL,
                            task_id TEXT NOT NULL,
                            status TEXT NOT NULL,
                            duration_ms INTEGER NOT NULL DEFAULT 0,
                            result_json TEXT NOT NULL DEFAULT '{}',
                            error_json TEXT,
                            created_at_ms INTEGER NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_cron_user_time
                            ON cron_execution_logs(user, occurred_at_ms DESC, id DESC);
                        CREATE INDEX IF NOT EXISTS idx_cron_task_time
                            ON cron_execution_logs(task_id, occurred_at_ms DESC, id DESC);
                        CREATE TABLE IF NOT EXISTS message_route_logs (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            event_key TEXT NOT NULL UNIQUE,
                            occurred_at TEXT NOT NULL,
                            occurred_at_ms INTEGER NOT NULL,
                            user TEXT NOT NULL,
                            machine_id TEXT NOT NULL,
                            platform TEXT NOT NULL,
                            chat_type TEXT NOT NULL,
                            chat_id TEXT NOT NULL,
                            direction TEXT NOT NULL,
                            kind TEXT NOT NULL,
                            content TEXT NOT NULL DEFAULT '',
                            file_path TEXT,
                            mime TEXT,
                            size INTEGER,
                            success INTEGER NOT NULL DEFAULT 1,
                            source TEXT NOT NULL,
                            created_at_ms INTEGER NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_message_machine_time
                            ON message_route_logs(machine_id, occurred_at_ms DESC, id DESC);
                        CREATE INDEX IF NOT EXISTS idx_message_user_time
                            ON message_route_logs(user, occurred_at_ms DESC, id DESC);
                        """
                    )
                    connection.execute("PRAGMA user_version = 1")
                    try:
                        self.path.parent.chmod(0o700)
                        self.path.chmod(0o600)
                    except OSError:
                        pass
                    _READY_PATHS.add(ready_key)
                yield connection
            connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise LogStoreError(f"日志数据库不可用：{exc}") from exc
        finally:
            try:
                connection.close()  # type: ignore[union-attr]
            except (UnboundLocalError, AttributeError):
                pass

    def _prune(self, connection: sqlite3.Connection) -> None:
        if self.retention_days <= 0:
            return
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        last = connection.execute(
            "SELECT value FROM log_meta WHERE key = 'last_pruned_ms'"
        ).fetchone()
        if last is not None and now_ms - int(last[0] or 0) < 86_400_000:
            return
        cutoff = int((datetime.now(timezone.utc) - timedelta(days=self.retention_days)).timestamp() * 1000)
        connection.execute("DELETE FROM cron_execution_logs WHERE occurred_at_ms < ?", (cutoff,))
        connection.execute("DELETE FROM message_route_logs WHERE occurred_at_ms < ?", (cutoff,))
        connection.execute(
            "INSERT INTO log_meta(key, value) VALUES('last_pruned_ms', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(now_ms),),
        )

    def append_cron(self, record: dict[str, Any]) -> None:
        self.append_cron_records([record])

    def append_cron_records(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        with self._connection() as connection:
            for record in records:
                occurred_at = str(record.get("executed_at") or datetime.now(BEIJING).isoformat())
                user = str(record.get("user") or "")
                task_id = str(record.get("task_id") or "")
                status = str(record.get("status") or "unknown")
                result = _json_object(record.get("result"))
                error = record.get("error") if isinstance(record.get("error"), dict) else None
                event_key = _fingerprint(
                    "cron", user, task_id, occurred_at, status,
                    record.get("duration_ms"), _json_text(result),
                    _json_text(error) if error is not None else "",
                )
                connection.execute(
                    """INSERT OR IGNORE INTO cron_execution_logs
                    (event_key, occurred_at, occurred_at_ms, user, task_id, status,
                     duration_ms, result_json, error_json, created_at_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_key, occurred_at, _epoch_ms(occurred_at), user, task_id,
                        status, max(0, int(record.get("duration_ms") or 0)),
                        _json_text(result), _json_text(error) if error is not None else None,
                        int(datetime.now(timezone.utc).timestamp() * 1000),
                    ),
                )
            self._prune(connection)

    def list_cron(self, user: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT event_key, occurred_at, user, task_id, status, duration_ms,
                          result_json, error_json
                   FROM cron_execution_logs WHERE user = ?
                   ORDER BY occurred_at_ms DESC, id DESC LIMIT ?""",
                (user, max(1, min(5000, int(limit)))),
            ).fetchall()
        result = []
        for row in rows:
            result.append({
                "id": f"{row['task_id']}:{row['event_key']}",
                "task_id": row["task_id"], "executed_at": row["occurred_at"],
                "status": row["status"], "duration_ms": int(row["duration_ms"] or 0),
                "result": _json_object(json.loads(row["result_json"] or "{}")),
                "error": json.loads(row["error_json"]) if row["error_json"] else None,
                "source": "execution_log",
            })
        return result

    def append_message_entries(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        with self._connection() as connection:
            for entry in entries:
                event_key = _fingerprint(
                    "message", entry.get("machine_id"), entry.get("user"),
                    entry.get("occurred_at"), entry.get("chat_type"),
                    entry.get("chat_id"), entry.get("direction"), entry.get("kind"),
                    _compact_text(entry.get("content")), entry.get("file_path"), entry.get("mime"),
                    entry.get("size"), entry.get("success"),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO message_route_logs
                    (event_key, occurred_at, occurred_at_ms, user, machine_id, platform,
                     chat_type, chat_id, direction, kind, content, file_path, mime,
                     size, success, source, created_at_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_key, str(entry.get("occurred_at") or ""),
                        _epoch_ms(entry.get("occurred_at")), str(entry.get("user") or ""),
                        str(entry.get("machine_id") or ""), str(entry.get("platform") or ""),
                        str(entry.get("chat_type") or ""), str(entry.get("chat_id") or ""),
                        str(entry.get("direction") or ""), str(entry.get("kind") or ""),
                        str(entry.get("content") or ""), entry.get("file_path"),
                        entry.get("mime"), entry.get("size"),
                        1 if entry.get("success", True) else 0,
                        str(entry.get("source") or "runtime/logs.sqlite3"),
                        int(datetime.now(timezone.utc).timestamp() * 1000),
                    ),
                )
            self._prune(connection)

    def list_messages(self, machine_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT event_key, occurred_at, chat_type, chat_id, direction, kind,
                          content, file_path, mime, size, success, source
                   FROM message_route_logs WHERE machine_id = ?
                   ORDER BY occurred_at_ms DESC, id DESC LIMIT ?""",
                (machine_id, max(1, min(5000, int(limit)))),
            ).fetchall()
        return [
            {
                "timestamp": row["occurred_at"], "chat_type": row["chat_type"],
                "chat_id": row["chat_id"], "source": row["source"],
                "id": f"{machine_id}:{row['event_key']}",
                "direction": row["direction"], "kind": row["kind"],
                "content": _compact_text(row["content"]),
                "file_path": row["file_path"], "success": bool(row["success"]),
                **({"mime": row["mime"]} if row["mime"] else {}),
                **({"size": int(row["size"])} if row["size"] is not None else {}),
            }
            for row in rows
        ]

    def count_messages(self, machine_id: str, *, date_prefix: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM message_route_logs WHERE machine_id = ?"
        parameters: tuple[Any, ...] = (machine_id,)
        if date_prefix:
            query += " AND occurred_at LIKE ?"
            parameters = (machine_id, f"{date_prefix}%")
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return max(0, int(row[0] if row is not None else 0))

    def delete_message_logs(self, machine_id: str, *, user: str) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM message_route_logs WHERE machine_id = ? AND user = ?",
                (machine_id, user),
            )
        return max(0, int(cursor.rowcount or 0))

    def mark_legacy_source(self, source: Path) -> None:
        stat = source.stat()
        key = f"legacy:{source.resolve()}"
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO log_meta(key, value) VALUES(?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, f"{stat.st_mtime_ns}:{stat.st_size}"),
            )

    def legacy_source_changed(self, source: Path) -> bool:
        try:
            stat = source.stat()
        except OSError:
            return False
        key = f"legacy:{source.resolve()}"
        with self._connection() as connection:
            row = connection.execute("SELECT value FROM log_meta WHERE key = ?", (key,)).fetchone()
        return row is None or row[0] != f"{stat.st_mtime_ns}:{stat.st_size}"

    def migrate_cron_logs(self, directory: Path) -> None:
        for path in sorted(directory.glob("*.jsonl")) if directory.is_dir() else ():
            if not self.legacy_source_changed(path):
                continue
            records: list[dict[str, Any]] = []
            try:
                for line in path.read_text("utf-8").splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        records.append(item)
            except (OSError, UnicodeError):
                continue
            self.append_cron_records(records)
            try:
                self.mark_legacy_source(path)
            except OSError:
                pass

    def migrate_message_logs(
        self, directory: Path, *, machine_id: str, user: str, platform: str, files_root: str
    ) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.md")):
            if not self.legacy_source_changed(path):
                continue
            try:
                content = path.read_text("utf-8-sig")
            except (OSError, UnicodeError):
                continue
            entries: list[dict[str, Any]] = []
            headings = list(_MESSAGE_HEADING.finditer(content))
            source = path.as_posix()
            try:
                source = path.relative_to(self.root).as_posix()
            except ValueError:
                pass
            for index, heading in enumerate(headings):
                end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
                block = content[heading.end():end]
                common = {
                    "occurred_at": heading.group("timestamp").strip(),
                    "user": user, "machine_id": machine_id, "platform": platform,
                    "chat_type": heading.group("chat_type").strip(),
                    "chat_id": heading.group("chat_id").strip(), "source": source,
                }
                inbound = _MESSAGE_INBOUND.search(block)
                if inbound and _compact_text(inbound.group("content")) not in {"", "[仅附件]"}:
                    entries.append({**common, "direction": "receive", "kind": "text", "content": inbound.group("content"), "success": True})
                for attachment in _MESSAGE_ATTACHMENT.finditer(block):
                    name = _compact_text(attachment.group("name"))
                    entries.append({**common, "direction": "receive", "kind": "file", "content": name, "file_path": f"{files_root}/{name}", "mime": attachment.group("mime").strip(), "size": int(attachment.group("size")), "success": True})
                outbound = _MESSAGE_OUTBOUND.search(block)
                if outbound and _compact_text(outbound.group("content")):
                    text = outbound.group("content")
                    entries.append({**common, "direction": "send", "kind": "system" if _compact_text(text).startswith("处理失败：") else "text", "content": text, "success": not _compact_text(text).startswith("处理失败：")})
                for attachment in _MESSAGE_OUTBOUND_ATTACHMENT.finditer(block):
                    entries.append({**common, "direction": "send", "kind": "file", "content": _compact_text(attachment.group("name")), "file_path": attachment.group("path").strip(), "success": True})
            self.append_message_entries(entries)
            try:
                self.mark_legacy_source(path)
            except OSError:
                pass
