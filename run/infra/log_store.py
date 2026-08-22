"""SQLite-backed structured runtime logs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
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
                        CREATE TABLE IF NOT EXISTS message_route_state (
                            machine_id TEXT PRIMARY KEY,
                            user TEXT NOT NULL,
                            platform TEXT NOT NULL,
                            schema_version INTEGER NOT NULL,
                            health TEXT NOT NULL,
                            last_check TEXT,
                            last_message_at TEXT,
                            error TEXT,
                            latency_ms INTEGER,
                            messages_received_today INTEGER NOT NULL DEFAULT 0,
                            messages_sent_today INTEGER NOT NULL DEFAULT 0,
                            input_status TEXT NOT NULL,
                            input_restart_count INTEGER NOT NULL DEFAULT 0,
                            input_last_restart_at TEXT,
                            input_error TEXT,
                            extra_json TEXT NOT NULL DEFAULT '{}',
                            updated_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_message_route_state_user
                            ON message_route_state(user, platform);
                        """
                    )
                    connection.execute("PRAGMA user_version = 2")
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
                "user": row["user"], "task_id": row["task_id"],
                "executed_at": row["occurred_at"],
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

    def delete_message_route_state(self, machine_id: str, *, user: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM message_route_state WHERE machine_id=? AND user=?",
                (machine_id, user),
            )
        return cursor.rowcount > 0

    def read_message_route_state(self, machine_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM message_route_state WHERE machine_id=?",
                (machine_id,),
            ).fetchone()
        if row is None:
            return None
        extra = _json_object(json.loads(row["extra_json"] or "{}"))
        return {
            **extra,
            "schema_version": int(row["schema_version"]),
            "health": str(row["health"]),
            "last_check": row["last_check"],
            "last_message_at": row["last_message_at"],
            "error": row["error"],
            "latency_ms": int(row["latency_ms"]) if row["latency_ms"] is not None else None,
            "messages_received_today": int(row["messages_received_today"] or 0),
            "messages_sent_today": int(row["messages_sent_today"] or 0),
            "input_status": str(row["input_status"]),
            "input_restart_count": int(row["input_restart_count"] or 0),
            "input_last_restart_at": row["input_last_restart_at"],
            "input_error": row["input_error"],
        }

    def write_message_route_state(
        self,
        machine_id: str,
        *,
        user: str,
        platform: str,
        state: dict[str, Any],
    ) -> None:
        standard = {
            "schema_version", "health", "last_check", "last_message_at",
            "error", "latency_ms", "messages_received_today",
            "messages_sent_today", "input_status", "input_restart_count",
            "input_last_restart_at", "input_error",
        }
        extra = {key: value for key, value in state.items() if key not in standard}
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO message_route_state(
                    machine_id, user, platform, schema_version, health,
                    last_check, last_message_at, error, latency_ms,
                    messages_received_today, messages_sent_today, input_status,
                    input_restart_count, input_last_restart_at, input_error,
                    extra_json, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(machine_id) DO UPDATE SET
                    user=excluded.user,
                    platform=excluded.platform,
                    schema_version=excluded.schema_version,
                    health=excluded.health,
                    last_check=excluded.last_check,
                    last_message_at=excluded.last_message_at,
                    error=excluded.error,
                    latency_ms=excluded.latency_ms,
                    messages_received_today=excluded.messages_received_today,
                    messages_sent_today=excluded.messages_sent_today,
                    input_status=excluded.input_status,
                    input_restart_count=excluded.input_restart_count,
                    input_last_restart_at=excluded.input_last_restart_at,
                    input_error=excluded.input_error,
                    extra_json=excluded.extra_json,
                    updated_at=excluded.updated_at
                """,
                (
                    machine_id, user, platform,
                    max(1, int(state.get("schema_version") or 1)),
                    str(state.get("health") or "unknown"), state.get("last_check"),
                    state.get("last_message_at"),
                    None if state.get("error") is None else str(state.get("error")),
                    state.get("latency_ms"),
                    max(0, int(state.get("messages_received_today") or 0)),
                    max(0, int(state.get("messages_sent_today") or 0)),
                    str(state.get("input_status") or "unknown"),
                    max(0, int(state.get("input_restart_count") or 0)),
                    state.get("input_last_restart_at"),
                    None if state.get("input_error") is None else str(state.get("input_error")),
                    _json_text(extra), datetime.now(timezone.utc).isoformat(),
                ),
            )
