"""CLI 运行桥。

这个模块故意很小，以便 cron 和消息适配器可以稍后调用
具有自己的源/会话标识符的相同运行引擎。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from run.config import project_root
from run.engine import handle_request
from run.history import (
    get_or_reserve_active as get_or_reserve_history_session,
)


def resolve_interactive_context(
    user: str, *, root: Path | None = None
) -> dict[str, str]:
    """Resolve the Web/CLI shared interactive binding for CLI presentation."""

    base = (root or project_root()).resolve()
    active, _ = get_or_reserve_history_session(
        base,
        user,
        "web",
        f"interactive:{user}",
        reuse_latest=True,
    )
    return {
        "source": str(active.get("source") or "web"),
        "session_id": str(active.get("session_id") or ""),
    }


def _interactive_request(
    request: dict[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    payload = dict(request)
    user = str(payload.get("user") or "").strip()
    if not user:
        raise ValueError("CLI 请求缺少字段：user")
    base = (root or project_root()).resolve()
    active_key = f"interactive:{user}"
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        # Web and CLI intentionally share one canonical interactive binding.
        context = resolve_interactive_context(user, root=base)
        session_id = context["session_id"]
        payload["source"] = context["source"]
    else:
        payload.setdefault("source", "cli")
    payload["session_id"] = session_id
    payload["_history_active_key"] = active_key
    return payload


def handle_cli_request(
    request: dict[str, Any], *, root: Path | None = None
) -> dict[str, Any]:
    required = {"user", "prompt"}
    missing = sorted(required - request.keys())
    if missing:
        raise ValueError(f"CLI 请求缺少字段：{', '.join(missing)}")
    return handle_request(_interactive_request(request, root=root), root=root)


def stream_cli_request(request: dict[str, Any], *, root: Path | None = None):
    required = {"user", "prompt"}
    missing = sorted(required - request.keys())
    if missing:
        raise ValueError(f"CLI 请求缺少字段：{', '.join(missing)}")
    from run.engine import iter_request_events

    return iter_request_events(_interactive_request(request, root=root), root=root)


def handle_cli_status(request: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    from run.engine import context_status

    return context_status(_interactive_request(request, root=root), root=root)


def handle_cli_compress(request: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    from run.engine import compress_context

    return compress_context(_interactive_request(request, root=root), root=root)
