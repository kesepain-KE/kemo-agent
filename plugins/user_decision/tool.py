"""Interactive questions exposed through the standard tool-plugin entrypoint."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from run.cards import CardStore
from run.config import interactive_decisions_enabled, load_config
from run.tools.user_decisions import DecisionError, DecisionStore, validate_question


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
        store.refresh(source, session_id, integration_id=context["integration_id"])
        return {"ok": True, **store.get(
            source, session_id, decision_id, integration_id=context["integration_id"],
        )}
    if action == "close":
        return store.settle(
            source, session_id, decision_id, status="resolved_by_agent",
            resolution=arguments.get("resolution", ""), integration_id=context["integration_id"],
        )
    raise DecisionError("action 必须为 ask、get 或 close")
