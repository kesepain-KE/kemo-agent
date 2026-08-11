"""kemo app 桥接服务状态采集 — 写入 input_data.md 供 Prompt 注入。"""

from __future__ import annotations

import json
import os
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_PATH = os.path.join(BASE_DIR, "input_data.md")
CONNECTIONS_PATH = os.path.join(BASE_DIR, "_connections.json")


def _load_config() -> dict:
    try:
        with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"host": "127.0.0.1", "port": 8742}


def _load_connections() -> dict:
    try:
        with open(CONNECTIONS_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_label(value: object) -> str:
    return str(value or "unknown").replace("`", "").replace("\n", " ").strip() or "unknown"


def update() -> dict:
    cfg = _load_config()
    host = cfg.get("host", "127.0.0.1")
    port = int(cfg.get("port", 8742))
    url = f"http://{host}:{port}/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        connections = _load_connections()
        devices = connections.get("devices") if isinstance(connections.get("devices"), list) else []
        lines = [
            "# kemo app 桥接服务",
            "",
            f"- 状态: **在线**",
            f"- 服务: {data.get('display_name') or data.get('service')} v{data.get('version')}",
            f"- 端口: {host}:{port}",
            f"- 上游: {data.get('upstream', 'unknown')}",
            f"- WebSocket 连接: {int(connections.get('websocket_connections') or data.get('websocket_connections') or 0)}",
            f"- 在线设备: {int(connections.get('connected_devices') or data.get('connected_devices') or 0)}",
        ]
        if devices:
            lines.extend(["", "## 在线设备"])
            for device in devices:
                if not isinstance(device, dict):
                    continue
                lines.append(
                    f"- 用户: `{_safe_label(device.get('user'))}`；设备 ID: `{_safe_label(device.get('device_id'))}`；连接数: {int(device.get('connections') or 1)}"
                )
        status = "online"
    except Exception as e:
        lines = [
            "# kemo app 桥接服务",
            "",
            f"- 状态: **离线**（{type(e).__name__}）",
            f"- 端口: {host}:{port}",
        ]
        status = "offline"
    with open(INPUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return {"ok": True, "status": status}


def main():
    outcome = update()
    print(json.dumps(outcome, ensure_ascii=False), flush=True)
    raise SystemExit(0 if outcome.get("ok") else 1)


if __name__ == "__main__":
    main()
