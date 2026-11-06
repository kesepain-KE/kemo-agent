"""Durable, conversation-scoped business child tasks (not maintenance jobs).

The scheduler owns execution truth. A provider response is not a task terminal:
required children and unacknowledged notifications form a completion barrier.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
import threading
import uuid
import weakref
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from provider.protocol.diagnostics import redact_diagnostic_text
from run.config import user_dir, _contains_reparse_point
from run.agents.public_trace import public_tool_data

_OWNER = uuid.uuid4().hex
_SIGNAL = threading.Condition()
_LIVE = {"queued", "running", "timed_out_running", "cancelling"}
_FINAL = {"completed", "limited", "partial", "failed", "cancelled", "interrupted", "timed_out", "tool_limit"}
# The main loop can be waiting for the dispatch tool itself to finish preparing
# a child. Waiting is an open execution phase, not a terminal/cancellation fence.
_OPEN_PARENT = frozenset({"running", "waiting_children", "merging"})
_DB_LOCKS_GUARD = threading.Lock()
_DB_LOCKS: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()


def _database_lock(path: Path):
    # A per-database lock covers validation through SQLite close. Without it a
    # peer connection can delete a WAL/SHM between lstat and reparse inspection
    # on Windows. Keep the fail-closed link checks, not a permissive retry.
    key = os.path.normcase(str(path.absolute()))
    with _DB_LOCKS_GUARD:
        lock = _DB_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _DB_LOCKS[key] = lock
        return lock


def register_owned_task(runner, task, ownership):
    if ownership is not None:
        ledger = TaskLedger(runner.root, runner.user)
        ledger.register(task, ownership)
        task.ledger = ledger


def task_progress(task, fallback):
    callback = task.event_callback or fallback
    if task.ledger is None:
        return callback

    def progress(event):
        if (event.metadata or {}).get("task_id") == task.id:
            task.progress = {k: event.metadata[k] for k in ("status", "tool_name", "iteration", "budget", "usage")
                             if k in event.metadata}
            if isinstance(task.progress.get("usage"), dict):
                task.progress["usage"] = {k: v for k, v in task.progress["usage"].items()
                                          if isinstance(v, (int, float)) and not isinstance(v, bool)}
            event.metadata["run_count"] = task.run_count
            if not task.ledger.event(task.id, event.metadata or {}):
                return
        if callback is not None:
            callback(event)
    return progress


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe(value: Any, depth: int = 0, _budget: list[int] | None = None) -> Any:
    """Bounded public projection; never include raw reasoning or credentials."""
    budget = _budget if _budget is not None else [16000, 512]
    budget[1] -= 1
    if depth > 8 or min(budget) <= 0:
        return "[truncated]"
    if isinstance(value, dict):
        result = {}
        for k, v in list(value.items())[:80]:
            if min(budget) <= 0:
                result["truncated"] = True
                break
            if any(word in str(k).lower() for word in (
                "reasoning", "think", "prompt", "secret", "password", "token",
                "credential", "authorization", "cookie", "private_key", "api_key",
                "metadata", "arguments", "raw_text", "system",
            )):
                continue
            result[str(k)[:160]] = safe(v, depth + 1, budget)
        return result
    if isinstance(value, (list, tuple)):
        items = []
        for v in value[:80]:
            if min(budget) <= 0:
                items.append("[truncated]")
                break
            items.append(safe(v, depth + 1, budget))
        return items
    if isinstance(value, str):
        text = redact_diagnostic_text(value[:max(0, budget[0])])
        budget[0] -= len(text)
        return text + ("…[truncated]" if len(value) > len(text) and budget[0] <= 0 else "")
    return value if value is None or isinstance(value, (bool, int, float)) else None


class SessionExecutionBusyError(RuntimeError):
    """A closed response still owns execution in this exact conversation."""
    retryable = False


def ensure_session_execution_available(root, user, source, session_id):
    from run.config import validate_user_name
    if not (Path(root) / "users" / validate_user_name(user) / "runtime" / "agent_tasks.sqlite3").exists():
        return
    ledger = TaskLedger(root, user)
    if not ledger.path.exists():
        return
    with ledger.db() as db:
        rows = db.execute(
            """SELECT * FROM agent_tasks WHERE parent='' AND source=? AND session=?
               AND json_extract(record, '$.response_closed')=1
               AND json_extract(record, '$.execution_live')=1""", (source, session_id),
        ).fetchall()
    for row in rows:
        parent = ledger.decode(row)
        with ledger.db() as db:
            children = db.execute("SELECT * FROM agent_tasks WHERE parent=?", (parent["id"],)).fetchall()
        for child in children:
            current = ledger._reconciled(child)
            if (current and current.get("cardstream_integration_id")
                    and current["status"] in _FINAL and not current.get("execution_live")
                    and not current.get("card_result_seq")):
                ledger._append_card_result(current["id"], source, session_id)
        with ledger.db(write=True) as db:
            ledger._refresh_closed_parent(db, parent["id"])
        parent = ledger.get(parent["id"], source, session_id)
        if parent and parent.get("execution_live"):
            raise SessionExecutionBusyError(
                "本会话仍有尚未退出的子任务或 required Shell 作业，执行保护仍在；"
                "请待实际退出后再发送新请求。"
            )


class TaskLedger:
    def __init__(self, root: Path | str, user: str):
        base = user_dir(user, Path(root))
        directory = base / "runtime"
        self.path = directory / "agent_tasks.sqlite3"
        self.root, self.base = Path(root), base
        if (_contains_reparse_point(directory, Path(root))
                or _contains_reparse_point(self.path, base)
                or (self.path.exists() and self.path.stat().st_nlink != 1)):
            raise ValueError("智能体任务存储不能是链接")
        self.user = user
        self._db_lock = _database_lock(self.path)

    @contextmanager
    def db(self, *, write: bool = False):
        with self._db_lock:
            with self._connection(write=write) as db:
                yield db

    @contextmanager
    def _connection(self, *, write: bool = False):
        for path in (self.path.parent, self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue  # SQLite legitimately removes idle WAL/SHM sidecars.
            if (_contains_reparse_point(path, self.root)
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                raise ValueError("智能体任务存储不能是链接")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS agent_tasks(
                id TEXT PRIMARY KEY, parent TEXT NOT NULL, source TEXT NOT NULL,
                session TEXT NOT NULL, updated TEXT NOT NULL, record TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS agent_tasks_scope ON agent_tasks(source,session,updated)")
            db.execute("CREATE INDEX IF NOT EXISTS agent_tasks_parent ON agent_tasks(parent)")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        if write:
            with _SIGNAL:
                _SIGNAL.notify_all()

    @staticmethod
    def put(db, record):
        db.execute("""INSERT INTO agent_tasks VALUES(?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET parent=excluded.parent,
            updated=excluded.updated,record=excluded.record""",
            (record["id"], record.get("root_task_id", ""), record["source"],
             record["session_id"], record["updated_at"], json.dumps(record, ensure_ascii=False)))

    @staticmethod
    def decode(row) -> Any:
        return json.loads(row["record"]) if row else None

    @staticmethod
    def reconcile(record):
        # A PID alone is unsafe (reuse). psutil creation time verifies a surviving
        # other process without claiming its work is resumable in this process.
        if record["status"] not in _LIVE | {"waiting_children", "merging"} and not record.get("execution_live"):
            return record
        if record.get("response_closed") and not record.get("root_task_id"):
            # A parent process exiting does not prove its child processes or
            # required Shell jobs exited. _refresh_closed_parent checks them.
            return record
        if record.get("owner") == _OWNER:
            return record
        try:
            import psutil
            pid = int(record.get("pid") or 0)
            if pid <= 0 or not record.get("process_started"):
                raise ValueError("缺少可核对的进程身份")
            process = psutil.Process(pid)
            alive = abs(process.create_time() - record["process_started"]) < .01
        except Exception as exc:
            # Import/query/access failures are not proof of executor exit.
            # NoSuchProcess is the sole query exception proving death.
            if type(exc).__name__ not in {"NoSuchProcess", "ZombieProcess"}:
                record["reconcile_unverified"] = True
                record["reconcile_error_type"] = type(exc).__name__
                return record
            alive = False
        if not alive:
            record.update(status="partial" if record.get("response_closed") else "interrupted",
                          stop_reason="interrupted", finished_at=record.get("finished_at") or now(), updated_at=now(),
                          execution_live=False, error={"message": "执行进程已退出；未自动重放有副作用操作"})
        return record

    def _refresh_closed_parent(self, db, parent_id):
        parent = self.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (parent_id,)).fetchone())
        if not parent or not parent.get("response_closed") or not parent.get("execution_live"):
            return parent
        children = [self.decode(row) for row in db.execute(
            "SELECT * FROM agent_tasks WHERE parent=?", (parent_id,),
        ).fetchall()]
        children_live = any(child["status"] not in _FINAL or child.get("execution_live")
                            or (parent.get("cardstream_integration_id")
                                and not child.get("card_result_seq"))
                            for child in children)
        from run.tools import ShellInbox
        shell_live = bool(ShellInbox(
            self.root, self.user, parent["source"], parent["session_id"],
            root_task_id=parent_id,
        ).pending(owned_task_ids={child["id"] for child in children}))
        if not children_live and not shell_live:
            parent.update(execution_live=False, execution_finished_at=now(), updated_at=now())
            self.put(db, parent)
        return parent

    def _reconciled(self, row):
        record = self.decode(row)
        if not record or self.reconcile(record.copy()) == record:
            return record
        # Ordinary polling stays read-only. Recovery rechecks under the writer
        # lock so it cannot overwrite a scheduler update observed after the read.
        with self.db(write=True) as db:
            current = self.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?",
                                            (record["id"],)).fetchone())
            if current:
                reconciled = self.reconcile(current.copy())
                if reconciled != current:
                    self.put(db, reconciled)
                    self._refresh_closed_parent(db, current.get("root_task_id", ""))
                return reconciled
        return None

    def get(self, task_id: str, source: str, session_id: str) -> Any:
        with self.db() as db:
            row = db.execute("SELECT * FROM agent_tasks WHERE id=? AND source=? AND session=?",
                             (task_id, source, session_id)).fetchone()
        return self._reconciled(row)

    def records(self, source: str = "", session_id: str = "", *, parent: str | None = None,
                offset: int = 0, limit: int = 50):
        clauses, args = [], []
        for column, value in (("source", source), ("session", session_id)):
            if value:
                clauses.append(f"{column}=?")
                args.append(value)
        if parent is not None:
            clauses.append("parent=?")
            args.append(parent)
        where = " AND ".join(clauses) or "1=1"
        with self.db() as db:
            total = db.execute(f"SELECT count(*) FROM agent_tasks WHERE {where}", args).fetchone()[0]
            # rowid is durable insertion order, independent of timestamps and
            # progress updates. Legacy records need no destructive migration.
            order = "COALESCE(json_extract(record, '$.submission_order'), rowid) ASC, rowid ASC" if parent else "rowid DESC"
            rows = db.execute(f"SELECT * FROM agent_tasks WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                              [*args, limit, offset]).fetchall()
        records = [record for row in rows if (record := self._reconciled(row)) is not None]
        return records, total

    def create_root(self, task_id, source, session_id, run_id, attempt_id, description,
                    *, cardstream_integration_id="", cardstream_generation="",
                    cardstream_meter_key=""):
        import psutil
        with self.db(write=True) as db:
            existing = self.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())
            if existing:
                if (existing["source"], existing["session_id"]) != (source, session_id):
                    raise ValueError("主任务作用域不匹配")
                existing.update(status="running", run_id=run_id, attempt_id=attempt_id, updated_at=now(),
                                finished_at="", owner=_OWNER, pid=os.getpid(),
                                process_started=psutil.Process().create_time(),
                                cardstream_integration_id=cardstream_integration_id,
                                cardstream_generation=cardstream_generation,
                                cardstream_meter_key=cardstream_meter_key)
                self.put(db, existing)
                return
            self.put(db, {
                "id": task_id, "root_task_id": "", "source": source, "session_id": session_id,
                "run_id": run_id, "attempt_id": attempt_id, "agent": "main",
                "description": safe(description), "status": "running",
                "cardstream_integration_id": cardstream_integration_id,
                "cardstream_generation": cardstream_generation,
                "cardstream_meter_key": cardstream_meter_key,
                "created_at": now(), "updated_at": now(), "finished_at": "",
                "owner": _OWNER, "pid": os.getpid(),
                "process_started": psutil.Process().create_time(),
            })

    def register(self, task, ownership):
        with self.db(write=True) as db:
            parent = self.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?",
                                            (ownership["root_task_id"],)).fetchone())
            if (not parent or parent["status"] not in _OPEN_PARENT
                    or parent["source"] != task.source or parent["session_id"] != task.session_id
                    or parent["attempt_id"] != ownership["attempt_id"]):
                raise ValueError("主任务已收束或子任务归属无效")
            description = (task.input_data.get("task") or task.input_data.get("instruction")
                           or task.input_data.get("request") or task.agent)
            previous_count = db.execute("SELECT count(*) FROM agent_tasks WHERE parent=?",
                                        (parent["id"],)).fetchone()[0]
            task.submission_order = max(parent.get("last_submission_order", 0), previous_count) + 1
            parent["last_submission_order"] = task.submission_order
            self.put(db, parent)
            self.put(db, {
                "id": task.id, "agent": task.agent, "source": task.source,
                "session_id": task.session_id, **ownership, "required": True,
                "submission_order": task.submission_order,
                "description": safe(description[:4000] if isinstance(description, str) else task.agent),
                "status": "queued", "created_at": task.created_at, "updated_at": now(),
                "started_at": "", "finished_at": "", "events": [], "result": None,
                "error": None, "delivered": False, "handled": False,
                "run_count": 1, "resumable": False,
                "origin_task_id": getattr(task, "origin_task_id", ""),
                "budget": getattr(task, "progress", {}).get("budget", {}),
                "cardstream_integration_id": parent.get("cardstream_integration_id", ""),
                "cardstream_generation": parent.get("cardstream_generation", ""),
                "cardstream_meter_key": parent.get("cardstream_meter_key", ""),
                "owner": parent["owner"], "pid": parent["pid"],
                "process_started": parent["process_started"],
            })

    def update(self, task_id, **changes):
        with self.db(write=True) as db:
            record = self.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())
            if record:
                record.update(changes, updated_at=now())
                self.put(db, record)

    def event(self, task_id, metadata):
        allowed = ("status", "tool_name", "iteration", "tool_count", "next_attempt",
                   "elapsed_ms", "tool_status", "summary", "tool_call_id", "run_count")
        entry = {"at": now(), **{k: safe(metadata[k]) for k in allowed if k in metadata}}
        if metadata.get("status") in {"tool_running", "tool_finished"}:
            for key in ("arguments", "result"):
                if key in metadata:
                    entry[key] = public_tool_data(metadata[key])
        with self.db(write=True) as db:
            record = self.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())
            if record:
                if metadata.get("run_count", 1) != record.get("run_count", 1):
                    return False
                record["events"] = [*record.get("events", []), entry][-160:]
                if isinstance(metadata.get("budget"), dict):
                    record["budget"] = safe(metadata["budget"])
                if isinstance(metadata.get("usage"), dict):
                    record["usage"] = {k: v for k, v in metadata["usage"].items()
                                       if isinstance(v, (int, float)) and not isinstance(v, bool)}
                record["progress"] = {k: entry[k] for k in ("at", "status", "tool_name", "iteration") if k in entry}
                record["updated_at"] = now()
                self.put(db, record)
                return True
        return False

    def scheduler_update(self, task, status):
        snapshot = task.snapshot()
        error = snapshot.get("error") or {}
        live = snapshot["status"] in {"queued", "running", "timed_out_running"} or error.get("process_terminated") is False
        actual = ("cancelling" if error.get("user_cancel_requested") and live
                  else snapshot["status"])
        result = snapshot.get("result")
        # Raw runner metadata contains tool traces and reasoning. Persist only
        # output data and reliable numeric usage. Tool details use the separate
        # bounded public event projection, never raw runner metadata.
        from run.agents.conclusions import conclusion_data
        public_result = None
        if isinstance(result, dict):
            data = result["data"] if "agent" in result and "data" in result else result
            public_result = conclusion_data(data)
        usage = result.get("usage") if isinstance(result, dict) else (task.resume_state or {}).get("usage")
        background = None
        if not live:
            from run.agents.controls import background_receipt
            try:
                background = background_receipt(self.root, self.user, task.source, task.session_id, task.id)
                if not background.get("total"):
                    background = None
            except Exception as exc:
                background = {"inspection_error": type(exc).__name__, "policy": "后台作业状态未核实，请主动查询，不能假定已结束。"}
            metadata = getattr(task.result, "metadata", None)
            if isinstance(metadata, dict) and background is not None:
                metadata["background_jobs"] = background
        with self.db(write=True) as db:
            record = self.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task.id,)).fetchone())
            if not record or record.get("run_count", 1) != task.run_count:
                return
            elapsed_ms = task.previous_elapsed_ms
            if snapshot["started_at"]:
                end = snapshot["finished_at"] if not live and snapshot["finished_at"] else now()
                elapsed_ms += max(0, round((datetime.fromisoformat(end) - datetime.fromisoformat(snapshot["started_at"])).total_seconds() * 1000))
            record.update(status=actual, started_at=snapshot["started_at"],
                          finished_at="" if live else snapshot["finished_at"],
                          result=public_result, error=safe(error) if error else None,
                          usage={k: v for k, v in (usage or {}).items()
                                 if isinstance(v, (int, float)) and not isinstance(v, bool)},
                          execution_live=live, run_count=task.run_count,
                          resumable=snapshot["resumable"], active_elapsed_ms=elapsed_ms,
                          budget=snapshot.get("budget") or record.get("budget", {}),
                          stop_reason=error.get("stop_reason") or ({"timed_out": "timeout", "tool_limit": "tool_budget_exhausted",
                              "failed": "execution_error", "cancelled": "cancelled", "interrupted": "interrupted"}.get(actual)),
                          background_jobs=background,
                          updated_at=now())
            self.put(db, record)
            self._refresh_closed_parent(db, record.get("root_task_id", ""))
        self.event(task.id, {"status": status, "run_count": task.run_count})
        if not live:
            try:
                self._append_card_result(task.id, task.source, task.session_id)
            except Exception as exc:
                logging.getLogger(__name__).exception(
                    "子任务结果卡提交失败，保留会话执行保护：%s", task.id,
                )
                self.update(task.id, card_result_error_type=type(exc).__name__)

    def _append_card_result(self, task_id, source, session_id):
        record = self.get(task_id, source, session_id)
        if (not record or record.get("card_result_seq")
                or record["status"] not in _FINAL or record.get("execution_live")):
            return
        integration_id = str(record.get("cardstream_integration_id") or "")
        generation = str(record.get("cardstream_generation") or "")
        if not integration_id or not generation:
            return
        from run.agents.conclusions import notification
        from run.cards import CardMeter, CardStore
        run_count = int(record.get("run_count") or 1)
        task_card_id = f"task_{task_id}_run_{run_count}"
        result = notification(record)
        payload = {
            "tool_call_id": task_card_id,
            "execution_id": task_card_id,
            "original_call": {
                "name": "subagent_task.result",
                "arguments": {"task_id": task_id, "agent": record.get("agent", ""),
                              "run_count": run_count},
            },
            "execution": {
                "status": record.get("status", "failed"),
                "duration_ms": max(0, int(record.get("active_elapsed_ms") or 0)),
                "execution_live": False,
            },
            "result": result,
        }
        store = CardStore(
            self.root, self.user, meter=CardMeter(key=record.get("cardstream_meter_key") or ""),
        )
        event_key = f"subagent-result:{task_id}:run:{run_count}"
        # A crash can happen after the card commit but before its ledger marker.
        # Recover that exact event instead of rebuilding a changed notification.
        from run.cards import session_key
        with store.db(write=True) as card_db:
            old = card_db.execute(
                "SELECT seq,integration_id FROM context_cards WHERE session_key=? AND event_key=?",
                (session_key(source, session_id), event_key),
            ).fetchone()
            if old is not None and old["integration_id"] != integration_id:
                raise RuntimeError("子任务结果卡归属冲突")
            state = card_db.execute(
                "SELECT meter_key FROM card_sessions WHERE session_key=?",
                (session_key(source, session_id),),
            ).fetchone()
            if state is not None:
                store.meter = CardMeter(key=state["meter_key"])
            seq = old["seq"] if old is not None else store.append(
                source, session_id, integration_id, "tool_result", payload,
                tool_call_id=task_card_id, event_key=event_key,
                expected_generation=generation, transaction=card_db,
            ).seq
        with self.db(write=True) as db:
            current = self.decode(db.execute(
                "SELECT * FROM agent_tasks WHERE id=? AND source=? AND session=?",
                (task_id, source, session_id),
            ).fetchone())
            if current:
                if current.get("run_count", 1) != run_count:
                    raise RuntimeError("子任务结果提交期间执行代际改变")
                current.update(card_result_seq=seq, card_result_error_type="", updated_at=now())
                self.put(db, current)
                self._refresh_closed_parent(db, current.get("root_task_id", ""))

    @staticmethod
    def public(record):
        public = {k: v for k, v in record.items() if k not in {
            "owner", "pid", "process_started", "attempt_id", "last_submission_order", "_guidance",
            "cardstream_generation", "cardstream_meter_key",
        }}
        public["guidance_pending"] = len(record.get("_guidance") or [])
        public["guidance_delivered"] = int(record.get("guidance_delivered_count") or 0)
        return public
