"""Telegram 示例入口共享的最小工具。

这是示例模块自己的内部实现，不属于框架强制目录结构。复制模板后可以保留、
替换或移入任意内部包，只要 input/output/detect 的公开入口仍能正常导入。
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import threading
from typing import Any


def load_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("环境变量 TELEGRAM_BOT_TOKEN 未设置")
    return token


def guess_mime(suffix: str) -> str:
    normalized = str(suffix or "").strip()
    candidate = f"file{normalized}" if normalized.startswith(".") else normalized
    guessed, _encoding = mimetypes.guess_type(candidate)
    return guessed or "application/octet-stream"


def sync_run(awaitable: Any, *, timeout: float = 30.0) -> Any:
    """Safely wait for one Telegram coroutine from sync framework callbacks."""

    async def wait() -> Any:
        return await asyncio.wait_for(awaitable, timeout=max(0.1, float(timeout)))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(wait())

    result: list[Any] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            result.append(asyncio.run(wait()))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, name="telegram-sync-call", daemon=True)
    thread.start()
    thread.join(timeout=max(1.0, float(timeout) + 1.0))
    if thread.is_alive():
        raise TimeoutError("Telegram 协程等待超时")
    if errors:
        raise errors[0]
    return result[0] if result else None
