"""Append-only RAW and atomic mutable VIEW, without legacy-window dependencies."""
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from run.cards.errors import CardConflictError, CardSessionClosedError
from run.cards.meter import CardMeter, request_calibration_key
from run.cards.models import (
    RAW_KINDS, Card, CardSnapshot, CompactionBatch, json_text, session_key,
    utc_now, validate_summary,
)
from run.cards.schema import connection


class CardStore:
    def __init__(self, root: Path, user: str, *, meter: CardMeter | None = None):
        self.root = Path(root).resolve()
        self.user = user
        self.meter = meter or CardMeter()

    @property
    def path(self) -> Path:
        return self.root / "users" / self.user / "history" / "cards.sqlite3"

    def db(self, *, write=False):
        return connection(self.root, self.user, write=write)

    def ensure_session(self, source: str, session_id: str) -> dict[str, Any]:
        key, now = session_key(source, session_id), utc_now()
        with self.db(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO card_sessions "
                "(session_key,source,session_id,generation,meter_key,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (key, source, session_id, uuid4().hex, self.meter.key, now, now),
            )
            row = db.execute("SELECT * FROM card_sessions WHERE session_key=?", (key,)).fetchone()
            self._require_open(row)
            return dict(row)

    @staticmethod
    def _require_open(row) -> None:
        if row is None:
            raise CardConflictError("CardStream session does not exist")
        if row["lifecycle"] != "open":
            raise CardSessionClosedError(
                "CardStream 对话已经删除，不能复用原身份" if row["lifecycle"] == "deleted"
                else "CardStream 对话已经结束，请显式重新打开或新建会话")

    def session(self, source: str, session_id: str) -> dict[str, Any] | None:
        with self.db() as db:
            row = db.execute("SELECT * FROM card_sessions WHERE session_key=?",
                             (session_key(source, session_id),)).fetchone()
            return dict(row) if row else None

    def append(
        self, source: str, session_id: str, integration_id: str, kind: str,
        payload: dict[str, Any], *, level: str | None = None,
        tool_call_id: str | None = None, event_key: str | None = None,
        expected_generation: str | None = None,
        transaction=None,
    ) -> Card:
        if kind not in RAW_KINDS or not isinstance(payload, dict) or not integration_id:
            raise ValueError("Invalid CardStream original event")
        if kind.startswith("tool_") and not tool_call_id:
            raise ValueError("Tool cards require a stable tool_call_id")
        if level is not None and level not in (
            {"initial", "guidance"} if kind == "user_text" else {"interim", "final"}
            if kind == "agent_text" else set()
        ):
            raise ValueError("Invalid historical card level")
        raw = json_text(payload)
        tokens = self.meter.tokens(kind, payload)
        key = session_key(source, session_id)
        # Runtime registration/result routing and its historical event must
        # share one commit. Only trusted framework callers supply a connection.
        with (nullcontext(transaction) if transaction is not None else self.db(write=True)) as db:
            state = db.execute("SELECT * FROM card_sessions WHERE session_key=?", (key,)).fetchone()
            self._require_open(state)
            if expected_generation is not None and state["generation"] != expected_generation:
                raise CardConflictError("Stale session generation")
            if state["meter_key"] != self.meter.key:
                raise CardConflictError("Model meter changed; re-meter the active view before appending")
            if event_key is not None:
                old = db.execute("SELECT * FROM context_cards WHERE session_key=? AND event_key=?",
                                 (key, event_key)).fetchone()
                if old:
                    card = Card.from_row(old)
                    if (card.raw_payload_json, card.raw_kind, card.raw_level,
                        card.integration_id, card.tool_call_id) != (
                        raw, kind, level, integration_id, tool_call_id
                    ):
                        raise CardConflictError("An event key cannot identify different execution facts")
                    return card
            card_id, now, seq = "card_" + uuid4().hex, utc_now(), state["last_seq"] + 1
            db.execute(
                "INSERT INTO context_cards(card_id,session_key,integration_id,seq,"
                "raw_kind,raw_level,raw_payload,raw_tokens,view_kind,view_payload,view_tokens,"
                "token_active,view_revision,tool_call_id,event_key,meter_key,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,1,1,?,?,?,?)",
                (card_id, key, integration_id, seq, kind, level, raw, tokens, kind,
                 raw, tokens, tool_call_id, event_key, self.meter.key, now),
            )
            db.execute("UPDATE card_sessions SET last_seq=?,active_tokens=active_tokens+?,"
                       "updated_at=? WHERE session_key=?", (seq, tokens, now, key))
            record = json.loads(state["record_json"])
            if record.get("memory_status") == "completed":
                record["memory_status"] = "pending"
                record.pop("memory_target_seq", None)
                db.execute("UPDATE card_sessions SET record_json=? WHERE session_key=?",
                           (json_text(record), key))
            return Card.from_row(db.execute("SELECT * FROM context_cards WHERE card_id=?",
                                           (card_id,)).fetchone())

    def snapshot(self, source: str, session_id: str) -> CardSnapshot:
        key = session_key(source, session_id)
        with self.db() as db:
            state = db.execute("SELECT * FROM card_sessions WHERE session_key=?", (key,)).fetchone()
            if state is None:
                raise CardConflictError("CardStream session does not exist")
            rows = db.execute("SELECT * FROM context_cards WHERE session_key=? "
                              "AND token_active=1 ORDER BY seq", (key,)).fetchall()
            cards = tuple(Card.from_row(row) for row in rows)
            if sum(c.view_tokens for c in cards) != state["active_tokens"]:
                raise CardConflictError("Stored effective token count is inconsistent")
            return CardSnapshot(key, state["generation"], state["last_seq"], state["view_epoch"],
                                state["active_tokens"], cards, state["meter_key"])

    def raw_page(
        self, source: str, session_id: str, *, after_seq: int = 0,
        through_seq: int | None = None, limit: int = 100, query: str = "",
    ) -> tuple[Card, ...]:
        if type(limit) is not int or not 1 <= limit <= 500 or type(after_seq) is not int or after_seq < 0:
            raise ValueError("Historical RAW reads require bounded pagination")
        where, values = ["session_key=?", "seq>?"], [session_key(source, session_id), after_seq]
        if through_seq is not None:
            where.append("seq<=?")
            values.append(through_seq)
        if query:
            where.append("instr(raw_payload,?)>0")
            values.append(query)
        with self.db() as db:
            rows = db.execute("SELECT * FROM context_cards WHERE " + " AND ".join(where)
                              + " ORDER BY seq LIMIT ?", (*values, limit)).fetchall()
            return tuple(Card.from_row(row) for row in rows)

    def replace_prefix(
        self, batch: CompactionBatch, summary: dict[str, Any], *,
        before_commit: Callable[[], None] | None = None,
        validate_snapshot: Callable[[CardSnapshot], None] | None = None,
    ) -> CardSnapshot:
        """CAS only the selected prefix; newly appended suffix events remain valid."""
        validate_summary(summary)
        if not batch.cards or batch.cards != batch.snapshot.cards[:len(batch.cards)]:
            raise ValueError("Compaction must consume a contiguous active prefix")
        if batch.snapshot.meter_key != self.meter.key:
            raise CardConflictError("Cannot compact a snapshot measured for another model")
        summary_tokens = self.meter.tokens("summary", summary)
        if summary_tokens > batch.summary_limit or summary_tokens >= batch.batch_tokens:
            raise ValueError(
                "Candidate summary exceeds capacity or does not reduce history: "
                f"summary={summary_tokens} limit={batch.summary_limit} batch={batch.batch_tokens}")
        key, host = batch.snapshot.session_key, batch.cards[0]
        with self.db(write=True) as db:
            state = db.execute("SELECT * FROM card_sessions WHERE session_key=?", (key,)).fetchone()
            self._require_open(state)
            if (state["generation"], state["view_epoch"], state["meter_key"]) != (
                batch.snapshot.generation, batch.snapshot.view_epoch, self.meter.key
            ):
                raise CardConflictError("The view changed while the summary was generated")
            rows = db.execute("SELECT * FROM context_cards WHERE session_key=? "
                              "AND token_active=1 AND seq<=? ORDER BY seq",
                              (key, batch.cards[-1].seq)).fetchall()
            actual = tuple(Card.from_row(row) for row in rows)
            if actual != batch.cards:
                raise CardConflictError("The selected prefix changed while generating its summary")
            for card in batch.cards[1:]:
                db.execute("UPDATE context_cards SET token_active=0,absorbed_by=?,"
                           "view_revision=view_revision+1 WHERE card_id=?",
                           (host.card_id, card.card_id))
            db.execute("UPDATE context_cards SET view_kind='summary',view_payload=?,view_tokens=?,"
                       "token_active=1,view_revision=view_revision+1,"
                       "compression_stage=compression_stage+1,absorbed_by=NULL WHERE card_id=?",
                       (json_text(summary), summary_tokens, host.card_id))
            db.execute("UPDATE card_sessions SET active_tokens=active_tokens-?+?,"
                       "view_epoch=view_epoch+1,updated_at=? WHERE session_key=?",
                       (batch.batch_tokens, summary_tokens, utc_now(), key))
            cards = tuple(Card.from_row(row) for row in db.execute(
                "SELECT * FROM context_cards WHERE session_key=? AND token_active=1 ORDER BY seq",
                (key,)).fetchall())
            after = CardSnapshot(
                key, state["generation"], state["last_seq"], state["view_epoch"] + 1,
                state["active_tokens"] - batch.batch_tokens + summary_tokens, cards, self.meter.key,
            )
            if validate_snapshot is not None:
                validate_snapshot(after)
            if before_commit is not None:
                before_commit()  # Also lets tests prove multi-row rollback.
        return after

    def remeter(self, source: str, session_id: str) -> CardSnapshot:
        """Only VIEW tokens change with model/tokenizer; RAW tokens are evidence."""
        key, total = session_key(source, session_id), 0
        with self.db(write=True) as db:
            state = db.execute("SELECT * FROM card_sessions WHERE session_key=?", (key,)).fetchone()
            self._require_open(state)
            if state["meter_key"] != self.meter.key:
                for row in db.execute("SELECT * FROM context_cards WHERE session_key=? AND token_active=1",
                                      (key,)).fetchall():
                    card = Card.from_row(row)
                    tokens = self.meter.tokens(card.view_kind, card.view_payload)
                    total += tokens
                    db.execute("UPDATE context_cards SET view_tokens=?,meter_key=?,"
                               "view_revision=view_revision+1 WHERE card_id=?",
                               (tokens, self.meter.key, card.card_id))
                db.execute("UPDATE card_sessions SET active_tokens=?,meter_key=?,view_epoch=view_epoch+1 "
                           "WHERE session_key=?", (total, self.meter.key, key))
        return self.snapshot(source, session_id)

    def close(self, source: str, session_id: str, *, deleted: bool = False) -> None:
        with self.db(write=True) as db:
            self.require_idle(db, session_key(source, session_id))
            db.execute("UPDATE card_sessions SET lifecycle=?,updated_at=? WHERE session_key=? "
                       "AND lifecycle!='deleted'",
                       ("deleted" if deleted else "closed", utc_now(), session_key(source, session_id)))

    @staticmethod
    def require_idle(db, key: str) -> None:
        active = db.execute(
            "SELECT 1 FROM card_integrations i WHERE i.session_key=? AND "
            "(i.response_closed=0 OR EXISTS(SELECT 1 FROM card_tool_executions t "
            "WHERE t.integration_id=i.integration_id AND t.execution_live=1))", (key,),
        ).fetchone()
        if active:
            raise CardConflictError("会话仍有活动整合或未确认退出的工具，不能关闭、删除或重置")

    def update_record(self, source: str, session_id: str, changes: dict[str, Any]) -> None:
        """Control-plane metadata is separate from immutable historical facts."""
        key = session_key(source, session_id)
        with self.db(write=True) as db:
            row = db.execute("SELECT record_json FROM card_sessions WHERE session_key=?", (key,)).fetchone()
            if row is None:
                raise CardConflictError("CardStream session does not exist")
            record = {**json.loads(row[0]), **changes}
            db.execute("UPDATE card_sessions SET record_json=?,updated_at=? WHERE session_key=?",
                       (json_text(record), utc_now(), key))

    def observe_usage(self, snapshot, *, request_id: str, estimated: int, actual: int) -> None:
        """Calibration is evidence about this exact request, never a RAW rewrite."""
        if (snapshot.history.meter_key != self.meter.key or not request_id
                or type(estimated) is not int or estimated < 1
                or type(actual) is not int or actual < 0):
            raise ValueError("Usage calibration requires an exact model/request snapshot")
        with self.db(write=True) as db:
            key = request_calibration_key(
                self.meter.key, self.meter.calibration_scope, snapshot.items()
            )
            values = (request_id, snapshot.snapshot_id, key, estimated, actual)
            old = db.execute("SELECT request_id,snapshot_id,meter_key,estimated_tokens,actual_tokens "
                             "FROM card_meter_observations WHERE request_id=?", (request_id,)).fetchone()
            if old and tuple(old) != values:
                raise CardConflictError("同一 Provider 请求的 usage 不能关联另一快照")
            db.execute("INSERT OR IGNORE INTO card_meter_observations VALUES(?,?,?,?,?,?)",
                       (*values, utc_now()))

    def calibrated_tokens(self, estimated: int, *, items=()) -> int:
        import math
        key = request_calibration_key(self.meter.key, self.meter.calibration_scope, items)
        with self.db() as db:
            rows = db.execute("SELECT estimated_tokens,actual_tokens FROM card_meter_observations "
                              "WHERE meter_key=? ORDER BY observed_at DESC LIMIT 64",
                              (key,)).fetchall()
        # Never lower a conservative estimate on the strength of cache billing
        # or one incomplete upstream usage report.
        scale = max([1.0, *(actual / predicted for predicted, actual in rows)])
        return math.ceil(estimated * scale)
