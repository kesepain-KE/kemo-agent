"""Interactive questions exposed through the standard tool-plugin entrypoint."""
from __future__ import annotations

from concurrent.futures import CancelledError
from pathlib import Path
import time
from typing import Any

from run.cards import CardStore
from run.config import interactive_decisions_enabled, load_config
from run.tools import DEFAULT_QUERY_WAIT_SECONDS, DecisionError, DecisionStore, query_wait_seconds, validate_question


_TERMINAL = {"answered", "expired", "cancelled", "resolved_by_agent"}


def run(action: str, *, context: dict[str, Any], **arguments) -> dict[str, Any]:
    store = DecisionStore(CardStore(Path(context["root"]), context["user"]))
    source, session_id = context["source"], context["session_id"]
    if not interactive_decisions_enabled(load_config(context["user"], store.store.root)):
        raise DecisionError("用户尚未启用 interactive_decisions.enabled")
    if action == "ask":
        validate_question(arguments)
        return {"ok": True, **store.get(
            source, session_id, context["decision_execution_id"],
            integration_id=context["integration_id"],
        )}
    decision_id = arguments.get("decision_id", "")
    if not isinstance(decision_id, str) or not decision_id:
        raise DecisionError("decision_id 必须为非空文本")
    if action == "get":
        deadline = (
            time.monotonic() + query_wait_seconds(arguments.get("timeout", DEFAULT_QUERY_WAIT_SECONDS), action="get")
            if arguments.get("wait") is True else None
        )
        cancel_event = context.get("cancel_event") if deadline is not None else None
        while True:
            store.refresh(source, session_id, integration_id=context["integration_id"])
            snapshot = store.get(source, session_id, decision_id)
            if deadline is None:
                return {"ok": True, **snapshot}
            if snapshot["status"] in _TERMINAL:
                return {"ok": True, **snapshot, "wait_outcome": "finished"}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"ok": True, **snapshot, "wait_outcome": "timeout"}
            interval = min(2.0, remaining)
            if cancel_event is None:
                time.sleep(interval)
            elif cancel_event.wait(interval):
                raise CancelledError("已停止等待用户决策")
    if action == "close":
        return store.settle(
            source, session_id, decision_id, status="resolved_by_agent",
            resolution=arguments.get("resolution", ""),
        )
    raise DecisionError("action 必须为 ask、get 或 close")
