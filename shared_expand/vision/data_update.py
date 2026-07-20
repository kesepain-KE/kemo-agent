#!/usr/bin/env python3
"""共享摄像头状态采集入口 — 运行此文件即刷新 input_data.md。

用法:
    python data_update.py

从各摄像头 API 采集在线状态、运动事件，写入 input_data.md，
并同步更新 expand.json 中的 input_health 和时间戳。
"""

import json
from datetime import datetime
from pathlib import Path


def collect_camera_status() -> dict:
    """采集各摄像头状态（对接 RTSP / ONVIF / 摄像头 SDK）"""
    # ↓ 对接实际摄像头 API
    return {
        "front_door": {
            "online": True,
            "resolution": "1920×1080",
            "night_vision": "auto",
            "last_motion": None,
            "last_capture": "2026-07-21 14:58:00",
            "summary": "前门走廊空无一人，光线充足",
        },
        "garage": {
            "online": True,
            "resolution": "1280×720",
            "wide_angle": True,
            "last_motion": "2026-07-21 14:45:00（车辆）",
            "last_capture": "2026-07-21 14:45:00",
            "summary": "车库存有一辆白色轿车，灯光关闭",
        },
        "backyard": {
            "online": True,
            "resolution": "3840×2160",
            "ptz_pan": 0,
            "ptz_tilt": 0,
            "last_motion": None,
            "last_capture": "2026-07-21 13:00:00",
            "summary": "后院草坪整洁，无异常活动",
        },
    }


def render_markdown(data: dict, health: str = "正常") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    labels = {
        "front_door": "前门摄像头",
        "garage": "车库摄像头",
        "backyard": "后院摄像头",
    }

    lines = ["# 共享摄像头状态\n", f"> 最后更新: {now}\n"]
    for cam_id, info in data.items():
        label = labels.get(cam_id, cam_id)
        online_text = "在线" if info["online"] else "离线"
        lines.append(f"## {cam_id} — {label}")
        lines.append(f"- 状态: {online_text}")
        lines.append(f"- 分辨率: {info['resolution']}")
        if "night_vision" in info:
            lines.append(f"- 夜视: {'开启' if info['night_vision'] else '关闭'}（{info['night_vision']}）")
        if "ptz_pan" in info:
            lines.append(f"- 云台角度: 水平 {info['ptz_pan']}° / 垂直 {info['ptz_tilt']}°")
        motion = info.get("last_motion")
        lines.append(f"- 最近运动: {motion or '无'}")
        lines.append(f"- 最后捕获: {info['last_capture']}")
        lines.append(f"- 画面摘要: {info['summary']}\n")

    return "\n".join(lines).strip()


def update() -> None:
    base = Path(__file__).resolve().parent
    data = collect_camera_status()
    md = render_markdown(data)
    (base / "input_data.md").write_text(md, encoding="utf-8")

    meta = json.loads((base / "expand.json").read_text(encoding="utf-8"))
    meta["input_health"] = "正常"
    (base / "expand.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[扩展模块 共享摄像头视觉感知] input_data.md 已更新")


if __name__ == "__main__":
    update()
