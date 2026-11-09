"""Native user questions, persisted as ordinary CardStream tool executions."""
from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from run.cards import Card, CardConflictError, CardStore, IntegrationStore, session_key
from run.config import interactive_decisions_enabled, load_config


TIMEOUT_SECONDS = 30 * 60
_QUESTION = (
    "json_extract(t.original_call,'$.name')='user_decision' AND "
    "json_extract(t.original_call,'$.arguments.action')='ask'"
)


class DecisionError(ValueError):
    pass


class DecisionClosedError(DecisionError):
    pass


def validate_question(arguments: dict[str, Any]) -> None:
    for name, limit in (("title", 120), ("question", 4000)):
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise DecisionError(f"{name} 必须是 1..{limit} 字符的非空文本")
    if arguments.get("mode", "wait") not in {"wait", "async"}:
        raise DecisionError("mode 必须为 wait 或 async")
    options = arguments.get("options")
    if not isinstance(options, list) or not 2 <= len(options) <= 6:
        raise DecisionError("options 必须包含 2..6 个选项")
    identifiers: set[str] = set()
    for option in options:
        if not isinstance(option, dict) or set(option) - {"id", "label", "description", "recommended"}:
            raise DecisionError("选项字段无效")
        for key, limit in (("id", 80), ("label", 200)):
            value = option.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise DecisionError(f"选项 {key} 必须为 1..{limit} 字符")
        if option["id"] in identifiers:
            raise DecisionError("选项 id 不能重复")
        identifiers.add(option["id"])
        if not isinstance(option.get("description", ""), str) or len(option.get("description", "")) > 1000:
            raise DecisionError("选项 description 最多 1000 字符")
        if type(option.get("recommended", False)) is not bool:
            raise DecisionError("选项 recommended 必须为布尔值")
    if sum(option.get("recommended", False) for option in options) > 1:
        raise DecisionError("最多只能有一个推荐选项")


class DecisionStore:
    """Use the existing execution ID, generation fence and result transaction."""

    def __init__(self, store: CardStore):
        self.store = store

    def _rows(self, db, source, session_id, *, decision_id="", integration_id="", pending=False):
        where, values = ["i.session_key=?", _QUESTION], [session_key(source, session_id)]
        if decision_id:
            where.append("t.execution_id=?")
            values.append(decision_id)
        if integration_id:
            where.append("i.integration_id=?")
            values.append(integration_id)
        if pending:
            where.append("t.execution_live=1")
        return db.execute(
            "SELECT t.*,i.generation,i.response_closed,s.lifecycle,s.generation AS session_generation,"
            "c.raw_payload AS receipt FROM card_tool_executions t "
            "JOIN card_integrations i USING(integration_id) "
            "JOIN card_sessions s ON s.session_key=i.session_key "
            "LEFT JOIN context_cards c ON c.session_key=i.session_key AND c.seq=t.last_result_seq "
            "WHERE " + " AND ".join(where) + " ORDER BY t.started_at,t.execution_id", values,
        ).fetchall()

    @staticmethod
    def public(row) -> dict[str, Any]:
        arguments = json.loads(row["original_call"])["arguments"]
        created = datetime.fromisoformat(row["started_at"]).timestamp()
        result = json.loads(row["receipt"])["result"] if row["receipt"] else {}
        return {
            "decision_id": row["execution_id"], "integration_id": row["integration_id"],
            "title": arguments["title"], "question": arguments["question"],
            "options": arguments["options"], "mode": arguments.get("mode", "wait"),
            "status": "pending" if row["execution_live"] else result.get("status", "cancelled"),
            "created_at": created, "expires_at": created + TIMEOUT_SECONDS,
            "answer": result.get("answer"), "resolution": result.get("resolution", ""),
        }

    def list(self, source: str, session_id: str, *, integration_id="", pending=True):
        with self.store.db() as db:
            return [self.public(row) for row in self._rows(
                db, source, session_id, integration_id=integration_id, pending=pending,
            ) if row["lifecycle"] == "open" and row["generation"] == row["session_generation"]]

    def get(self, source: str, session_id: str, decision_id: str, *, integration_id=""):
        with self.store.db() as db:
            rows = self._rows(db, source, session_id, decision_id=decision_id,
                              integration_id=integration_id)
            if not rows or rows[0]["lifecycle"] != "open" or (
                rows[0]["generation"] != rows[0]["session_generation"]
            ):
                raise KeyError(decision_id)
            return self.public(rows[0])

    def settle(self, source: str, session_id: str, decision_id: str, *, status: str,
               option_id: str = "", text: str = "", resolution: str = "",
               integration_id: str = "", now: float | None = None):
        import time
        current = time.time() if now is None else now
        with self.store.db(write=True) as db:
            rows = self._rows(db, source, session_id, decision_id=decision_id,
                              integration_id=integration_id)
            if not rows or rows[0]["lifecycle"] != "open":
                raise KeyError(decision_id)
            row = rows[0]
            if row["generation"] != row["session_generation"]:
                raise KeyError(decision_id)
            if not row["execution_live"]:
                raise DecisionClosedError("该问题已回答、超时或关闭，请刷新后查看")
            item = self.public(row)
            if row["response_closed"]:
                status = "cancelled"
            elif current >= item["expires_at"]:
                status = "expired"
            result: dict[str, Any] = {"ok": True, "decision_id": decision_id, "status": status}
            if status == "answered":
                if bool(option_id) == bool(text.strip()):
                    raise DecisionError("请选择一个预设选项，或填写自定义回复")
                if len(text) > 8000:
                    raise DecisionError("自定义回复最多 8000 字符")
                selected = next((option for option in item["options"] if option["id"] == option_id), None)
                if option_id and selected is None:
                    raise DecisionError("选择的选项不存在")
                result["answer"] = {
                    "option_id": option_id, "text": selected["label"] if selected else text.strip(),
                    "custom": not bool(option_id),
                }
            elif status == "expired":
                result["instruction"] = (
                    "用户 30 分钟内没有回答。请根据现有信息自主决定并继续任务；"
                    "用户沉默不代表批准尚未授权的操作。"
                )
            elif status == "resolved_by_agent":
                if not resolution.strip() or len(resolution) > 4000:
                    raise DecisionError("关闭问题时必须说明解决结果，最多 4000 字符")
                result["resolution"] = resolution.strip()
            elif status == "cancelled":
                result["resolution"] = resolution or "当前任务已结束或交互式决策已关闭"
            else:
                raise DecisionError("无效的决策终态")
            integration = IntegrationStore(self.store, source, session_id, row["integration_id"])
            integration.generation = row["generation"]
            # The first terminal result wins in the same BEGIN IMMEDIATE as
            # the live-state check. Answer/timeout/agent close cannot overwrite it.
            integration.tool_result(
                decision_id, result, status="cancelled" if status == "cancelled" else "completed",
                duration_ms=round(max(0, current - item["created_at"]) * 1000), transaction=db,
            )
        return {**item, **result}

    def refresh(self, source: str, session_id: str, *, integration_id="", cancel=False):
        import time
        items = self.list(source, session_id, integration_id=integration_id)
        enabled = interactive_decisions_enabled(load_config(self.store.user, self.store.root))
        for item in items:
            if cancel or not enabled or time.time() >= item["expires_at"]:
                try:
                    self.settle(
                        source, session_id, item["decision_id"],
                        status="cancelled" if cancel or not enabled else "expired",
                    )
                except DecisionClosedError:
                    pass  # Another answer/timeout/close already committed.

    def result_cards(self, integration: IntegrationStore):
        with self.store.db() as db:
            return [Card.from_row(db.execute(
                "SELECT * FROM context_cards WHERE session_key=? AND seq=?",
                (integration.key, row["last_result_seq"]),
            ).fetchone()) for row in self._rows(
                db, integration.source, integration.session_id, integration_id=integration.id,
            ) if not row["execution_live"] and row["last_result_seq"] is not None]
