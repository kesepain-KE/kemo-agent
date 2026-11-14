"""Shared tokenizer view accounting. Persisting a card never changes occupancy."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Callable, Iterable

from run.cards.errors import CardCapacityError, CardConflictError
from run.cards.models import Card, historical_data_text, json_text, user_content
from provider.tokenizer import TOKENIZER_ID, count_value, count_items, count_request
from run.tools import TOOL_RESULT_VIEW_VERSION


def estimate_tokens(value: Any) -> int:
    """Count canonical request text with the framework-wide DeepSeek tokenizer."""
    return count_value(value)


def estimate_items_tokens(items) -> int:
    return count_items(items)


def estimate_request_tokens(system_prompt: str, items, tools) -> int:
    return count_request(system_prompt, items, tools)


def request_calibration_key(meter_key: str, calibration_scope: str, items) -> str:
    """Keep usage-derived safety calibration isolated by media composition."""
    media = set()
    for item in items:
        value = item.model_dump(mode="json", exclude_none=True) if hasattr(item, "model_dump") else item
        if value.get("type") == "message":
            media.update(block["type"] for block in value.get("content") or []
                         if block.get("type") in {"image", "audio", "video", "file"})
    return hashlib.sha256(json_text(
        [meter_key, calibration_scope, sorted(media)]
    ).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CardMeter:
    key: str = f"cardstream-{TOKENIZER_ID}-{TOOL_RESULT_VIEW_VERSION}"
    counter: Callable[[Any], int] = estimate_tokens
    calibration_scope: str = "default"

    def tokens(self, kind: str, payload: dict[str, Any]) -> int:
        # RAW can contain signed state, diagnostic usage and a duplicate plain
        # text projection. Count what the model sees, not every persisted field.
        if kind in {"user_text", "agent_text"}:
            visible = {"role": "user" if kind == "user_text" else "assistant",
                       "content": user_content(payload) if kind == "user_text"
                       else payload.get("content") or payload.get("text") or ""}
            if kind == "agent_text":
                visible["provider_state"] = [state["provider_state"]
                    for state in payload.get("provider_state") or []
                    if isinstance(state, dict) and isinstance(state.get("provider_state"), dict)]
        elif kind == "summary":
            # The summary is sent as a labelled JSON string inside a message.
            # Include that encoding here so quote/path escapes are not free.
            visible = {"role": "user", "content": historical_data_text("rolling_summary", payload)}
        elif kind == "tool_call":
            visible = {key: payload.get(key) for key in ("name", "arguments")}
        else:
            visible = payload
        count = self.counter({"kind": kind, "payload": visible})
        if type(count) is not int or count < 0:
            raise ValueError("Token counter must return a nonnegative integer")
        return count + 8  # Item/role framing, not just visible payload bytes.

    @classmethod
    def for_model(cls, provider_type: str, model: str, *, counter=None) -> CardMeter:
        scope = hashlib.sha256(json_text([
            str(provider_type or "").casefold(),
            str(model or "").casefold(),
        ]).encode()).hexdigest()
        return cls(
            key=f"cardstream-{TOKENIZER_ID}-{TOOL_RESULT_VIEW_VERSION}",
            counter=counter or estimate_tokens,
            calibration_scope=scope,
        )


def effective_cards(*collections: Iterable[Card]) -> tuple[Card, ...]:
    """Union first, then filter: an in-memory inactive revision must hide old DB data."""
    selected: dict[str, Card] = {}
    for collection in collections:
        for card in collection:
            previous = selected.get(card.card_id)
            if previous is not None:
                if previous.session_key != card.session_key:
                    raise CardConflictError("Card identity crosses conversation spaces")
                if previous.view_revision == card.view_revision and previous != card:
                    raise CardConflictError("The same card revision has conflicting content")
            if previous is None or card.view_revision > previous.view_revision:
                selected[card.card_id] = card
    return tuple(sorted((c for c in selected.values() if c.token_active),
                        key=lambda c: (c.session_key, c.seq)))


@dataclass(frozen=True, slots=True)
class RequestOccupancy:
    fixed_tokens: int
    history_tokens: int
    dynamic_tokens: int
    schema_tokens: int
    input_limit: int

    def __post_init__(self):
        values = (self.fixed_tokens, self.history_tokens, self.dynamic_tokens, self.schema_tokens)
        if any(type(value) is not int or value < 0 for value in values) or self.input_limit < 1:
            raise ValueError("Invalid CardStream input capacity")

    @property
    def total(self) -> int:
        return self.fixed_tokens + self.history_tokens + self.dynamic_tokens + self.schema_tokens

    @property
    def fixed_occupancy(self) -> int:
        return self.total - self.history_tokens

    def should_compact(self, trigger_ratio: float = .8) -> bool:
        if not 0 < trigger_ratio < 1:
            raise ValueError("Compression trigger ratio must be between zero and one")
        return self.history_tokens > 0 and self.total > self.input_limit * trigger_ratio

    def require_fixed_capacity(self) -> None:
        if self.fixed_occupancy >= self.input_limit:
            raise CardCapacityError("固定上下文、动态尾部和工具 Schema 已占满输入容量，历史压缩无法解决")

    def require_capacity(self) -> None:
        if self.total > self.input_limit:
            raise CardCapacityError(f"CardStream request exceeds input capacity: {self.total}>{self.input_limit}")
