"""提供者、运行和传输共享的统一运行时事件协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


EventType = Literal[
    "text_delta",
    "reasoning_delta",
    "tool_call_start",
    "tool_call_result",
    "usage",
    "error",
    "done",
]

TERMINAL_EVENTS = frozenset({"error", "done"})


@dataclass(slots=True)
class RunEvent:
    type: EventType
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
        for key in ("content", "tool_call_id", "tool_name"):
            value = getattr(self, key)
            if value:
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
            "usage",
            "error",
            "done",
        }:
            raise ValueError(f"未知事件类型：{event_type!r}")
        return cls(
            type=event_type,
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
