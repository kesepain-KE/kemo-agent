"""
Telegram Bot outbound message sender.

Protocol: send(payload) -> bool
  payload dict fields:
    - chat_type: str (private / group / channel)
    - external_chat_id: str (Telegram chat ID)
    - text: str (message text, may be empty)
    - file_path: str | None (path to file to send, may be None)
    - reply_to: str | None (message_id to reply to)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import telegram
from telegram.constants import ParseMode

from message.out.telegram.util import guess_mime, load_token, sync_run


def send(payload: dict) -> bool:
    """Send a message or file to Telegram. Returns True if sent successfully."""
    chat_id = (payload.get("external_chat_id") or "").strip()
    text = (payload.get("text") or "").strip()
    file_path = payload.get("file_path")
    reply_to = payload.get("reply_to")

    if not chat_id:
        raise ValueError("external_chat_id 不能为空")
    if not text and not file_path:
        raise ValueError("text 和 file_path 不能同时为空")

    token = load_token()
    bot = telegram.Bot(token=token)
    reply_id = int(reply_to) if reply_to and reply_to.lstrip("-").isdigit() else None

    try:
        if file_path and Path(file_path).is_file():
            path = Path(file_path)
            mime = guess_mime(path.suffix)

            with path.open("rb") as f:
                if mime.startswith("image/"):
                    sync_run(
                        bot.send_photo(
                            chat_id=chat_id,
                            photo=f,
                            caption=text or None,
                            reply_to_message_id=reply_id,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    )
                elif mime.startswith("audio/"):
                    sync_run(
                        bot.send_audio(
                            chat_id=chat_id,
                            audio=f,
                            caption=text or None,
                            reply_to_message_id=reply_id,
                        )
                    )
                elif mime.startswith("video/"):
                    sync_run(
                        bot.send_video(
                            chat_id=chat_id,
                            video=f,
                            caption=text or None,
                            reply_to_message_id=reply_id,
                        )
                    )
                else:
                    sync_run(
                        bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            caption=text or None,
                            reply_to_message_id=reply_id,
                        )
                    )
        elif text:
            sync_run(
                bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_to_message_id=reply_id,
                    parse_mode=ParseMode.MARKDOWN,
                )
            )
        else:
            raise ValueError("文件和文本都不存在，无法发送")

        return True
    except telegram.error.TelegramError as exc:
        raise RuntimeError(f"Telegram API 错误：{exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"发送失败：{exc}") from exc
