"""The private CardStream behind a local agent's Provider requests."""
from __future__ import annotations

import copy
import json
from bisect import bisect_right
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from provider.protocol.enums import MessageRole
from provider.protocol.models import Item, JsonContent, MessageItem, ReasoningItem, ToolCallItem, ToolResultItem, text_from_content
from run.cards import (
    Card, CardConflictError, CardMeter, CardSnapshot, CardStore, DynamicTail, IntegrationStore,
    RequestSnapshot, json_text, session_key,
)
from run.config import user_dir
from run.tools import tool_call_signature
from run.agents.continuation import TASK_ID, decode_legacy_items, private_data
from run.agents.retry_policy import _agent_recovery_items


SOURCE = "subagent"
_MEDIA = {"image", "audio", "video", "file"}


def begin_model_attempt(context, runtime, input_data, retry_state) -> AgentCardStream:
    state = context.continuation
    initial = state.initial
    stream = context.card_stream
    if stream is None:
        stream = context.card_stream = AgentCardStream(context, runtime)
        stream.start(MessageItem.text(
            MessageRole.USER, json.dumps(input_data, ensure_ascii=False, sort_keys=True),
        ), initial)
        state.stream = stream
    else:
        stream.retry(resume=bool(initial), recovery=_agent_recovery_items(retry_state.recovery)
                     if initial is None else [])
    if initial is not None:
        guidance = str(initial.get("guidance") or "")
        notice = MessageItem.text(
            MessageRole.USER,
            ("[框架重试安全点恢复，运行预算不重置] " if initial.get("retry_resume") else "[框架原任务续跑] ")
            + "保留上一轮已完成工作，不重复已有副作用；"
            "结果未知的操作先核对。继续剩余任务，按当前权限与 output_schema 交付。" + guidance,
        )
        if guidance:
            stream.message(notice, "guidance")
        else:
            stream.control("resume", notice)
        retry_state.usage = copy.deepcopy(initial.get("usage") or {})
        retry_state.response_ids = list(initial.get("response_ids") or [])
        state.initial = None
    return stream


def agent_store(root, user, task_id: str, *, meter=None) -> CardStore:
    if not TASK_ID.fullmatch(task_id):
        raise ValueError("子代理 CardStream 任务 ID 无效")
    path = user_dir(user, root) / "runtime" / "subagent_stores" / task_id / "cards.sqlite3"
    return CardStore(root, user, meter=meter, store_path=path)


def integration_id(task_id: str, run_count: int) -> str:
    return f"{task_id}:run:{run_count}"


def read_checkpoint(store: CardStore, task_id: str, run_count: int) -> dict[str, Any]:
    # CardStore's ordinary connection may initialize a database. Resume must
    # never turn missing history into a new task with the old identity.
    if not store.path.is_file():
        raise ValueError("子代理续跑卡片库不存在")
    key = session_key(SOURCE, task_id)
    with store.db() as db:
        row = db.execute(
            "SELECT i.generation, i.response_closed, i.record_json, s.last_seq, s.lifecycle "
            "FROM card_integrations i JOIN card_sessions s ON i.session_key=s.session_key "
            "WHERE i.integration_id=? AND i.session_key=? AND i.generation=s.generation",
            (integration_id(task_id, run_count), key),
        ).fetchone()
        if row is not None:
            checkpoint = json.loads(row["record_json"]).get("agent_checkpoint")
            if (isinstance(checkpoint, dict) and checkpoint.get("version") == 2
                    and checkpoint.get("task_id") == task_id and checkpoint.get("run_count") == run_count
                    and checkpoint.get("generation") == row["generation"]
                    and checkpoint.get("last_seq") == row["last_seq"]
                    and row["lifecycle"] == "open" and row["response_closed"]
                    and checkpoint.get("available") is True
                    and not db.execute(
                        "SELECT 1 FROM card_tool_executions WHERE integration_id=? "
                        "AND execution_live=1 LIMIT 1", (integration_id(task_id, run_count),),
                    ).fetchone()):
                return checkpoint
    raise ValueError("子代理续跑卡片安全点不可用或身份不匹配")


class AgentCardStream:
    def __init__(self, context, runtime):
        task_id = context.task_id
        if not TASK_ID.fullmatch(task_id):
            task_id = f"agent-task-{uuid4().hex}"
        self.task_id = task_id
        self.model = runtime["model"]
        self.store = agent_store(context.runner.root, context.runner.user, task_id,
                                 meter=CardMeter.for_model(runtime["type"], self.model))
        self.run_count = context.continuation.run_count
        self.integration = IntegrationStore(self.store, SOURCE, task_id,
                                            integration_id(task_id, self.run_count))
        self.opened = False
        self.head = 0
        self.initial_seq = 0
        self.retry_after = 0
        self.retry_revision = 0
        self._control: dict[str, tuple[int, MessageItem]] = {}
        self._ephemeral: dict[int, list[Item]] = {}
        self._reasoning: list[tuple[list[int], list[ReasoningItem]]] = []
        self._messages: dict[str, dict[str, Any]] = {}
        self._media_messages: dict[str, dict[str, Any]] = {}
        self._arguments: dict[str, dict[str, Any]] = {}
        self._results: dict[str, Any] = {}
        self._calls: dict[str, str] = {}
        self._legacy_unknown: set[str] = set()
        self.shell_jobs: set[str] = set()

    def start(self, initial: MessageItem, restored: dict[str, Any] | None) -> None:
        if restored and restored["version"] == 2:
            saved = read_checkpoint(self.store, self.task_id, self.run_count - 1)
            if any(saved[key] != restored.get(key) for key in (
                "source", "session_id", "definition_digest", "generation", "last_seq",
            )):
                raise ValueError("子代理续跑安全点在入队后发生改变")
        elif self.store.path.is_file():
            old = self.store.session(SOURCE, self.task_id)
            if old is not None and old["last_seq"]:
                raise CardConflictError("子代理卡片库已经包含历史，拒绝覆盖或重复导入")
        state = self.integration.start()
        self.opened = True
        self.head = state["last_seq"]
        if state["meter_key"] != self.store.meter.key:
            self.store.remeter(SOURCE, self.task_id)
        if restored and restored["version"] == 1:
            self._import_legacy(restored)
        elif not restored:
            self.initial_seq = self.message(initial, "initial", event_key="agent-input").seq
        else:
            with self.store.db() as db:
                for row in db.execute(
                    "SELECT raw_payload FROM context_cards WHERE session_key=? "
                    "AND event_key LIKE 'shell-notice:%'", (self.integration.key,),
                ):
                    self.shell_jobs.update(json.loads(row["raw_payload"])["job_ids"])
                for row in db.execute(
                    "SELECT x.original_call FROM card_tool_executions x JOIN card_integrations i "
                    "ON x.integration_id=i.integration_id WHERE i.session_key=? "
                    "AND x.status='interrupted_unknown'", (self.integration.key,),
                ):
                    original = json.loads(row["original_call"])
                    self._legacy_unknown.add(tool_call_signature(original["name"], original["arguments"]))

    def control(self, name: str, item: MessageItem, *, first: bool = False) -> None:
        self._control[name] = (0 if first else self.head, item)

    def retry(self, *, resume: bool, recovery: list[Any]) -> None:
        if not resume:
            # Calls without a resumable scope historically restart the prompt
            # and see the duplicate-prevention receipts, not old tool output.
            self.retry_after = self.head
            snapshot = self.store.snapshot(SOURCE, self.task_id)
            self.retry_revision = snapshot.cards[0].view_revision
            self._ephemeral.clear()
            self._reasoning.clear()
            self._media_messages.clear()
            for item in recovery:
                self._ephemeral.setdefault(self.initial_seq, []).append(item)

    def visible_history(self, snapshot: CardSnapshot) -> CardSnapshot:
        if not self.retry_after:
            return snapshot
        cards = []
        for card in snapshot.cards:
            if card.seq == self.initial_seq and card.view_revision <= self.retry_revision:
                # A prior attempt's rolling summary must not resurrect the
                # abandoned prompt. Recover the original input from RAW.
                card = replace(card, view_kind=card.raw_kind, view_payload_json=card.raw_payload_json,
                               view_tokens=card.raw_tokens)
            elif card.seq <= self.retry_after and card.seq != self.initial_seq:
                continue
            cards.append(card)
        return replace(snapshot, cards=tuple(cards), history_tokens=sum(card.view_tokens for card in cards))

    def request_items(self, request: RequestSnapshot) -> list[Item]:
        cards = request.history.cards
        positions = [0, *(card.seq for card in cards)]
        anchors = {card.seq: previous for previous, card in zip(positions, cards)
                   if card.view_kind != "summary"}
        extras: dict[int, list[Item]] = {}
        for seq, item in self._control.values():
            extras.setdefault(positions[bisect_right(positions, seq) - 1], []).append(item)
        for seq, items in self._ephemeral.items():
            extras.setdefault(positions[bisect_right(positions, seq) - 1], []).extend(
                item for item in items if not isinstance(item, ReasoningItem) or seq in anchors
            )
        for seqs, items in self._reasoning:
            for seq in seqs:
                if seq in anchors:
                    extras.setdefault(anchors[seq], []).extend(items)
                    break
        now = datetime.now(timezone.utc)
        for seq, items in extras.items():
            valid = []
            for item in items:
                if isinstance(item, ReasoningItem) and (state := item.provider_state):
                    expires = state.expires_at
                    if ((state.model and state.model != self.model) or
                            (expires is not None and (expires.tzinfo is None or expires <= now))):
                        if not (item.content or item.summary):
                            continue
                        item = item.model_copy(update={"provider_state": None})
                valid.append(item)
            extras[seq] = valid
        live = replace(request.history, cards=tuple(self._live_card(card) for card in cards))
        return replace(request, history=live).items(native_items=True, transient_after=extras)

    def prune_reasoning(self, snapshot: CardSnapshot) -> None:
        # Only a committed VIEW can retire state; candidate projections must not mutate it.
        visible = {card.seq for card in snapshot.cards if card.view_kind != "summary"}
        self._reasoning = [entry for entry in self._reasoning if visible.intersection(entry[0])]

    def project(self) -> list[Item]:
        snapshot = self.visible_history(self.store.snapshot(SOURCE, self.task_id))
        return self.request_items(RequestSnapshot(snapshot, "", DynamicTail(), "[]", model=self.model))

    def _live_card(self, card: Card) -> Card:
        if card.view_kind == "summary":
            return card
        call_id = card.tool_call_id
        arguments = self._arguments.get(call_id) if call_id is not None else None
        message = self._media_messages.get(card.card_id) or self._messages.get(card.card_id)
        if message is not None:
            return replace(card, view_payload_json=json_text(message))
        result_changed = card.card_id in self._results
        if arguments is None and not result_changed and card.view_kind != "tool_result":
            return card
        view = card.view_payload or {}
        receipt = view.get("result")
        private_reference = receipt if (isinstance(receipt, dict) and receipt.get("content_omitted")
                                        and receipt.get("raw_card_id") == card.card_id) else None
        if arguments is None and not result_changed and private_reference is None:
            return card
        raw = card.raw_payload
        if arguments is not None and card.view_kind == "tool_call":
            view["arguments"] = arguments
        elif arguments is not None and card.view_kind == "tool_result":
            raw["original_call"]["arguments"] = arguments
            original = view["original_call"]
            if not original.get("arguments_omitted"):
                original["arguments"] = arguments
        if result_changed and view.get("result") == card.raw_payload.get("result"):
            view["result"] = self._results[card.card_id]
        if private_reference is not None:
            del private_reference["raw_card_id"]
            private_reference["instruction"] = "原文仅保存于子代理卡片库，不能通过主对话历史查找；不要因正文省略重跑工具。"
        return replace(card, view_payload_json=json_text(view), raw_payload_json=json_text(raw))

    def _append(self, kind, payload, *, level=None, call_id=None, event_key=None, db=None) -> Card:
        card = self.store.append(
            SOURCE, self.task_id, self.integration.id, kind, payload,
            level=level, tool_call_id=call_id, event_key=event_key,
            expected_generation=self.integration.generation, transaction=db,
        )
        self.head = max(self.head, card.seq)
        return card

    def message(self, item: MessageItem, level=None, *, event_key=None, db=None, live=True, media=None) -> Card:
        kind = "agent_text" if item.role == MessageRole.ASSISTANT else "user_text"
        payload = item.model_dump(mode="json", exclude_none=True, include={
            "content", "phase", "refusal", "status", "annotations", "logprobs",
        })
        payload["text"] = text_from_content(item.content)
        blocks = payload.get("content", [])
        native = None
        if any(block["type"] in _MEDIA for block in blocks):
            native = payload
            payload = {key: value for key, value in payload.items() if key != "annotations"}
            payload["content"] = [block for block in blocks if block["type"] not in _MEDIA]
            if not payload["content"] and not payload.get("refusal"):
                payload["text"] = "[原生媒体只随一次请求提供]"
        safe = private_data(payload)
        card = self._append(kind, safe, level=level, event_key=event_key, db=db)
        if live and safe != payload:
            self._messages[card.card_id] = payload
        if media is not None and native is not None:
            media[card.card_id] = native
        return card

    def guidance(self, messages: list[str], delivered: int) -> None:
        with self.store.db(write=True) as db:
            for i, text in enumerate(messages, start=delivered + 1):
                self.message(MessageItem.text(
                    MessageRole.USER, "[主智能体运行中指导；不改变当前权限与用户授权]\n" + text,
                ), "guidance", event_key=f"{self.integration.id}:guidance:{i}", db=db)

    def _register(self, call: ToolCallItem, db) -> None:
        original = {"name": call.name, "arguments": call.arguments}
        safe = cast(dict[str, Any], private_data(original))
        execution, created = self.integration.register_tool(
            call.call_id, safe, planned=True, transaction=db,
        )
        if not created:
            raise CardConflictError("子代理已接受过此工具调用，拒绝重新登记")
        self._calls[call.call_id] = execution["execution_id"]
        if original != safe:
            self._arguments[call.call_id] = call.arguments
        self.head += 1

    def start_tool(self, call: ToolCallItem) -> None:
        self.integration.start_registered_tool(self._calls[call.call_id])

    def heartbeat_cards(self) -> list[dict[str, Any]]:
        from run.agents.public_trace import public_tool_data

        with self.store.db() as db:
            rows = db.execute(
                "SELECT seq,raw_kind,raw_payload FROM context_cards "
                "WHERE session_key=? AND seq<=? AND (raw_kind IN ('tool_call','tool_result') "
                "OR (raw_kind='agent_text' AND raw_level IN ('interim','final'))) "
                "ORDER BY seq DESC LIMIT 10", (self.integration.key, self.head),
            ).fetchall()
        cards = []
        for row in rows:
            kind = row["raw_kind"]
            payload = json.loads(row["raw_payload"])
            card = {"seq": row["seq"], "kind": kind}
            if kind == "agent_text":
                card["summary"] = payload.get("text", "")
            elif kind == "tool_call":
                card.update(tool_name=payload.get("name", ""), summary="已登记工具调用")
            else:
                status = payload["execution"]["status"]
                card.update(tool_name=payload["original_call"]["name"], status=status,
                            summary={"completed": "工具执行成功", "failed": "工具执行失败"}.get(status, "已记录工具回执"))
            card = public_tool_data(card)
            card["summary"] = card["summary"][:200]
            if card["summary"]:
                cards.append(card)
        return cards

    def unknown_legacy_call(self, call: ToolCallItem) -> bool:
        return bool(self._legacy_unknown) and tool_call_signature(
            call.name, cast(dict[str, Any], private_data(call.arguments))) in self._legacy_unknown

    def response(self, output: list[Any], shell_notices=()) -> None:
        reasoning: list[tuple[list[int], list[ReasoningItem]]] = []
        pending: list[ReasoningItem] = []
        attached: list[int] | None = None
        next_media: dict[str, dict[str, Any]] = {}
        with self.store.db(write=True) as db:
            self.head = db.execute("SELECT last_seq FROM card_sessions WHERE session_key=?",
                                   (self.integration.key,)).fetchone()[0]
            for job_ids, text in shell_notices:
                self._append("user_text", private_data({"text": text, "job_ids": list(job_ids)}),
                             event_key="shell-notice:" + ",".join(job_ids), db=db)
                self.shell_jobs.update(job_ids)
            for item in output:
                if isinstance(item, ReasoningItem):
                    pending.append(item)
                    attached = None
                    continue
                previous = self.head
                if isinstance(item, MessageItem):
                    attached = None
                    level = "final" if item.phase == "final_answer" else "interim"
                    self.message(item, level if item.role == MessageRole.ASSISTANT else None,
                                 db=db, media=next_media)
                elif isinstance(item, ToolCallItem):
                    self._register(item, db)
                elif isinstance(item, ToolResultItem):
                    attached = None
                    self._provider_result(item, db)
                if self.head == previous:
                    continue
                if pending:
                    seqs = [self.head]
                    reasoning.append((seqs, pending))
                    pending = []
                    if isinstance(item, ToolCallItem) or (
                        isinstance(item, MessageItem) and item.role == MessageRole.ASSISTANT
                    ):
                        attached = seqs
                elif attached is not None:
                    # Chat merges adjacent calls with the preceding assistant
                    # message. Reasoning survives while any of those cards do.
                    attached.append(self.head)
        self._reasoning.extend(reasoning)
        # Controls and reasoning without a following card are request-local.
        self._control.clear()
        self._ephemeral = {self.head: cast(list[Item], pending)} if pending else {}
        self._media_messages = next_media

    def _provider_result(self, item: ToolResultItem, db) -> None:
        body = item.content[0].data if len(item.content) == 1 and isinstance(
            item.content[0], JsonContent) and isinstance(item.content[0].data, dict) else [
                block.model_dump(mode="json") for block in item.content if block.type not in _MEDIA
            ]
        if not body and isinstance(body, list):
            body = {"ok": not item.is_error, "content_omitted": True}
        status = "failed" if item.is_error else "completed"
        if item.call_id in self._calls:
            if isinstance(body, dict) and body.get("not_executed") is True:
                self._unknown(self._calls[item.call_id], db)
            else:
                self.result(item.call_id, body, status, db=db)
        else:
            self._append("tool_result", private_data({
                "original_call": {"name": item.name, "arguments": {}},
                "execution": {"status": status, "execution_live": False}, "result": body,
            }), call_id=item.call_id, db=db)

    def result(self, call_id: str, payload, status: str, *, duration_ms=0, db=None) -> None:
        self._write_result(self._calls[call_id], payload, status, duration_ms, db=db)

    def _write_result(self, execution_id, payload, status, duration_ms=0, *, db=None) -> None:
        safe = private_data(payload)
        card = self.integration.tool_result(
            execution_id, safe, status=status, duration_ms=duration_ms,
            execution_live=status == "timed_out_running", transaction=db,
        )
        self.head = max(self.head, card.seq)
        if safe != payload:
            self._results[card.card_id] = payload

    def _unknown(self, execution_id: str, db) -> None:
        original = json.loads(db.execute(
            "SELECT original_call FROM card_tool_executions WHERE execution_id=?", (execution_id,),
        ).fetchone()[0])
        self._legacy_unknown.add(tool_call_signature(original["name"], original["arguments"]))
        self._write_result(execution_id, {"ok": False, "error": {
            "code": "legacy_execution_unknown",
            "message": "旧续跑快照未能证明此调用的执行结果。先核对副作用，不要重复执行。",
        }}, "interrupted_unknown", db=db)

    def _import_legacy(self, restored) -> None:
        items = decode_legacy_items(restored)
        if not items or not isinstance(items[0], MessageItem) or items[0].role != MessageRole.USER:
            raise ValueError("旧子代理续跑快照缺少初始任务")
        with self.store.db(write=True) as db:
            record = json.loads(self.integration._row(db)["record_json"])
            if record.get("legacy_imported_run") == restored["run_count"]:
                return
            for item in items:
                if isinstance(item, MessageItem):
                    self.message(item, db=db, live=False)
                elif isinstance(item, ToolCallItem):
                    self._register(item, db)
                elif isinstance(item, ToolResultItem):
                    self._provider_result(item, db)
            for row in self._planned(db):
                self._unknown(row["execution_id"], db)
            record["legacy_imported_run"] = restored["run_count"]
            db.execute("UPDATE card_integrations SET record_json=? WHERE integration_id=?",
                       (json_text(record), self.integration.id))

    def _planned(self, db):
        return db.execute(
            "SELECT execution_id FROM card_tool_executions WHERE integration_id=? "
            "AND execution_live=1 AND status='planned' ORDER BY rowid", (self.integration.id,),
        ).fetchall()

    def settle_unstarted(self, *, transaction=None) -> None:
        with (nullcontext(transaction) if transaction is not None else self.store.db(write=True)) as db:
            for row in self._planned(db):
                self._write_result(row["execution_id"], {
                    "ok": False, "not_executed": True,
                    "error": {"message": "上一轮在执行此调用前停止。未执行此操作；请根据已有结果重新决定。"},
                }, "not_executed", db=db)

    def checkpoint(self, payload: dict[str, Any], *, transaction=None) -> bool:
        with (nullcontext(transaction) if transaction is not None else self.store.db(write=True)) as db:
            row = self.integration._row(db)
            record = json.loads(row["record_json"])
            seq = db.execute("SELECT last_seq FROM card_sessions WHERE session_key=?",
                             (self.integration.key,)).fetchone()[0]
            available = bool(seq) and not db.execute(
                "SELECT 1 FROM card_tool_executions WHERE integration_id=? "
                "AND execution_live=1 LIMIT 1", (self.integration.id,),
            ).fetchone()
            record["agent_checkpoint"] = private_data({
                **payload, "available": available, "generation": row["generation"], "last_seq": seq,
            })
            db.execute("UPDATE card_integrations SET record_json=? WHERE integration_id=?",
                       (json_text(record), self.integration.id))
            return available

    def finish(self, status: str, *, checkpoint: dict[str, Any] | None) -> bool:
        with self.store.db(write=True) as db:
            self.settle_unstarted(transaction=db)
            if status == "completed":
                self.integration.request_finished(accepted_seq=self.head, transaction=db)
            self.integration.finish(status, transaction=db)
            return self.checkpoint(checkpoint, transaction=db) if checkpoint is not None else False
