"""Atomic, user-scoped deletion of history across all entry channels."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import time

from run.history.history_models import HistoryError, _now
from run.history.index_core import index_lock
from run.history.store_core import connection, database_path
from run.history.web_lifecycle import _lease_client_key, _lease_scope_sql
from run.history.window_deletion import _delete_session_windows


def delete_user_history(
    root: Path, user: str, *, client_id: str = ""
) -> tuple[int, int]:
    """Delete the entire snapshot or fail without deleting any of it.

    Session identity includes source. Take non-blocking in-process execution
    locks and recheck durable run/claim/presence state in the write transaction.
    Tombstones also fence writers already in flight in another process.
    """
    from run.conversation import session_lock

    if not database_path(root, user).is_file():
        return 0, 0
    with index_lock(root, user), ExitStack() as locks:
        with connection(root, user, write=True) as database:
            targets = database.execute(
                """SELECT source, session_id FROM history_sessions
                UNION SELECT source, session_id FROM history_windows
                UNION SELECT source, session_id FROM history_active_sessions"""
            ).fetchall()
            for target in targets:
                source, session_id = str(target["source"]), str(target["session_id"])
                lock = session_lock(root, user, source, session_id)
                if not lock.acquire(blocking=False):
                    raise HistoryError("存在正在运行的会话，结束当前响应后再全部删除")
                locks.callback(lock.release)
            if database.execute(
                """SELECT 1 FROM history_sessions
                WHERE run_state IN ('running', 'queued', 'pending', 'stopping')
                   OR memory_status='processing' OR summary_status='processing'
                LIMIT 1"""
            ).fetchone() is not None:
                raise HistoryError("存在正在运行或处理中的会话，结束后再全部删除")
            for target in targets:
                source, session_id = str(target["source"]), str(target["session_id"])
                if source not in {"web", "app"}:
                    continue
                scope_sql, scope_value = _lease_scope_sql(source)
                # Only this browser's own lease may be disregarded, not an App
                # device that happens to use the same unscoped client id.
                excluded = _lease_client_key("web", client_id) if client_id else ""
                if database.execute(
                    f"""SELECT 1 FROM history_web_leases l
                    WHERE l.session_id=? AND {scope_sql}
                      AND l.expires_at>? AND l.released=0 AND l.client_id!=? LIMIT 1""",
                    (session_id, scope_value, time.time(), excluded),
                ).fetchone() is not None:
                    raise HistoryError("存在其他页面或设备正在使用的对话，关闭后再全部删除")
            deleted_windows = sum(
                _delete_session_windows(
                    database, str(target["source"]), str(target["session_id"])
                )
                for target in targets
            )
            database.execute("DELETE FROM history_active_sessions")
            database.execute("DELETE FROM history_sessions")
            database.execute("DELETE FROM history_web_leases")
            database.execute(
                """INSERT INTO history_meta(key, value) VALUES('registry_revision', '1')
                ON CONFLICT(key) DO UPDATE SET
                value=CAST(COALESCE(NULLIF(history_meta.value, ''), '0') AS INTEGER) + 1"""
            )
            database.execute(
                "INSERT OR REPLACE INTO history_meta(key, value) VALUES('registry_updated_at', ?)",
                (_now(),),
            )
    return len(targets), deleted_windows
