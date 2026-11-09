"""Durable execution truth, not a second historical store or a round model."""
from __future__ import annotations

import json
import os
from contextlib import nullcontext
from typing import Any
from uuid import uuid4

from run.cards.errors import CardConflictError
from run.cards.models import Card, json_text, session_key, utc_now
from run.cards.store import CardStore

_OWNER = uuid4().hex


class IntegrationStore:
    """One integration owns a session until its response and executions close."""

    def __init__(self, store: CardStore, source: str, session_id: str, integration_id: str):
        self.store, self.source, self.session_id = store, source, session_id
        self.id, self.key = integration_id, session_key(source, session_id)
        self.generation = ""

    @staticmethod
    def _owner_lost(row) -> bool:
        from run.infra import process_identity_matches, process_snapshot
        if row["owner_pid"] == os.getpid() and row["owner_token"] == _OWNER:
            return False
        expected = json.loads(row["record_json"]).get("owner_identity") or {}
        return process_identity_matches(
            process_snapshot(row["owner_pid"]),
            process_started_at=expected.get("process_started_at", ""),
            process_name=expected.get("process_name", ""),
        ) is False

    @staticmethod
    def _decision_resumable(db, row) -> bool:
        executions = db.execute(
            "SELECT original_call,execution_live,last_result_seq FROM card_tool_executions "
            "WHERE integration_id=?", (row["integration_id"],),
        ).fetchall()
        question = False
        for execution in executions:
            call = json.loads(execution["original_call"])
            native = call["name"] == "user_decision" and call["arguments"].get("action") == "ask"
            if execution["execution_live"] and not native:
                return False  # Unknown external effects keep their existing fence.
            if native and (execution["execution_live"] or (
                execution["last_result_seq"] or 0
            ) > row["acknowledged_seq"]):
                question = True
        return question and not row["response_closed"] and bool(
            json.loads(row["record_json"]).get("decision_checkpoint"))

    def checkpoint_decisions(self, request: dict) -> None:
        with self.store.db(write=True) as db:
            row = self._row(db)
            record = json.loads(row["record_json"])
            record["decision_checkpoint"] = {
                key: request[key] for key in (
                    "run_id", "_agent_root_task_id", "_task_plan_id",
                    "_task_plan_step_id", "_task_plan_mode",
                ) if request.get(key)
            }
            # Even callers without a run_id need an explicit checkpoint marker.
            record["decision_checkpoint"]["stream"] = bool(request.get("stream"))
            db.execute("UPDATE card_integrations SET record_json=? WHERE integration_id=?",
                       (json_text(record), self.id))

    def resume_decisions(self) -> dict[str, Any]:
        """Claim a lost native wait without replaying any tool invocation."""
        from run.infra import process_snapshot
        with self.store.db(write=True) as db:
            row = self._row(db)
            if not self._owner_lost(row) or not self._decision_resumable(db, row):
                raise CardConflictError("该整合仍有宿主或未确认的工具执行，不能自动恢复")
            record = json.loads(row["record_json"])
            record["owner_identity"] = process_snapshot(os.getpid())
            db.execute(
                "UPDATE card_integrations SET owner_pid=?,owner_token=?,status='resuming',"
                "model_request_inflight=0,continuation_required=1,record_json=?,updated_at=? "
                "WHERE integration_id=?",
                (os.getpid(), _OWNER, json_text(record), utc_now(), self.id),
            )
            self.generation = row["generation"]
            return record

    def start(self) -> dict[str, Any]:
        state = self.store.ensure_session(self.source, self.session_id)
        self.generation = state["generation"]
        self.recover_interrupted()
        from run.infra import process_snapshot
        owner_identity = process_snapshot(os.getpid())
        with self.store.db(write=True) as db:
            pending = db.execute(
                "SELECT 1 FROM card_integrations i WHERE i.session_key=? AND "
                "(i.response_closed=0 OR EXISTS(SELECT 1 FROM card_tool_executions t "
                "WHERE t.integration_id=i.integration_id AND t.execution_live=1))",
                (self.key,),
            ).fetchone()
            if pending:
                raise CardConflictError("会话仍有未关闭的整合或未实际退出的工具；不重复执行未知副作用")
            now = utc_now()
            db.execute(
                "INSERT INTO card_integrations(integration_id,session_key,generation,"
                "owner_pid,owner_token,record_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (self.id, self.key, self.generation, os.getpid(), _OWNER,
                 json_text({"owner_identity": owner_identity}), now, now),
            )
        return state

    def recover_interrupted(self) -> list[str]:
        """Only a confirmed lost process can retire its abandoned response.

        An unknown side-effect execution stays fenced. A dead coordinator is
        not proof that an isolated child exited or that its effect failed.
        """
        from run.infra import process_identity_matches, process_snapshot
        recovered = []
        with self.store.db(write=True) as db:
            rows = db.execute(
                "SELECT * FROM card_integrations i WHERE session_key=? AND "
                "(response_closed=0 OR EXISTS(SELECT 1 FROM card_tool_executions t "
                "WHERE t.integration_id=i.integration_id AND t.execution_live=1 "
                "AND t.status!='interrupted_unknown'))", (self.key,),
            ).fetchall()
            for row in rows:
                if row["owner_pid"] == os.getpid() and row["owner_token"] == _OWNER:
                    continue
                record = json.loads(row["record_json"])
                expected = record.get("owner_identity") or {}
                snapshot = process_snapshot(row["owner_pid"])
                matches = process_identity_matches(snapshot,
                    process_started_at=expected.get("process_started_at", ""),
                    process_name=expected.get("process_name", ""))
                if matches is not False:
                    continue
                if self._decision_resumable(db, row):
                    # A user question has no orphan process or unknown external
                    # effect. The recovery worker claims this same integration.
                    continue
                executions = db.execute("SELECT * FROM card_tool_executions WHERE integration_id=? "
                                         "AND execution_live=1 AND status!='interrupted_unknown'",
                                         (row["integration_id"],)).fetchall()
                for execution in executions:
                    self.store.append(
                        self.source, self.session_id, row["integration_id"], "tool_result", {
                            "tool_call_id": execution["tool_call_id"], "execution_id": execution["execution_id"],
                            "original_call": json.loads(execution["original_call"]),
                            "execution": {"status": "interrupted_unknown", "execution_live": True},
                            "result": {"ok": False, "error": {"code": "owner_lost",
                                       "message": "宿主已退出；副作用与隔离执行状态未知，不自动重放"}},
                        }, tool_call_id=execution["tool_call_id"],
                        event_key=f"{execution['execution_id']}:owner_lost", transaction=db)
                    db.execute("UPDATE card_tool_executions SET status='interrupted_unknown' "
                               "WHERE execution_id=?", (execution["execution_id"],))
                record.update(recovery={"reason": "owner_lost", "unknown_executions": len(executions),
                                        "replay_permitted": False, "observed_at": utc_now()})
                # A previously closed partial/cancelled response keeps its true
                # terminal status/time. Recovery describes the orphan execution,
                # not a second fabricated response completion.
                db.execute(
                    "UPDATE card_integrations SET response_closed=1,model_request_inflight=0,"
                    "status=?,continuation_required=0,record_json=?,updated_at=? WHERE integration_id=?",
                    (row["status"] if row["response_closed"] else "failed", json_text(record),
                     row["updated_at"] if row["response_closed"] else utc_now(), row["integration_id"]),
                )
                recovered.append(row["integration_id"])
        return recovered

    def _row(self, db):
        row = db.execute("SELECT * FROM card_integrations WHERE integration_id=?", (self.id,)).fetchone()
        if row is None or row["session_key"] != self.key:
            raise CardConflictError("整合身份不存在或不属于当前会话")
        state = db.execute("SELECT * FROM card_sessions WHERE session_key=?", (self.key,)).fetchone()
        self.store._require_open(state)
        if state["generation"] != row["generation"]:
            raise CardConflictError("整合所属会话已改变代际")
        return row

    def request_started(self) -> None:
        with self.store.db(write=True) as db:
            row = self._row(db)
            if row["response_closed"] or row["model_request_inflight"]:
                raise CardConflictError("不能向已关闭整合或正在请求的整合重复发送模型请求")
            db.execute("UPDATE card_integrations SET model_request_inflight=1,status='running',"
                       "continuation_required=1,updated_at=? WHERE integration_id=?", (utc_now(), self.id))

    def request_finished(self, *, accepted_seq: int | None = None,
                         continuation_required: bool = False) -> None:
        with self.store.db(write=True) as db:
            row = self._row(db)
            seq = row["acknowledged_seq"] if accepted_seq is None else accepted_seq
            last_seq = db.execute("SELECT last_seq FROM card_sessions WHERE session_key=?",
                                  (self.key,)).fetchone()[0]
            if not row["acknowledged_seq"] <= seq <= last_seq:
                raise CardConflictError("模型只能确认本次已接受请求快照实际覆盖的序号")
            db.execute("UPDATE card_integrations SET model_request_inflight=0,acknowledged_seq=?,"
                       "continuation_required=?,updated_at=? WHERE integration_id=?",
                       (seq, int(continuation_required), utc_now(), self.id))

    def update_usage(self, usage: dict[str, Any]) -> None:
        """Replace the live integration snapshot; usage updates are not deltas."""
        with self.store.db(write=True) as db:
            row = self._row(db)
            if row["response_closed"]:
                raise CardConflictError("整合已经关闭，不能更新用量")
            record = json.loads(row["record_json"])
            record["usage"] = usage
            db.execute("UPDATE card_integrations SET record_json=?,updated_at=? WHERE integration_id=?",
                       (json_text(record), utc_now(), self.id))

    def continuation(self, required: bool) -> None:
        with self.store.db(write=True) as db:
            row = self._row(db)
            if row["response_closed"]:
                raise CardConflictError("不能恢复已关闭的整合")
            db.execute("UPDATE card_integrations SET continuation_required=?,updated_at=? "
                       "WHERE integration_id=?", (int(required), utc_now(), self.id))

    def register_tool(self, tool_call_id: str, original_call: dict[str, Any]) -> tuple[dict, bool]:
        """Registration + call RAW + initial VIEW are one transaction."""
        serialized = json_text(original_call)
        with self.store.db(write=True) as db:
            row = self._row(db)
            if row["response_closed"]:
                raise CardConflictError("整合已关闭，不能启动新工具")
            existing = db.execute(
                "SELECT * FROM card_tool_executions WHERE integration_id=? AND tool_call_id=?",
                (self.id, tool_call_id),
            ).fetchone()
            if existing:
                if existing["original_call"] != serialized:
                    raise CardConflictError("同一工具调用 ID 对应不同参数，拒绝重放")
                return dict(existing), False
            execution_id, now = "exec_" + uuid4().hex, utc_now()
            db.execute(
                "INSERT INTO card_tool_executions(execution_id,integration_id,tool_call_id,"
                "original_call,status,started_at) VALUES(?,?,?,?,'running',?)",
                (execution_id, self.id, tool_call_id, serialized, now),
            )
            self.store.append(
                self.source, self.session_id, self.id, "tool_call",
                {**original_call, "tool_call_id": tool_call_id, "execution_id": execution_id},
                tool_call_id=tool_call_id, event_key=f"{execution_id}:call",
                expected_generation=row["generation"], transaction=db,
            )
            execution = db.execute("SELECT * FROM card_tool_executions WHERE execution_id=?",
                                   (execution_id,)).fetchone()
            return dict(execution), True

    def tool_result(
        self, execution_id: str, result: Any, *, status: str, duration_ms: int,
        execution_live: bool = False, transaction=None,
    ):
        with (nullcontext(transaction) if transaction is not None else self.store.db(write=True)) as db:
            row = self._row(db)
            execution = db.execute(
                "SELECT * FROM card_tool_executions WHERE execution_id=? AND integration_id=?",
                (execution_id, self.id),
            ).fetchone()
            if execution is None:
                raise CardConflictError("结果没有对应的工具执行登记")
            event_key = (f"{execution_id}:running_notice:{status}" if execution_live
                         else f"{execution_id}:result")
            payload = {
                "tool_call_id": execution["tool_call_id"], "execution_id": execution_id,
                "original_call": json.loads(execution["original_call"]),
                "execution": {"status": status, "duration_ms": max(0, int(duration_ms)),
                              "execution_live": bool(execution_live)},
                "result": result,
            }
            if not execution["execution_live"]:
                old = db.execute("SELECT * FROM context_cards WHERE session_key=? AND event_key=?",
                                 (self.key, event_key)).fetchone()
                if old is None or old["raw_payload"] != json_text(payload):
                    raise CardConflictError("已退出的工具执行不能被另一份结果覆盖")
                return Card.from_row(old)
            card = self.store.append(
                self.source, self.session_id, self.id, "tool_result", payload,
                tool_call_id=execution["tool_call_id"], event_key=event_key,
                expected_generation=row["generation"], transaction=db,
            )
            db.execute("UPDATE card_tool_executions SET status=?,execution_live=?,"
                       "last_result_seq=?,finished_at=? WHERE execution_id=?",
                       (status, int(execution_live), card.seq,
                        None if execution_live else utc_now(), execution_id))
            return card

    def publish_running_notice(self, execution_id: str, *, status: str = "running",
                               result: dict[str, Any] | None = None):
        """Durably hand a still-running tool to the next model request."""
        if not status or status in {"completed", "failed", "cancelled"}:
            raise ValueError("Running notices require a non-terminal status")
        with self.store.db(write=True) as db:
            row = self._row(db)
            execution = db.execute(
                "SELECT * FROM card_tool_executions WHERE execution_id=? AND integration_id=?",
                (execution_id, self.id),
            ).fetchone()
            if execution is None:
                raise CardConflictError("运行中通知没有对应的工具执行登记")
            if not execution["execution_live"]:
                return None
            original = json.loads(execution["original_call"])
            event_key = f"{execution_id}:running_notice:{status}"
            old = db.execute(
                "SELECT * FROM context_cards WHERE session_key=? AND event_key=?",
                (self.key, event_key),
            ).fetchone()
            if old is not None:
                return Card.from_row(old)
            payload = {
                "tool_call_id": execution["tool_call_id"], "execution_id": execution_id,
                "original_call": original,
                "execution": {"status": status, "execution_live": True},
                "result": result if result is not None else {"ok": True, "accepted": True},
            }
            card = self.store.append(
                self.source, self.session_id, self.id, "tool_result", payload,
                tool_call_id=execution["tool_call_id"], event_key=event_key,
                expected_generation=row["generation"], transaction=db,
            )
            db.execute(
                "UPDATE card_tool_executions SET status=?,last_result_seq=? WHERE execution_id=?",
                (status, card.seq, execution_id),
            )
            return card

    def missing_receipts(self, execution_ids: list[str]) -> list[str]:
        if not execution_ids:
            return []
        placeholders = ",".join("?" for _ in execution_ids)
        with self.store.db() as db:
            self._row(db)
            rows = db.execute(
                f"SELECT execution_id,last_result_seq FROM card_tool_executions "
                f"WHERE integration_id=? AND execution_id IN ({placeholders})",
                (self.id, *execution_ids),
            ).fetchall()
        received = {row["execution_id"] for row in rows if row["last_result_seq"] is not None}
        return [execution_id for execution_id in execution_ids if execution_id not in received]

    def tool_execution_status(self, execution_id: str) -> str | None:
        with self.store.db() as db:
            self._row(db)
            row = db.execute(
                "SELECT status FROM card_tool_executions "
                "WHERE integration_id=? AND execution_id=?",
                (self.id, execution_id),
            ).fetchone()
        return str(row["status"]) if row else None

    def pending(self, *, after_seq: int | None = None) -> tuple[int, int]:
        with self.store.db() as db:
            row = self._row(db)
            acknowledged_seq = row["acknowledged_seq"] if after_seq is None else after_seq
            counts = db.execute(
                "SELECT coalesce(sum(execution_live),0),coalesce(sum(last_result_seq>?),0) "
                "FROM card_tool_executions WHERE integration_id=?",
                (acknowledged_seq, self.id),
            ).fetchone()
        return int(counts[0]), int(counts[1])

    def live_executions(self) -> list[dict[str, Any]]:
        with self.store.db() as db:
            self._row(db)
            rows = db.execute(
                "SELECT execution_id,tool_call_id,original_call,status FROM card_tool_executions "
                "WHERE integration_id=? AND execution_live=1 ORDER BY started_at,execution_id",
                (self.id,),
            ).fetchall()
            return [{"id": row["execution_id"], "tool_call_id": row["tool_call_id"],
                     "agent": "tool", "name": json.loads(row["original_call"]).get("name", ""),
                     "status": row["status"], "execution_live": True} for row in rows]

    def phase(self, status: str) -> None:
        if status not in {"running", "waiting", "resuming", "finalizing"}:
            raise ValueError("Invalid integration phase")
        with self.store.db(write=True) as db:
            self._row(db)
            db.execute("UPDATE card_integrations SET status=?,updated_at=? WHERE integration_id=? "
                       "AND response_closed=0", (status, utc_now(), self.id))

    def finish(self, status: str, *, final_payload: dict | None = None, metadata: dict | None = None):
        """Completion is a runtime transaction, never inferred from model text."""
        if status not in {"completed", "limited", "partial", "failed", "cancelled"}:
            raise ValueError("Invalid integration terminal state")
        with self.store.db(write=True) as db:
            row = self._row(db)
            if row["response_closed"]:
                raise CardConflictError("整合已经关闭")
            if status == "completed":
                pending = db.execute(
                    "SELECT 1 FROM card_tool_executions WHERE integration_id=? AND "
                    "(execution_live=1 OR last_result_seq>?)",
                    (self.id, row["acknowledged_seq"]),
                ).fetchone()
                user_events = db.execute(
                    "SELECT 1 FROM context_cards WHERE session_key=? AND integration_id=? "
                    "AND seq>? AND raw_kind='user_text' LIMIT 1",
                    (self.key, self.id, row["acknowledged_seq"]),
                ).fetchone()
                if row["model_request_inflight"] or row["continuation_required"] or pending or user_events:
                    raise CardConflictError("工具未退出、结果未处理或模型请求未结束，拒绝完成整合")
            card = None
            if final_payload is not None:
                card = self.store.append(
                    self.source, self.session_id, self.id, "agent_text", final_payload,
                    level="final" if status == "completed" else "interim",
                    event_key=f"{self.id}:final", expected_generation=row["generation"],
                    transaction=db,
                )
            metadata = {**json.loads(row["record_json"]), **dict(metadata or {})}
            snapshot = db.execute("SELECT last_seq,active_tokens,view_epoch FROM card_sessions "
                                  "WHERE session_key=?", (self.key,)).fetchone()
            metadata["context"] = {**metadata.get("context", {}),
                                   "last_seq": snapshot["last_seq"],
                                   "history_tokens": snapshot["active_tokens"],
                                   "view_epoch": snapshot["view_epoch"]}
            db.execute("UPDATE card_integrations SET status=?,response_closed=1,model_request_inflight=0,"
                       "continuation_required=0,record_json=?,updated_at=? WHERE integration_id=?",
                       (status, json_text(metadata), utc_now(), self.id))
            if self.source.startswith("background:cron:"):
                state = db.execute("SELECT record_json FROM card_sessions WHERE session_key=?",
                                   (self.key,)).fetchone()
                record = {**json.loads(state[0]), "cron_finished_at": utc_now()}
                db.execute("UPDATE card_sessions SET record_json=? WHERE session_key=?",
                           (json_text(record), self.key))
            return card
