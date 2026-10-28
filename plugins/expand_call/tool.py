"""Provider tool adapter for isolated Expand operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from run.extensions import invoke_expand
from run.agents import require_non_agent_expand_command


def run(
    scope: str,
    module: str,
    command: str,
    params: dict[str, Any] | None = None,
    timeout: int = 0,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    root = Path(str(context.get("root") or Path.cwd())).resolve()
    user = str(context.get("user") or "").strip()
    if not user:
        raise ValueError("工具上下文缺少 user")
    if context.get("caller") == "subagent":
        require_non_agent_expand_command(root, user, scope, module, command)
    raw_timeout = timeout or context.get("tool_timeout")
    try:
        effective_timeout = float(raw_timeout if raw_timeout is not None else "")
    except (TypeError, ValueError) as exc:
        raise ValueError("拓展工具超时必须是正数") from exc
    if effective_timeout <= 0:
        raise ValueError("拓展工具超时必须是正数")
    return invoke_expand(
        root=root,
        user=user,
        scope=scope,
        module=module,
        command=command,
        params=params,
        timeout=min(effective_timeout, 3600.0),
        cancel_event=context.get("cancel_event"),
    )
