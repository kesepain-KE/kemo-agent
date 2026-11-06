"""Secret-safe configuration helpers for the kemo app user panel."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from initialize_config import initialize


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
PANEL_STATUS_PATH = BASE_DIR / "module" / "status.json"
USERS_DIR = BASE_DIR.parent.parent / "users"
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _atomic_json(path: Path, payload: Any, *, sensitive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    if sensitive:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        initialize(BASE_DIR)
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("config.json 顶层必须是 JSON 对象")
    return value


def _users(config: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    path = BASE_DIR / str(config.get("users_path") or "users.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("users.json 顶层必须是 JSON 对象")
    return path, value


def set_port(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        raise ValueError("端口必须是 1～65535 的整数")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("端口必须是 1～65535 的整数") from exc
    if port != value and not (isinstance(value, str) and value.strip() == str(port)):
        raise ValueError("端口必须是整数")
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1～65535 之间")
    config = _config()
    config["port"] = port
    _atomic_json(CONFIG_PATH, config, sensitive=True)
    return {"ok": True, "port": port}


def set_device_token(token: Any) -> dict[str, Any]:
    rendered = str(token or "")
    if len(rendered) < 32 or len(rendered) > 4096:
        raise ValueError("设备 Token 长度必须在 32～4096 字符之间")
    if any(ord(character) < 32 or ord(character) == 127 for character in rendered):
        raise ValueError("设备 Token 不能包含控制字符")
    config = _config()
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    config["token_sha256"] = digest
    _atomic_json(CONFIG_PATH, config, sensitive=True)
    return {
        "ok": True,
        "token_configured": True,
        "token_fingerprint": f"{digest[:10]}...{digest[-10:]}",
    }


def bind_user(app_username: Any, agent_username: Any) -> dict[str, Any]:
    app_user = str(app_username or "").strip()
    agent_user = str(agent_username or "").strip()
    if not USER_RE.fullmatch(app_user):
        raise ValueError("App 用户名格式无效")
    if not USER_RE.fullmatch(agent_user):
        raise ValueError("智能体用户名格式无效")
    if not (USERS_DIR / agent_user).is_dir():
        raise ValueError(f"智能体用户不存在：{agent_user}")
    config = _config()
    users_path, users = _users(config)
    record = users.get(app_user)
    if not isinstance(record, dict) or not record.get("salt") or not record.get("hash"):
        raise ValueError(f"App 用户尚未创建密码账户：{app_user}")
    users[app_user] = {**record, "agent_user": agent_user}
    _atomic_json(users_path, users, sensitive=True)
    return {"ok": True, "app_user": app_user, "agent_user": agent_user}


def unbind_user(app_username: Any) -> dict[str, Any]:
    app_user = str(app_username or "").strip()
    if not USER_RE.fullmatch(app_user):
        raise ValueError("App 用户名格式无效")
    config = _config()
    users_path, users = _users(config)
    record = users.get(app_user)
    if not isinstance(record, dict):
        raise ValueError(f"App 用户不存在：{app_user}")
    updated = dict(record)
    updated.pop("agent_user", None)
    users[app_user] = updated
    _atomic_json(users_path, users, sensitive=True)
    return {"ok": True, "app_user": app_user, "agent_user": app_user}


def public_status(runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime = dict(runtime or {})
    try:
        config = _config() if CONFIG_PATH.is_file() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        config = {}
    try:
        _, users = _users(config) if config else (BASE_DIR / "users.json", {})
    except (OSError, ValueError, json.JSONDecodeError):
        users = {}
    token_hash = str(config.get("token_sha256") or "").strip().lower()
    bindings = {
        str(username): str(record.get("agent_user") or username)
        for username, record in sorted(users.items())
        if isinstance(record, dict)
        and bool(record.get("enabled", True))
        and record.get("salt")
        and record.get("hash")
    }
    host = str(config.get("host") or "127.0.0.1")
    try:
        port = int(config.get("port", 8742))
    except (TypeError, ValueError):
        port = 8742
    running = bool(runtime.get("running"))
    active = bool(runtime.get("active"))
    return {
        "bridge_state": "在线" if active else "端口已有服务" if running else "未运行",
        "endpoint": f"{host}:{port}",
        "exposed_port": port,
        "token_status": "已配置" if len(token_hash) == 64 else "未配置",
        "token_fingerprint": (
            f"{token_hash[:10]}...{token_hash[-10:]}" if len(token_hash) == 64 else "—"
        ),
        "bindings": bindings,
        "configured_users": len(bindings),
        "activation": "已激活" if runtime.get("activated") else "未激活",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "Token 只保存 SHA-256，不可反向查看明文；页面展示配置状态与核对指纹。",
    }


def write_status(runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = public_status(runtime)
    _atomic_json(PANEL_STATUS_PATH, payload)
    return payload
