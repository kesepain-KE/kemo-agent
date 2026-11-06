"""Offline fixtures and assertions against real CardStream storage."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from run.cards import CardStore, IntegrationStore, protocol_items, session_key
from run.history import reserve_session
from run.infra import merge_usage_dict, new_usage_total


@dataclass
class SessionHistory:
    root: Path
    user: str
    source: str
    session_id: str

    @property
    def store(self):
        return CardStore(self.root, self.user)

    def refresh(self):
        return self

    @property
    def cards(self):
        result, after = [], 0
        while True:
            page = self.store.raw_page(self.source, self.session_id, after_seq=after, limit=500)
            result.extend(page)
            if len(page) < 500:
                return result
            after = page[-1].seq

    @property
    def messages(self):
        return [
            {"role": "user" if card.raw_kind == "user_text" else "assistant",
             "content": card.raw_payload.get("text", ""), "level": card.raw_level,
             "attachments": card.raw_payload.get("attachments", []),
             "metadata": card.raw_payload.get("metadata", {})}
            for card in self.cards if card.raw_kind in {"user_text", "agent_text"}
        ]

    @property
    def integrations(self):
        with self.store.db() as db:
            rows = db.execute(
                "SELECT * FROM card_integrations WHERE session_key=? ORDER BY created_at,integration_id",
                (session_key(self.source, self.session_id),),
            ).fetchall()
        return [
            {**json.loads(row["record_json"]), "integration_id": row["integration_id"],
             "status": row["status"], "response_closed": bool(row["response_closed"])}
            for row in rows
        ]

    @property
    def usage(self):
        result = new_usage_total()
        for integration in self.integrations:
            merge_usage_dict(result, integration.get("usage", {}))
        return result

    @property
    def tools(self):
        return [
            {"id": card.tool_call_id, "name": card.raw_payload["original_call"]["name"],
             "arguments": card.raw_payload["original_call"].get("arguments", {}),
             "result": card.raw_payload.get("result"),
             "status": card.raw_payload["execution"]["status"],
             "elapsed_ms": card.raw_payload["execution"].get("duration_ms", 0),
             "duplicate": card.raw_payload["execution"]["status"].startswith("duplicate")}
            for card in self.cards if card.raw_kind == "tool_result"
        ]

    @property
    def items(self):
        return [
            item.model_dump(mode="json", exclude_none=True)
            for item in protocol_items(self.store.snapshot(self.source, self.session_id).cards)
        ]

    @property
    def reasoning(self):
        return [{"content": card.raw_payload.get("reasoning", "")}
                for card in self.cards if card.raw_kind == "agent_text"]


def session_history(root, user, source, session_id):
    store = CardStore(root, user)
    if not store.path.is_file() or store.session(source, session_id) is None:
        return None
    return SessionHistory(Path(root), user, source, session_id)


def seed_history(root, user, source, session_id, messages, *, metadata=None, usage=None):
    """Create real integrations; never create, read, or emulate a window."""
    reserve_session(root, user, source, session_id)
    store = CardStore(root, user)
    integration = None
    for message in messages:
        if integration is None or message["role"] == "user":
            if integration is not None:
                integration.request_started()
                integration.request_finished(accepted_seq=store.snapshot(source, session_id).last_seq)
                integration.finish("completed")
            integration = IntegrationStore(store, source, session_id, "fixture_" + uuid4().hex)
            integration.start()
        kind = "user_text" if message["role"] == "user" else "agent_text"
        payload = {"text": message.get("content", "")}
        for key in ("metadata", "attachments"):
            if key in message:
                payload[key] = message[key]
        store.append(source, session_id, integration.id, kind, payload,
                     level="initial" if kind == "user_text" else "final")
    if integration is not None:
        integration.request_started()
        integration.request_finished(accepted_seq=store.snapshot(source, session_id).last_seq)
        integration.finish("completed", metadata={"usage": usage or {}})
    if metadata:
        store.update_record(source, session_id, metadata)
    return SessionHistory(Path(root), user, source, session_id)
