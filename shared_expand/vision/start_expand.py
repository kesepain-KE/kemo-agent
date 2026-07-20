#!/usr/bin/env python3
"""共享摄像头操控入口。

智能体通过传入 JSON 指令控制共享摄像头：拍照、画面描述、运动检测、云台控制。

用法:
    python start_expand.py '{"action": "capture", "camera": "front_door"}'
    python start_expand.py '{"action": "describe", "camera": "garage"}'
    python start_expand.py '{"action": "motion_check", "camera": "front_door"}'
    python start_expand.py '{"action": "ptz", "camera": "backyard", "pan": 45, "tilt": 10}'
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# 实际项目应导入摄像头硬件模块
# from 乱七八糟的模块 import capture_image, get_camera_status

BASE = Path(__file__).resolve().parent


def execute(command: dict) -> dict:
    action = command.get("action")
    camera = command.get("camera", "")

    valid_cameras = {"front_door", "garage", "backyard"}
    if camera and camera not in valid_cameras:
        return {"status": "error", "message": f"未知摄像头: {camera}"}

    # ↓ 对接实际摄像头 API（RTSP / ONVIF / SDK 等）

    if action == "capture":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_path = f"users/kesepain/download/capture_{camera}_{timestamp}.jpg"
        # capture_image(camera, image_path)
        return {
            "status": "ok",
            "camera": camera,
            "image_path": image_path,
            "message": f"已保存截图到 {image_path}",
        }

    elif action == "describe":
        # 拍照 + 调用多模态模型描述
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_path = f"users/kesepain/download/capture_{camera}_{timestamp}.jpg"
        return {
            "status": "ok",
            "camera": camera,
            "image_path": image_path,
            "description": f"[需调用多模态模型分析 {image_path}]",
        }

    elif action == "status":
        if camera:
            return {"status": "ok", "camera": camera, "online": True}
        return {
            "status": "ok",
            "cameras": {
                "front_door": {"online": True, "resolution": "1920×1080"},
                "garage": {"online": True, "resolution": "1280×720"},
                "backyard": {"online": True, "resolution": "3840×2160"},
            },
        }

    elif action == "motion_check":
        return {
            "status": "ok",
            "camera": camera,
            "recent_motion": False,
            "last_event": None,
        }

    elif action == "ptz":
        pan = command.get("pan", 0)
        tilt = command.get("tilt", 0)
        return {
            "status": "ok",
            "camera": camera,
            "pan": pan,
            "tilt": tilt,
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
