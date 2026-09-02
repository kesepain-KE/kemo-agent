"""Detached App run transport with durable replay snapshots.

The Android HTTP connection is only a subscriber.  The broker owns the
upstream ``/api/chat`` stream until its terminal event, so closing or killing
the Android process never translates into a framework cancellation.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from upstream import UpstreamClient


ACTIVE_STATUSES = frozenset({"starting", "running", "cancelling"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
MAX_REPLAY_EVENTS = 20_000
DEFAULT_TERMINAL_RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_TERMINAL_RUN_LIMIT = 500
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


@dataclass(frozen=True)
class StoredEvent:
    event_id: int
    data: str
    created_at: int


class RunStore:
    """Small SQLite journal used only by the App bridge runtime."""

    def __init__(
        self,
        path: Path,
        *,
        retention_seconds: int = DEFAULT_TERMINAL_RETENTION_SECONDS,
        max_terminal_runs_per_user: int = DEFAULT_TERMINAL_RUN_LIMIT,
    ) -> None:
        self.path = path
        self.retention_seconds = max(60, int(retention_seconds))
        self.max_terminal_runs_per_user = max(1, int(max_terminal_runs_per_user))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_runs (
                    run_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    client_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    finished_at INTEGER NOT NULL DEFAULT 0,
                    last_event_id INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    request_json TEXT NOT NULL DEFAULT '{}',
                    delete_when_terminal INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_app_runs_user_status
                    ON app_runs(username, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS app_run_events (
                    run_id TEXT NOT NULL,
                    event_id INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(run_id, event_id),
                    FOREIGN KEY(run_id) REFERENCES app_runs(run_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS app_run_aliases (
                    alias_run_id TEXT PRIMARY KEY,
                    canonical_run_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    FOREIGN KEY(canonical_run_id) REFERENCES app_runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_app_run_aliases_canonical
                    ON app_run_aliases(canonical_run_id, username);
                """
            )
            columns = {
                str(row["name"])
                for row in self._connection.execute("PRAGMA table_info(app_runs)").fetchall()
            }
            if "delete_when_terminal" not in columns:
                self._connection.execute(
                    "ALTER TABLE app_runs ADD COLUMN delete_when_terminal "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            # A bridge restart necessarily closes the upstream HTTP stream.  Do
            # not advertise such a journal as live after startup.
            now = int(time.time())
            self._connection.execute(
                """UPDATE app_runs
                   SET status='interrupted', updated_at=?, finished_at=?,
                       error=CASE WHEN error='' THEN 'bridge_restarted' ELSE error END
                   WHERE status IN ('starting','running','cancelling')""",
                (now, now),
            )
            deferred = [
                str(row["run_id"])
                for row in self._connection.execute(
                    """SELECT run_id FROM app_runs
                       WHERE delete_when_terminal=1
                         AND status IN ('completed','failed','cancelled','interrupted')"""
                ).fetchall()
            ]
            self._delete_run_ids_locked(deferred)
            self._connection.commit()
            self._prune_locked(now=now)
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _resolve_run_id_locked(self, username: str, run_id: str) -> str:
        """Resolve a canonical run id while enforcing user ownership."""

        normalized = str(run_id or "").strip()
        if not normalized:
            raise KeyError(run_id)
        direct = self._connection.execute(
            "SELECT run_id, username FROM app_runs WHERE run_id=?",
            (normalized,),
        ).fetchone()
        if direct is not None:
            if str(direct["username"]) != username:
                raise PermissionError("run_user_mismatch")
            return normalized
        alias = self._connection.execute(
            "SELECT canonical_run_id, username FROM app_run_aliases WHERE alias_run_id=?",
            (normalized,),
        ).fetchone()
        if alias is None:
            raise KeyError(run_id)
        if str(alias["username"]) != username:
            raise PermissionError("run_user_mismatch")
        canonical = str(alias["canonical_run_id"])
        owner = self._connection.execute(
            "SELECT username FROM app_runs WHERE run_id=?",
            (canonical,),
        ).fetchone()
        if owner is None:
            raise KeyError(run_id)
        if str(owner["username"]) != username:
            raise PermissionError("run_user_mismatch")
        return canonical

    def canonical_id(self, username: str, run_id: str) -> str:
        with self._lock:
            return self._resolve_run_id_locked(username, run_id)

    def _register_alias_locked(
        self,
        username: str,
        alias_run_id: str,
        canonical_run_id: str,
    ) -> None:
        alias = str(alias_run_id or "").strip()
        canonical = str(canonical_run_id or "").strip()
        if (
            not alias
            or alias == canonical
            or not _RUN_ID_RE.fullmatch(alias)
            or not canonical
        ):
            return
        direct = self._connection.execute(
            "SELECT run_id FROM app_runs WHERE run_id=?",
            (alias,),
        ).fetchone()
        if direct is not None:
            # Never replace a real run id with an upstream-provided alias.
            return
        existing = self._connection.execute(
            "SELECT canonical_run_id, username FROM app_run_aliases WHERE alias_run_id=?",
            (alias,),
        ).fetchone()
        if existing is not None:
            return
        self._connection.execute(
            "INSERT INTO app_run_aliases(alias_run_id, canonical_run_id, username) VALUES(?,?,?)",
            (alias, canonical, username),
        )

    def create(self, username: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        run_id = str(payload.get("run_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        client_id = str(payload.get("client_id") or "").strip()
        if not run_id or not session_id:
            raise ValueError("run_id_and_session_id_required")
        with self._lock:
            existing = self._connection.execute(
                "SELECT * FROM app_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                value = dict(existing)
                if value["username"] != username:
                    raise PermissionError("run_user_mismatch")
                if value["session_id"] != session_id:
                    raise ValueError("run_session_mismatch")
                return value, False
            alias = self._connection.execute(
                "SELECT canonical_run_id, username FROM app_run_aliases WHERE alias_run_id=?",
                (run_id,),
            ).fetchone()
            if alias is not None:
                if str(alias["username"]) != username:
                    raise PermissionError("run_user_mismatch")
                canonical = self._connection.execute(
                    "SELECT * FROM app_runs WHERE run_id=?",
                    (str(alias["canonical_run_id"]),),
                ).fetchone()
                if canonical is None:
                    raise KeyError(run_id)
                value = dict(canonical)
                if value["session_id"] != session_id:
                    raise ValueError("run_session_mismatch")
                return value, False
            now = int(time.time())
            self._connection.execute(
                """INSERT INTO app_runs(
                       run_id,username,session_id,client_id,status,created_at,
                       updated_at,request_json
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    username,
                    session_id,
                    client_id,
                    "starting",
                    now,
                    now,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            self._connection.commit()
            return self.get(username, run_id), True

    def get(self, username: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                canonical = self._resolve_run_id_locked(username, run_id)
            except PermissionError as exc:
                # Keep reads indistinguishable from an unknown run so a
                # caller cannot probe another user's run ids.
                raise KeyError(run_id) from exc
            row = self._connection.execute(
                "SELECT * FROM app_runs WHERE run_id=? AND username=?",
                (canonical, username),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(row)

    def set_status(self, username: str, run_id: str, status: str, error: str = "") -> dict[str, Any]:
        if status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
            raise ValueError("invalid_run_status")
        now = int(time.time())
        finished_at = now if status in TERMINAL_STATUSES else 0
        with self._lock:
            canonical = self._resolve_run_id_locked(username, run_id)
            cursor = self._connection.execute(
                """UPDATE app_runs SET status=?,updated_at=?,finished_at=?,error=?
                   WHERE run_id=? AND username=?""",
                (status, now, finished_at, error[:500], canonical, username),
            )
            if cursor.rowcount != 1:
                raise KeyError(run_id)
            self._connection.commit()
        return self.get(username, run_id)

    def append(self, username: str, run_id: str, data: str) -> StoredEvent:
        now = int(time.time())
        terminal_status, error = _terminal_status(data)
        with self._lock:
            canonical = self._resolve_run_id_locked(username, run_id)
            row = self._connection.execute(
                "SELECT username,last_event_id,status FROM app_runs WHERE run_id=?",
                (canonical,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["username"] != username:
                raise PermissionError("run_user_mismatch")
            event_id = int(row["last_event_id"]) + 1
            self._connection.execute(
                "INSERT INTO app_run_events(run_id,event_id,data,created_at) VALUES(?,?,?,?)",
                (canonical, event_id, data, now),
            )
            continuation_id = _continuation_run_id(data)
            self._register_alias_locked(username, continuation_id, canonical)
            status = terminal_status or ("cancelling" if row["status"] == "cancelling" else "running")
            finished_at = now if terminal_status else 0
            self._connection.execute(
                """UPDATE app_runs
                   SET status=?,updated_at=?,finished_at=?,last_event_id=?,error=?
                   WHERE run_id=?""",
                (status, now, finished_at, event_id, error[:500], canonical),
            )
            self._connection.commit()
        return StoredEvent(event_id, data, now)

    def events_after(self, username: str, run_id: str, after: int) -> list[StoredEvent]:
        with self._lock:
            canonical = self._resolve_run_id_locked(username, run_id)
            rows = self._connection.execute(
                """SELECT event_id,data,created_at FROM app_run_events
                   WHERE run_id=? AND event_id>? ORDER BY event_id LIMIT ?""",
                (canonical, max(0, int(after)), MAX_REPLAY_EVENTS),
            ).fetchall()
        return [StoredEvent(int(row["event_id"]), str(row["data"]), int(row["created_at"])) for row in rows]

    def active(self, username: str, *, client_id: str = "", session_id: str = "") -> list[dict[str, Any]]:
        query = "SELECT * FROM app_runs WHERE username=? AND status IN ('starting','running','cancelling')"
        params: list[Any] = [username]
        if client_id:
            query += " AND client_id=?"
            params.append(client_id)
        if session_id:
            query += " AND session_id=?"
            params.append(session_id)
        query += " ORDER BY updated_at DESC"
        with self._lock:
            return [dict(row) for row in self._connection.execute(query, params).fetchall()]

    def _delete_run_ids_locked(self, run_ids: list[str]) -> list[str]:
        deleted: list[str] = []
        for offset in range(0, len(run_ids), 400):
            chunk = [str(value) for value in run_ids[offset : offset + 400] if value]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            self._connection.execute(
                f"DELETE FROM app_runs WHERE run_id IN ({placeholders})",
                chunk,
            )
            deleted.extend(chunk)
        return deleted

    def _prune_locked(self, *, now: int) -> list[str]:
        cutoff = now - self.retention_seconds
        run_ids = [
            str(row["run_id"])
            for row in self._connection.execute(
                """SELECT run_id FROM app_runs
                   WHERE status IN ('completed','failed','cancelled','interrupted')
                     AND finished_at > 0 AND finished_at <= ?""",
                (cutoff,),
            ).fetchall()
        ]
        users = [
            str(row["username"])
            for row in self._connection.execute(
                """SELECT DISTINCT username FROM app_runs
                   WHERE status IN ('completed','failed','cancelled','interrupted')"""
            ).fetchall()
        ]
        for username in users:
            run_ids.extend(
                str(row["run_id"])
                for row in self._connection.execute(
                    """SELECT run_id FROM app_runs
                       WHERE username=?
                         AND status IN ('completed','failed','cancelled','interrupted')
                       ORDER BY finished_at DESC, updated_at DESC, run_id DESC
                       LIMIT -1 OFFSET ?""",
                    (username, self.max_terminal_runs_per_user),
                ).fetchall()
            )
        return self._delete_run_ids_locked(list(dict.fromkeys(run_ids)))

    def prune(self, *, now: int | None = None) -> list[str]:
        with self._lock:
            deleted = self._prune_locked(now=int(time.time()) if now is None else int(now))
            self._connection.commit()
            return deleted

    def delete_session(self, username: str, session_id: str) -> list[str]:
        """Delete replay copies for terminal runs in one deleted conversation."""

        with self._lock:
            self._connection.execute(
                """UPDATE app_runs SET delete_when_terminal=1
                   WHERE username=? AND session_id=?
                     AND status IN ('starting','running','cancelling')""",
                (username, session_id),
            )
            run_ids = [
                str(row["run_id"])
                for row in self._connection.execute(
                    """SELECT run_id FROM app_runs
                       WHERE username=? AND session_id=?
                         AND status IN ('completed','failed','cancelled','interrupted')""",
                    (username, session_id),
                ).fetchall()
            ]
            deleted = self._delete_run_ids_locked(run_ids)
            self._connection.commit()
            return deleted

    def delete_user(self, username: str) -> list[str]:
        """Delete replay copies for every terminal run owned by one user."""

        with self._lock:
            self._connection.execute(
                """UPDATE app_runs SET delete_when_terminal=1
                   WHERE username=?
                     AND status IN ('starting','running','cancelling')""",
                (username,),
            )
            run_ids = [
                str(row["run_id"])
                for row in self._connection.execute(
                    """SELECT run_id FROM app_runs
                       WHERE username=?
                         AND status IN ('completed','failed','cancelled','interrupted')""",
                    (username,),
                ).fetchall()
            ]
            deleted = self._delete_run_ids_locked(run_ids)
            self._connection.commit()
            return deleted

    def delete_if_requested(self, username: str, run_id: str) -> bool:
        """Finish a deferred conversation deletion after its active run ends."""

        with self._lock:
            canonical = self._resolve_run_id_locked(username, run_id)
            row = self._connection.execute(
                "SELECT status,delete_when_terminal FROM app_runs "
                "WHERE run_id=? AND username=?",
                (canonical, username),
            ).fetchone()
            if (
                row is None
                or not bool(row["delete_when_terminal"])
                or str(row["status"]) not in TERMINAL_STATUSES
            ):
                return False
            self._delete_run_ids_locked([canonical])
            self._connection.commit()
            return True


class RunBroker:
    def __init__(self, upstream: UpstreamClient, store: RunStore) -> None:
        self.upstream = upstream
        self.store = store
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._conditions: dict[str, asyncio.Condition] = {}
        self._lock = asyncio.Lock()

    async def stop(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self.store.close()

    async def start(self, username: str, payload: dict[str, Any]) -> dict[str, Any]:
        record, created = self.store.create(username, payload)
        run_id = str(record["run_id"])
        async with self._lock:
            task = self._tasks.get(run_id)
            if created:
                self._conditions.setdefault(run_id, asyncio.Condition())
                task = asyncio.create_task(self._pump(username, run_id, payload), name=f"kemo-app-run-{run_id[:24]}")
                self._tasks[run_id] = task
            elif record["status"] in ACTIVE_STATUSES and (task is None or task.done()):
                # This can only be a stale journal from a previously terminated
                # broker process. Reposting the prompt would duplicate side effects.
                record = self.store.set_status(username, run_id, "interrupted", "bridge_run_not_attached")
        return record

    def snapshot(
        self,
        username: str,
        run_id: str,
        after: int = 0,
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        record = self.store.get(username, run_id)
        if session_id and str(record.get("session_id") or "") != str(session_id):
            raise KeyError(run_id)
        events = self.store.events_after(username, run_id, after)
        status = str(record["status"])
        try:
            request = json.loads(str(record.get("request_json") or "{}"))
        except (TypeError, ValueError):
            request = {}
        return {
            "run_id": run_id,
            "session_id": record["session_id"],
            "client_id": record["client_id"],
            "status": status,
            "recoverable": status in ACTIVE_STATUSES,
            "terminal": status in TERMINAL_STATUSES,
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "finished_at": record["finished_at"],
            "last_event_id": record["last_event_id"],
            "error": record["error"],
            "prompt": str(request.get("prompt") or "") if isinstance(request, dict) else "",
            "uploaded_files": request.get("uploaded_files", []) if isinstance(request, dict) and isinstance(request.get("uploaded_files"), list) else [],
            "events": [
                {"event_id": event.event_id, "data": event.data, "created_at": event.created_at}
                for event in events
            ],
        }

    def scope(self, username: str, run_id: str) -> dict[str, str]:
        """Return the durable ownership scope without loading replay events."""

        record = self.store.get(username, run_id)
        return {
            "run_id": str(record["run_id"]),
            "session_id": str(record["session_id"]),
        }

    def active(self, username: str, *, client_id: str = "", session_id: str = "") -> list[dict[str, Any]]:
        return [self.snapshot(username, str(item["run_id"]), int(item["last_event_id"])) for item in self.store.active(
            username,
            client_id=client_id,
            session_id=session_id,
        )]

    def mark_cancelling(self, username: str, run_id: str) -> dict[str, Any]:
        current = self.store.get(username, run_id)
        if str(current["status"]) in TERMINAL_STATUSES:
            return current
        return self.store.set_status(username, run_id, "cancelling")

    def _forget_runs(self, run_ids: list[str]) -> None:
        for run_id in run_ids:
            self._conditions.pop(run_id, None)

    def delete_session(self, username: str, session_id: str) -> int:
        deleted = self.store.delete_session(username, session_id)
        self._forget_runs(deleted)
        return len(deleted)

    def delete_user(self, username: str) -> int:
        deleted = self.store.delete_user(username)
        self._forget_runs(deleted)
        return len(deleted)

    def prune(self) -> int:
        deleted = self.store.prune()
        self._forget_runs(deleted)
        return len(deleted)

    async def stream(
        self,
        username: str,
        run_id: str,
        after: int = 0,
        *,
        session_id: str = "",
    ) -> AsyncIterator[StoredEvent | None]:
        try:
            canonical_run_id = self.store.canonical_id(username, run_id)
        except (KeyError, PermissionError):
            return
        if session_id:
            try:
                record = self.store.get(username, canonical_run_id)
            except (KeyError, PermissionError):
                return
            if str(record.get("session_id") or "") != str(session_id):
                return
        cursor = max(0, int(after))
        condition = self._conditions.setdefault(canonical_run_id, asyncio.Condition())
        while True:
            try:
                events = self.store.events_after(username, run_id, cursor)
            except KeyError:
                # A conversation deletion may retire the journal immediately
                # after its active run reaches a terminal state.
                return
            for event in events:
                cursor = event.event_id
                yield event
            try:
                current = self.store.get(username, run_id)
            except KeyError:
                return
            if str(current["status"]) in TERMINAL_STATUSES and cursor >= int(current["last_event_id"]):
                return
            try:
                async with condition:
                    await asyncio.wait_for(condition.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                yield None

    async def _pump(self, username: str, run_id: str, payload: dict[str, Any]) -> None:
        response: httpx.Response | None = None
        terminal = False
        try:
            response = await self.upstream.open_stream("POST", "/api/chat", json_body=payload, sse=True)
            # An explicit cancel can arrive while the upstream connection is being
            # established.  Do not let the later "stream opened" transition erase
            # that cancellation intent; the bridge must continue to report the run
            # as cancelling until the upstream terminal event arrives.
            current = self.store.get(username, run_id)
            if current is not None and current["status"] != "cancelling":
                self.store.set_status(username, run_id, "running")
            data_lines: list[str] = []

            async def dispatch() -> None:
                nonlocal terminal
                if not data_lines:
                    return
                data = "\n".join(data_lines)
                data_lines.clear()
                event = self.store.append(username, run_id, data)
                status, _ = _terminal_status(data)
                terminal = status is not None
                await self._notify(run_id)

            async for line in response.aiter_lines():
                if line == "":
                    await dispatch()
                    if terminal:
                        break
                elif line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").lstrip())
            await dispatch()
            if not terminal:
                await self._append_failure(username, run_id, "上游对话流在完成前中断")
        except asyncio.CancelledError:
            current = self.store.get(username, run_id)
            if str(current["status"]) not in TERMINAL_STATUSES:
                self.store.set_status(username, run_id, "interrupted", "bridge_stopped")
                await self._notify(run_id)
            raise
        except Exception as exc:
            await self._append_failure(username, run_id, f"上游对话运行失败：{type(exc).__name__}")
        finally:
            if response is not None:
                await response.aclose()
            async with self._lock:
                self._tasks.pop(run_id, None)
            await self._notify(run_id)
            if self.store.delete_if_requested(username, run_id):
                self._conditions.pop(run_id, None)
            self.prune()

    async def _append_failure(self, username: str, run_id: str, message: str) -> None:
        current = self.store.get(username, run_id)
        if str(current["status"]) in TERMINAL_STATUSES:
            return
        payload = json.dumps(
            {"type": "error", "error": {"message": message}},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.store.append(username, run_id, payload)
        await self._notify(run_id)

    async def _notify(self, run_id: str) -> None:
        condition = self._conditions.setdefault(run_id, asyncio.Condition())
        async with condition:
            condition.notify_all()


def _continuation_run_id(data: str) -> str:
    """Extract a long-task replacement run id from one upstream event."""

    try:
        value = json.loads(data)
    except (TypeError, ValueError):
        return ""
    if not isinstance(value, dict):
        return ""
    nested = value.get("data") if isinstance(value.get("data"), dict) else value
    event_type = str(
        value.get("type")
        or value.get("event")
        or nested.get("type")
        or nested.get("event")
        or ""
    )
    if event_type != "long_task_update":
        return ""
    metadata = nested.get("metadata") if isinstance(nested.get("metadata"), dict) else {}
    candidate = metadata.get("next_run_id") or nested.get("next_run_id") or value.get("next_run_id")
    rendered = str(candidate or "").strip()
    return rendered if _RUN_ID_RE.fullmatch(rendered) else ""


def _terminal_status(data: str) -> tuple[str | None, str]:
    if data.strip() == "[DONE]":
        return "completed", ""
    try:
        value = json.loads(data)
    except (TypeError, ValueError):
        return None, ""
    if not isinstance(value, dict):
        return None, ""
    nested = value.get("data") if isinstance(value.get("data"), dict) else value
    event_type = str(value.get("type") or value.get("event") or nested.get("type") or nested.get("event") or "")
    if event_type in {"done", "completed"}:
        metadata = nested.get("metadata") if isinstance(nested.get("metadata"), dict) else {}
        raw_status = metadata.get("status")
        if raw_status is None:
            raw_status = nested.get("status")
        if raw_status is None and nested is not value:
            raw_status = value.get("status")
        status = str(raw_status or "").strip().casefold()
        if status in {"cancelled", "canceled", "cancelling"}:
            return "cancelled", ""
        if status in {"failed", "error"}:
            return "failed", f"upstream_terminal_status:{status}"
        if status in {"interrupted", "aborted", "stopped", "limited", "paused"}:
            return "interrupted", f"upstream_terminal_status:{status}"
        if status in {"", "completed", "success", "succeeded", "done"}:
            return "completed", ""
        # Do not silently advertise success for a newly introduced terminal
        # status until the bridge contract explicitly maps it.
        return "failed", f"upstream_terminal_status:{status[:64]}"
    if event_type == "error":
        error_value = nested.get("error")
        if isinstance(error_value, dict):
            message = str(error_value.get("message") or error_value.get("detail") or "")
        else:
            message = str(error_value or nested.get("message") or "")
        return "failed", message
    return None, ""
