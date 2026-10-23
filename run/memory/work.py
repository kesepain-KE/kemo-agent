"""Durable, user-local work ledger shared by extraction, archive and skills."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any
from contextlib import contextmanager
from datetime import timedelta
import uuid

from run.memory.store import connection


def digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             default=str).encode("utf-8")).hexdigest()


class WorkLedger:
    def __init__(self, root, user):
        self.root, self.user = root, user

    def get(self, key: str) -> dict | None:
        with connection(self.root, self.user) as db:
            row = db.execute("SELECT * FROM memory_work_items WHERE work_key=?", (key,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        result["source"] = json.loads(result.pop("source_json"))
        return result

    def require(self, key: str) -> dict:
        item = self.get(key)
        if item is None:
            raise KeyError(f"Memory work item is missing: {key}")
        return item

    def enqueue(self, kind: str, key: str, payload: dict, source: dict | None = None):
        from run.memory import iso, utc_now
        stamp = iso(utc_now())
        with connection(self.root, self.user, write=True) as db:
            db.execute(
                """INSERT OR IGNORE INTO memory_work_items(
                    work_type, work_key, payload_json, source_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?)""",
                (kind, key, json.dumps(payload, ensure_ascii=False),
                 json.dumps(source or {}, ensure_ascii=False), stamp, stamp))
        return self.require(key)

    def save(self, key: str, payload: dict, *, completed: bool = False,
             error: str | None = None, lease_token: str | None = None):
        from run.memory import iso, utc_now
        with connection(self.root, self.user, write=True) as db:
            result = db.execute(
                """UPDATE memory_work_items SET payload_json=?, status=?, attempts=attempts+1,
                    last_error=?, updated_at=? WHERE work_key=?
                    AND (? IS NULL OR lease_token=?)""",
                (json.dumps(payload, ensure_ascii=False), "completed" if completed else "pending",
                 error, iso(utc_now()), key, lease_token, lease_token))
            if result.rowcount != 1:
                raise RuntimeError("记忆待办租约已变化，拒绝陈旧处理结果")

    @contextmanager
    def lease(self, key: str):
        from run.memory import iso, utc_now
        now, token = utc_now(), uuid.uuid4().hex
        with connection(self.root, self.user, write=True) as db:
            acquired = db.execute(
                """UPDATE memory_work_items SET lease_token=?, lease_until=?
                   WHERE work_key=? AND (lease_token IS NULL OR lease_until<=?)""",
                (token, iso(now + timedelta(minutes=15)), key, iso(now))).rowcount
        if not acquired:
            from run.memory.extraction import MemoryWorkPending
            raise MemoryWorkPending("同一提取待办正在处理，保留源游标等待租约释放")
        try:
            yield token
        finally:
            with connection(self.root, self.user, write=True) as db:
                db.execute("UPDATE memory_work_items SET lease_token=NULL, lease_until=NULL "
                           "WHERE work_key=? AND lease_token=?", (key, token))

    def pending(self, kind: str, limit: int = 40) -> list[dict]:
        with connection(self.root, self.user) as db:
            keys = [r[0] for r in db.execute(
                """SELECT work_key FROM memory_work_items
                   WHERE work_type=? AND status!='completed' ORDER BY item_id LIMIT ?""",
                (kind, limit)).fetchall()]
        return [self.require(key) for key in keys]
