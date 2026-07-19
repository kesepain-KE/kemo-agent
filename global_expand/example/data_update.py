#!/usr/bin/env python3
"""数据采集入口 — 运行此文件即刷新 input_data.md。

用法:
    python data_update.py

从各传感器/API 采集灯光状态，写入 input_data.md，
并同步更新 expand.json 中的 input_health 和时间戳。
"""

import json
from datetime import datetime
from pathlib import Path


def collect_light_status() -> dict:
    """采集各灯组状态（对接实际硬件/API）"""
    return {
        "living_room": {"state": "on", "brightness": 80, "color_temp": 4000, "power_w": 24},
        "bedroom":     {"state": "off", "brightness": 0,  "color_temp": None,  "power_w": 0},
        "hallway":     {"state": "on",  "brightness": 30, "color_temp": 2700, "power_w": 6},
    }


def render_markdown(data: dict, health: str = "正常") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"# 智能灯光状态\n", f"> 最后更新: {now}\n"]

    area_labels = {
        "living_room": "客厅灯组",
        "bedroom": "卧室灯组",
        "hallway": "走廊灯组",
    }

    for area_key, info in data.items():
        label = area_labels.get(area_key, area_key)
        state_text = "开启" if info["state"] == "on" else "关闭"
        lines.append(f"## {label}")
        lines.append(f"- 状态: {state_text}")
        lines.append(f"- 亮度: {info['brightness']}%")
        if info["color_temp"]:
            lines.append(f"- 色温: {info['color_temp']}K")
        else:
            lines.append(f"- 色温: —")
        lines.append(f"- 功率: {info['power_w']}W\n")

    return "\n".join(lines).strip()


def update() -> None:
    base = Path(__file__).resolve().parent
    data = collect_light_status()
    md = render_markdown(data)
    (base / "input_data.md").write_text(md, encoding="utf-8")

    meta = json.loads((base / "expand.json").read_text(encoding="utf-8"))
    meta["input_health"] = "正常"
    meta["recent_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    (base / "expand.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[扩展模块 智能灯光控制] input_data.md 已更新")


if __name__ == "__main__":
    update()
