"""
Telegram Bot inbound message handler.

Protocol: start(config, buffer_path, files_path, state_path) / stop()
  - config: dict from message.json (raw)
  - buffer_path: path to message.md queue file
  - files_path: path to files/ directory
  - state_path: path to state.json

Architecture:
  start() launches an asyncio event loop in a daemon thread.
  The bot uses python-telegram-bot's Application.polling to receive updates.
  Each incoming message is parsed and written to message.md as YAML front matter blocks.
  Attachments are downloaded to files/ and referenced in the front matter.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from message.out.telegram.util import load_token

# --- Module-level state ---
_BOT_APP: Any = None
_LOOP: asyncio.AbstractEventLoop | None = None
_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()
_LOCK = threading.Lock()

_MACHINE_ID: str = ""
_BUFFER_PATH: Path | None = None
_FILES_PATH: Path | None = None
_PLUGIN_DIR: Path | None = None


def _write_message(message_data: dict, body: str) -> None:
    """将一条结构化消息追加到 message.md 队列文件。"""
    with _LOCK:
        path = _BUFFER_PATH
        if path is None:
            return
        front = {}
        for key in (
            "machine_id", "message_id", "chat_type",
            "external_user_id", "external_chat_id", "timestamp",
        ):
            front[key] = message_data[key]
        attachments = message_data.get("attachments")
        if attachments:
            front["attachments"] = attachments

        with path.open("a", encoding="utf-8") as f:
            f.write("---\n")
            f.write(
                yaml.dump(
                    front,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                    width=120,
                )
            )
            f.write("---\n")
            if body:
                f.write(body + "\n\n")


async def _save_file(file_obj: Any) -> dict | None:
    """下载 Telegram 文件到 files/ 目录，返回附件描述或 None。"""
    try:
        file_id = file_obj.file_id
        suffix = Path(getattr(file_obj, "file_path", file_id) or file_id).suffix or ""
        safe_name = f"{file_id}{suffix}"
        mime = getattr(file_obj, "mime_type", None) or "application/octet-stream"

        dest = (_FILES_PATH / safe_name) if _FILES_PATH else None
        if dest is None:
            return None

        await file_obj.download_to_drive(dest)

        size = dest.stat().st_size
        return {
            "path": f"files/{safe_name}",
            "name": getattr(file_obj, "file_path", safe_name).rsplit("/", 1)[-1] or safe_name,
            "mime": mime,
            "size": size,
        }
    except Exception as exc:
        print(f"[Telegram input] 文件下载失败：{exc}")
        return None


async def _build_message(update: Any) -> tuple[dict, str] | None:
    """从 Telegram Update 提取结构化消息，返回 (message_data, body)。"""
    msg = update.effective_message
    if msg is None:
        return None

    chat = msg.chat
    chat_type = chat.type if chat else "private"
    if chat_type == "private":
        mapped = "private"
    elif chat_type in ("group", "supergroup"):
        mapped = "group"
    elif chat_type == "channel":
        mapped = "channel"
    else:
        mapped = "private"

    external_user_id = str(msg.from_user.id) if msg.from_user else "0"
    external_chat_id = str(chat.id) if chat else "0"
    message_id = str(msg.message_id)
    timestamp = datetime.fromtimestamp(msg.date.timestamp(), tz=timezone.utc).isoformat()
    text = msg.text or msg.caption or ""

    attachments = []

    if msg.photo:
        photo = msg.photo[-1]
        att = await _save_file(await photo.get_file())
        if att:
            attachments.append(att)
            if not text:
                text = "[图片]"

    if msg.document:
        doc = msg.document
        att = await _save_file(await doc.get_file())
        if att:
            att["name"] = doc.file_name or att["name"]
            attachments.append(att)
            if not text:
                text = "[文件]"

    if msg.audio:
        audio = msg.audio
        att = await _save_file(await audio.get_file())
        if att:
            attachments.append(att)
            if not text:
                text = "[音频]"

    if msg.voice:
        voice = msg.voice
        att = await _save_file(await voice.get_file())
        if att:
            attachments.append(att)
            if not text:
                text = "[语音]"

    if msg.video:
        video = msg.video
        att = await _save_file(await video.get_file())
        if att:
            attachments.append(att)
            if not text:
                text = "[视频]"

    if msg.sticker:
        sticker = msg.sticker
        if sticker.emoji:
            text = text or f"[贴纸] {sticker.emoji}"
        else:
            text = text or "[贴纸]"

    message_data = {
        "machine_id": _MACHINE_ID,
        "message_id": message_id,
        "chat_type": mapped,
        "external_user_id": external_user_id,
        "external_chat_id": external_chat_id,
        "timestamp": timestamp,
    }
    if attachments:
        message_data["attachments"] = attachments

    return message_data, text


def _update_last_message_at(timestamp: str) -> None:
    """更新 state.json 中的 last_message_at。"""
    try:
        state_path = (_PLUGIN_DIR / "state.json") if _PLUGIN_DIR else None
        if state_path and state_path.is_file():
            state = json.loads(state_path.read_text("utf-8"))
            state["last_message_at"] = timestamp
            tmp = state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", "utf-8")
            tmp.replace(state_path)
    except Exception:
        pass


async def _run_bot(token: str) -> None:
    """运行 Telegram Bot Application (async 主循环)。"""
    from telegram import Update
    from telegram.ext import Application, MessageHandler, filters

    app = Application.builder().token(token).build()

    async def handler(update: Update, _context: Any) -> None:
        try:
            result = await _build_message(update)
            if result is None:
                return
            message_data, body = result
            _write_message(message_data, body)
            _update_last_message_at(message_data["timestamp"])
        except Exception as exc:
            print(f"[Telegram input] 消息处理异常：{exc}")

    # Slash commands must reach the platform-neutral router.  Platform input
    # modules only forward them and do not implement command semantics.
    app.add_handler(MessageHandler(filters.COMMAND, handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handler))

    global _BOT_APP
    _BOT_APP = app

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    while not _STOP_EVENT.is_set():
        await asyncio.sleep(0.5)

    await app.updater.stop()
    await app.stop()
    await app.shutdown()


def start(config: dict, buffer_path: str, files_path: str, state_path: str) -> None:
    """在后台线程中启动 Telegram Bot 长轮询。"""
    global _THREAD, _BUFFER_PATH, _FILES_PATH, _MACHINE_ID, _PLUGIN_DIR

    _BUFFER_PATH = Path(buffer_path)
    _FILES_PATH = Path(files_path)
    _PLUGIN_DIR = Path(__file__).resolve().parent
    _MACHINE_ID = config.get("machine_id", "tg-mybot-001")
    _STOP_EVENT.clear()
    token = load_token()

    def _run() -> None:
        global _LOOP
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
        try:
            _LOOP.run_until_complete(_run_bot(token))
        finally:
            _LOOP.close()
            _LOOP = None

    _THREAD = threading.Thread(target=_run, daemon=True, name="telegram-bot")
    _THREAD.start()


def stop() -> None:
    """优雅停止 Telegram Bot。"""
    global _BOT_APP, _THREAD, _LOOP
    _STOP_EVENT.set()
    if _THREAD and _THREAD.is_alive():
        _THREAD.join(timeout=15)
    _BOT_APP = None
    _THREAD = None
    _LOOP = None
