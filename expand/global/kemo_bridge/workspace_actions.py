"""Private Web workspace actions; start_expand.execute does not dispatch these."""
from __future__ import annotations

import start_expand
from admin_routes import dispatch
from panel_control import status_payload
from peer_store import BridgeError


def execute(command: str, params: dict, *, context: dict | None = None) -> dict:
    context = context or {}
    source = context.get("source")
    try:
        if source == "module_panel_status" and command == "panel_status":
            return status_payload(start_expand.status())
        if source == "module_panel" and command in {"initialize", "start", "deactivate", "restart"}:
            result = start_expand.execute(command)
            if not result.get("ok"):
                raise BridgeError(409, result.get("message") or result.get("error") or "bridge_action_failed")
            return result if command == "initialize" else {"ok": True}
        if source == "module_ui" and context.get("workspace_instance") is True and command == "bridge_admin":
            return dispatch(params["operation"], params.get("data", {}))
        raise BridgeError(403, "bridge_workspace_action_forbidden")
    except BridgeError as error:
        return {"ok": False, "error": error.code, "status_code": error.status}
