#!/usr/bin/env python3
"""本地摄像头操控入口。

智能体通过传入 JSON 指令控制本地视觉设备：拍照、截图、画面描述、视频帧提取。

用法:
    python start_expand.py '{"action": "capture", "device": "webcam"}'
    python start_expand.py '{"action": "capture", "device": "screen"}'
    python start_expand.py '{"action": "describe", "device": "webcam", "prompt": "描述人物动作"}'
    python start_expand.py '{"action": "video_frame", "video_path": "E:/video.mp4", "time": "00:01:30"}'
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# 实际项目应导入本地设备模块
# import cv2
# import pyautogui

BASE = Path(__file__).resolve().parent
USER_DOWNLOAD = BASE.parent.parent / "download"


def execute(command: dict) -> dict:
    action = command.get("action")
    device = command.get("device", "")

    # ↓ 对接实际本地设备

    if action == "capture":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_path = USER_DOWNLOAD / f"capture_{device}_{timestamp}.jpg"

        if device == "webcam":
            # cap = cv2.VideoCapture(0)
            # ret, frame = cap.read()
            # cv2.imwrite(str(image_path), frame)
            # cap.release()
            pass
        elif device in ("screen", "screen_all"):
            # img = pyautogui.screenshot()
            # img.save(str(image_path))
            pass
        else:
            return {"status": "error", "message": f"未知设备: {device}"}

        return {
            "status": "ok",
            "device": device,
            "image_path": str(image_path.relative_to(BASE.parent.parent)),
        }

    elif action == "describe":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_path = USER_DOWNLOAD / f"capture_{device}_{timestamp}.jpg"
        prompt = command.get("prompt", "描述画面内容")
        return {
            "status": "ok",
            "device": device,
            "image_path": str(image_path.relative_to(BASE.parent.parent)),
            "description": f"[需调用多模态模型分析 {image_path}，描述重点: {prompt}]",
        }

    elif action == "video_frame":
        video_path = command.get("video_path", "")
        time_str = command.get("time", "00:00:00")
        if not Path(video_path).is_file():
            return {"status": "error", "message": f"视频文件不存在: {video_path}"}
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        frame_path = USER_DOWNLOAD / f"frame_{timestamp}.jpg"
        # ffmpeg -ss {time_str} -i {video_path} -vframes 1 {frame_path}
        return {
            "status": "ok",
            "action": "video_frame",
            "video_path": video_path,
            "time": time_str,
            "frame_path": str(frame_path.relative_to(BASE.parent.parent)),
        }

    elif action == "status":
        return {
            "status": "ok",
            "devices": {
                "webcam": {"online": True, "resolution": "1280×720"},
                "screen": {"online": True, "resolution": "2560×1440"},
                "screen_all": {"online": True},
                "video_frame": {"online": True, "ffmpeg": True},
            },
        }

    return {"status": "error", "message": f"未知指令: {action}"}


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python start_expand.py '<json指令>'")
        sys.exit(1)

    try:
        command = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        result = {"status": "error", "message": f"JSON 解析失败: {e}"}
    else:
        result = execute(command)

    print(json.dumps(result, ensure_ascii=False))
    if result.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
