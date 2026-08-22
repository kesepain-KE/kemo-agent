"""SQLite-backed inbound-message idempotency state."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run.history import connection


class MessageStateError(RuntimeError):
    pass


_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(root: Path, user: str) -> threading.RLock:
    key = (str(root.resolve()).casefold(), user)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key_parts(key: str) -> tuple[str, str]:
    platform, separator, message_id = key.partition(":")
    return (platform if separator else "", message_id if separator else key)


def _error_text(error: dict[str, Any] | None) -> str | None:
    return (
        json.dumps(error, ensure_ascii=False, separators=(",", ":"))
        if error is not None
        else None
    )


def _error_value(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class ProcessedMessageStore:
    """Store inbound-message idempotency state in the user's history database."""

    def __init__(self, root: Path, user: str, *, max_entries: int = 2000) -> None:
        self.root = root.resolve()
        self.user = user
        self.max_entries = max(1, int(max_entries))
        self._lock = _lock_for(self.root, user)

    def claim(self, key: str) -> bool:
        return self.claim_many((key,))

    def claim_many(self, keys: tuple[str, ...]) -> bool:
        normalized = tuple(dict.fromkeys(str(key).strip() for key in keys if str(key).strip()))
        if not normalized:
            raise MessageStateError("消息幂等键不能为空")
        with self._lock:
            try:
                with connection(self.root, self.user, write=True) as database:
                    placeholders = ",".join("?" for _ in normalized)
                    if database.execute(
                        f"SELECT 1 FROM message_processed_messages WHERE dedupe_key IN ({placeholders}) LIMIT 1",
                        normalized,
                    ).fetchone() is not None:
                        return False
                    now = _now()
                    for key in normalized:
                        platform, message_id = _key_parts(key)
                        database.execute(
                            """
                            INSERT INTO message_processed_messages(
                                dedupe_key, platform, message_id, status,
                                claimed_at, updated_at, source, session_id, error_json
                            ) VALUES(?, ?, ?, 'processing', ?, ?, '', '', NULL)
                            """,
                            (key, platform, message_id, now, now),
                        )
                    self._trim(database)
                return True
            except sqlite3.Error as exc:
                raise MessageStateError(f"消息状态数据库不可用：{exc}") from exc

    def complete(self, key: str, *, status: str, error: dict[str, Any] | None = None) -> None:
        self.complete_many((key,), status=status, error=error)

    def complete_many(
        self,
        keys: tuple[str, ...],
        *,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise MessageStateError(f"终态无效：{status!r}")
        normalized = tuple(dict.fromkeys(str(key).strip() for key in keys if str(key).strip()))
        if not normalized:
            raise MessageStateError("消息幂等键不能为空")
        with self._lock:
            try:
                with connection(self.root, self.user, write=True) as database:
                    placeholders = ",".join("?" for _ in normalized)
                    existing = int(database.execute(
                        f"SELECT COUNT(*) FROM message_processed_messages WHERE dedupe_key IN ({placeholders})",
                        normalized,
                    ).fetchone()[0])
                    if existing != len(normalized):
                        missing = [key for key in normalized if database.execute(
                            "SELECT 1 FROM message_processed_messages WHERE dedupe_key=?", (key,)
                        ).fetchone() is None]
                        raise MessageStateError(f"消息尚未领取：{', '.join(missing)}")
                    database.execute(
                        f"UPDATE message_processed_messages SET status=?, updated_at=?, error_json=? WHERE dedupe_key IN ({placeholders})",
                        (status, _now(), _error_text(error), *normalized),
                    )
                    self._trim(database)
            except sqlite3.Error as exc:
                raise MessageStateError(f"消息状态数据库不可用：{exc}") from exc

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                with connection(self.root, self.user) as database:
                    row = database.execute(
                        "SELECT status, claimed_at, updated_at, error_json FROM message_processed_messages WHERE dedupe_key=?",
                        (key,),
                    ).fetchone()
            except sqlite3.Error as exc:
                raise MessageStateError(f"消息状态数据库不可用：{exc}") from exc
        if row is None:
            return None
        return {
            "status": str(row["status"]),
            "claimed_at": str(row["claimed_at"]),
            "updated_at": str(row["updated_at"]),
            "error": _error_value(row["error_json"]),
        }

    def recover_interrupted(self) -> list[str]:
        """Mark in-progress records failed; never replay possible side effects."""
        with self._lock:
            try:
                with connection(self.root, self.user, write=True) as database:
                    rows = database.execute(
                        "SELECT dedupe_key FROM message_processed_messages WHERE status='processing' ORDER BY updated_at, dedupe_key"
                    ).fetchall()
                    recovered = [str(row["dedupe_key"]) for row in rows]
                    if recovered:
                        database.execute(
                            """
                            UPDATE message_processed_messages
                            SET status='failed', updated_at=?, error_json=?
                            WHERE status='processing'
                            """,
                            (
                                _now(),
                                _error_text({
                                    "message": "宿主重启时消息仍在处理中；为避免重复副作用，不自动重放",
                                    "phase": "recovery",
                                }),
                            ),
                        )
                        self._trim(database)
                    return recovered
            except sqlite3.Error as exc:
                raise MessageStateError(f"消息状态数据库不可用：{exc}") from exc

    def _trim(self, database: sqlite3.Connection) -> None:
        total = int(database.execute(
            "SELECT COUNT(*) FROM message_processed_messages"
        ).fetchone()[0])
        overflow = total - self.max_entries
        if overflow <= 0:
            return
        database.execute(
            """
            DELETE FROM message_processed_messages WHERE dedupe_key IN (
                SELECT dedupe_key FROM message_processed_messages
                WHERE status != 'processing'
                ORDER BY updated_at, dedupe_key LIMIT ?
            )
            """,
            (overflow,),
        )
