#!/usr/bin/env python3
"""本地视觉设备状态采集入口 — 运行此文件即刷新 input_data.md。

用法:
    python data_update.py

检测本机摄像头、屏幕、FFmpeg 可用性，写入 input_data.md，
并同步更新 expand.json 中的 input_health 和时间戳。
"""

import json
from datetime import datetime
from pathlib import Path


def collect_device_status() -> dict:
    """检测本地视觉设备状态"""
    # ↓ 对接实际设备检测 API
    webcam_available = False
    screen_available = True
    ffmpeg_available = False

    # 尝试检测摄像头
    # try:
    #     import cv2
    #     cap = cv2.VideoCapture(0)
    #     webcam_available = cap.isOpened()
    #     cap.release()
    # except Exception:
    #     pass

    # 尝试检测 FFmpeg
    # import subprocess
    # result = subprocess.run(["ffmpeg", "-version"], capture_output=True)
    # ffmpeg_available = result.returncode == 0

    return {
        "webcam": {
            "online": webcam_available,
            "device_name": "USB 2.0 Camera",
            "resolution": "1280×720",
            "fps": 30,
            "last_capture": None,
        },
        "screen": {
            "online": screen_available,
            "primary_resolution": "2560×1440",
            "refresh_rate": "144Hz",
            "scale": "125%",
            "last_capture": None,
        },
        "video_frame": {
            "online": ffmpeg_available,
            "formats": ["mp4", "avi", "mkv", "mov", "webm"],
            "ffmpeg_available": ffmpeg_available,
        },
    }


def render_markdown(data: dict, health: str = "正常") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = ["# 本地视觉设备状态\n", f"> 最后更新: {now}\n"]

    cam = data["webcam"]
    lines.append("## webcam — USB 摄像头")
    lines.append(f"- 状态: {'在线' if cam['online'] else '离线'}")
    lines.append(f"- 设备名称: {cam['device_name']}")
    lines.append(f"- 分辨率: {cam['resolution']}")
    lines.append(f"- 帧率: {cam['fps']} fps")
    lines.append(f"- 最后捕获: {cam['last_capture'] or '无'}\n")

    screen = data["screen"]
    lines.append("## screen — 屏幕截图")
    lines.append(f"- 状态: {'可用' if screen['online'] else '不可用'}")
    lines.append(f"- 主显示器: {screen['primary_resolution']} @ {screen['refresh_rate']}")
    lines.append(f"- 缩放: {screen['scale']}")
    lines.append(f"- 最后截图: {screen['last_capture'] or '无'}\n")

    vf = data["video_frame"]
    lines.append("## video_frame — 视频帧提取")
    online = vf['online']
    lines.append(f"- 状态: {'可用（依赖 FFmpeg）' if online else '不可用（FFmpeg 未安装）'}")
    lines.append(f"- 支持格式: {', '.join(vf['formats'])}")

    return "\n".join(lines).strip()


def update() -> None:
    base = Path(__file__).resolve().parent
    data = collect_device_status()
    md = render_markdown(data)
    (base / "input_data.md").write_text(md, encoding="utf-8")

    meta = json.loads((base / "expand.json").read_text(encoding="utf-8"))
    meta["input_health"] = "正常"
    (base / "expand.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[扩展模块 本地摄像头视觉感知] input_data.md 已更新")


if __name__ == "__main__":
    update()
