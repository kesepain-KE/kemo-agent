"""Non-secret status snapshot for the host module workspace."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from bridge_config import BASE_DIR, USERS_DIR
from initialize_config import _atomic_json
from lifecycle import inspect_configuration
from peer_store import PeerStore

PANEL_STATUS_PATH = BASE_DIR / "module" / "status.json"


def peer_overview() -> dict[str, Any]:
    database = BASE_DIR / "_bridge_peers.sqlite3"
    if not database.is_file():
        return dict(total=0, device=0, agent=0, active=0, last_seen_at=None,
                    identity_mismatch_24h=0, denied_24h=0)
    return PeerStore(database, USERS_DIR).overview()


def status_payload(runtime: dict | None = None) -> dict:
    runtime = runtime or {}
    config = inspect_configuration(BASE_DIR)
    summary = peer_overview()
    if runtime.get("active"):
        state = "在线"
    elif runtime.get("unmanaged_process") or runtime.get("stale_pid"):
        state = "异常：进程归属或健康状态未确认"
    elif not config["initialized"]:
        state = "未初始化"
    elif not config["configured"]:
        state = "待配置"
    else:
        state = "未运行" if runtime.get("activated") else "已停止"
    return {
        "bridge_state": state,
        "public_url": config["public_url"] or "未设置",
        "port": config["port"],
        "total": summary["total"],
        "active": summary["active"],
        "denied_24h": summary["denied_24h"],
    }


def write_status(runtime: dict | None = None) -> dict:
    payload = status_payload(runtime)
    _atomic_json(PANEL_STATUS_PATH, payload)
    return payload
