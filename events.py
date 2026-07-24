"""提供者、运行和传输共享的统一运行时事件协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


EventType = Literal[
    "text_delta",
    "reasoning_delta",
    "tool_call_start",
    "tool_call_result",
    "media_output",
    "guidance_applied",
    "usage",
    "error",
    "done",
]

TERMINAL_EVENTS = frozenset({"error", "done"})


@dataclass(slots=True)
class RunEvent:
    type: EventType
    event_id: str = ""
    sequence: int | None = None
    run_sequence: int | None = None
    request_id: str = ""
    response_id: str = ""
    item_id: str = ""
    content_index: int | None = None
    protocol_event_type: str = ""
    content: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] | None = None
    result: Any = None
    usage: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type}
        for key in (
            "event_id",
            "request_id",
            "response_id",
            "item_id",
            "protocol_event_type",
            "content",
            "tool_call_id",
            "tool_name",
        ):
            value = getattr(self, key)
            if value:
                payload[key] = value
        for key in ("sequence", "run_sequence", "content_index"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        for key in ("arguments", "result", "usage", "error"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunEvent":
        event_type = value.get("type")
        if event_type not in {
            "text_delta",
            "reasoning_delta",
            "tool_call_start",
            "tool_call_result",
            "media_output",
            "guidance_applied",
            "usage",
            "error",
            "done",
        }:
            raise ValueError(f"未知事件类型：{event_type!r}")
        return cls(
            type=event_type,
            event_id=str(value.get("event_id") or ""),
            sequence=(int(value["sequence"]) if value.get("sequence") is not None else None),
            run_sequence=(
                int(value["run_sequence"])
                if value.get("run_sequence") is not None
                else None
            ),
            request_id=str(value.get("request_id") or ""),
            response_id=str(value.get("response_id") or ""),
            item_id=str(value.get("item_id") or ""),
            content_index=(
                int(value["content_index"])
                if value.get("content_index") is not None
                else None
            ),
            protocol_event_type=str(value.get("protocol_event_type") or ""),
            content=str(value.get("content") or ""),
            tool_call_id=str(value.get("tool_call_id") or ""),
            tool_name=str(value.get("tool_name") or ""),
            arguments=value.get("arguments"),
            result=value.get("result"),
            usage=value.get("usage"),
            error=value.get("error"),
            metadata=dict(value.get("metadata") or {}),
        )


def error_event(exc: BaseException, *, phase: str = "run") -> RunEvent:
    return RunEvent(
        type="error",
        error={
            "message": str(exc),
            "exception_type": type(exc).__name__,
            "phase": phase,
        },
    )
