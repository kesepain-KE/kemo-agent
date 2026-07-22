"""
Telegram Bot health check.

Protocol: check(config, state) -> dict
  - config: dict from message.json (raw)
  - state: current state dict
  Returns: updated state dict with health status
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import telegram

from message.out.telegram.util import load_token, sync_run


def check(config: dict, state: dict) -> dict:
    """Check Telegram Bot connectivity and health."""
    result = dict(state)

    try:
        token = load_token()
    except RuntimeError as exc:
        result["health"] = "dead"
        result["error"] = str(exc)
        return result

    try:
        bot = telegram.Bot(token=token)
        me = sync_run(bot.get_me(), timeout=10.0)

        result["health"] = "healthy"
        result["error"] = None
        result["last_check"] = datetime.now(timezone.utc).isoformat()
        result["_bot_info"] = {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
        }
    except telegram.error.TelegramError as exc:
        result["health"] = "dead"
        result["error"] = f"Telegram API 连接失败：{exc}"
    except Exception as exc:
        result["health"] = "dead"
        result["error"] = f"检测异常：{exc}"

    return result
