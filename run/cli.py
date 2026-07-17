"""CLI 运行桥。

这个模块故意很小，以便 cron 和消息适配器可以稍后调用
具有自己的源/会话标识符的相同运行引擎。"""

from __future__ import annotations

from typing import Any

from run.engine import handle_request


def handle_cli_request(request: dict[str, Any]) -> dict[str, Any]:
    required = {"user", "prompt", "source", "session_id"}
    missing = sorted(required - request.keys())
    if missing:
        raise ValueError(f"CLI 请求缺少字段：{', '.join(missing)}")
    return handle_request(request)


def stream_cli_request(request: dict[str, Any]):
    required = {"user", "prompt", "source", "session_id"}
    missing = sorted(required - request.keys())
    if missing:
        raise ValueError(f"CLI 请求缺少字段：{', '.join(missing)}")
    from run.engine import iter_request_events

    return iter_request_events(request)


def handle_cli_status(request: dict[str, Any]) -> dict[str, Any]:
    from run.engine import context_status

    return context_status(request)


def handle_cli_compress(request: dict[str, Any]) -> dict[str, Any]:
    from run.engine import compress_context

    return compress_context(request)
