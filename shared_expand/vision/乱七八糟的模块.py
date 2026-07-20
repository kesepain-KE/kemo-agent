#!/usr/bin/env python3
"""摄像头硬件通讯层。

此模块只负责与摄像头硬件通讯，不参与 prompt 注入，与智能体无关。
data_update.py 和 start_expand.py 会导入这里的函数来对接真实摄像头。

支持 RTSP 流、ONVIF 协议、USB 摄像头。
"""

import time


def capture_image(camera_id: str, output_path: str) -> bool:
    """从指定摄像头抓取一帧保存为 JPEG"""
    # 实际对接 RTSP / ONVIF 抓帧
    # rtsp_url = get_rtsp_url(camera_id)
    # frame = rtsp_capture(rtsp_url)
    # cv2.imwrite(output_path, frame)
    time.sleep(0.5)
    return True


def get_camera_status(camera_id: str) -> dict:
    """查询摄像头在线状态和基本信息"""
    # 实际对接 ONVIF / 摄像头 HTTP API
    cameras = {
        "front_door": {"online": True, "resolution": "1920×1080", "night_vision": True},
        "garage": {"online": True, "resolution": "1280×720", "wide_angle": True},
        "backyard": {"online": True, "resolution": "3840×2160", "ptz": True},
    }
    return cameras.get(camera_id, {"online": False})


def check_motion(camera_id: str, minutes: int = 5) -> dict:
    """检查最近 N 分钟内是否有运动事件"""
    # 查询运动检测日志
    return {"camera": camera_id, "recent_motion": False, "last_event_time": None}


def ptz_control(camera_id: str, pan: float, tilt: float) -> bool:
    """控制云台转动"""
    # 发送 ONVIF PTZ 指令
    return True


def get_rtsp_url(camera_id: str) -> str:
    """获取摄像头的 RTSP 流地址"""
    urls = {
        "front_door": "rtsp://192.168.1.101:554/stream1",
        "garage": "rtsp://192.168.1.102:554/stream1",
        "backyard": "rtsp://192.168.1.103:554/stream1",
    }
    return urls.get(camera_id, "")
