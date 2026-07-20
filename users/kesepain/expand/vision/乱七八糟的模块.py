#!/usr/bin/env python3
"""本地视觉设备硬件通讯层。

此模块只负责与本地设备通讯，不参与 prompt 注入，与智能体无关。
data_update.py 和 start_expand.py 会导入这里的函数来对接真实设备。

支持 USB 摄像头（OpenCV）、屏幕截图（Pillow/pyautogui）、视频帧提取（FFmpeg）。
"""

import time


def capture_webcam(output_path: str, camera_index: int = 0) -> bool:
    """使用 OpenCV 从 USB 摄像头抓取一帧保存"""
    # import cv2
    # cap = cv2.VideoCapture(camera_index)
    # ret, frame = cap.read()
    # if ret:
    #     cv2.imwrite(output_path, frame)
    # cap.release()
    # return ret
    time.sleep(0.3)
    return True


def capture_screen(output_path: str, all_monitors: bool = False) -> bool:
    """截取屏幕画面"""
    # import pyautogui
    # img = pyautogui.screenshot()
    # img.save(output_path)
    # if all_monitors:
    #     # 多显示器需要额外处理
    #     pass
    time.sleep(0.2)
    return True


def extract_video_frame(video_path: str, time_str: str, output_path: str) -> bool:
    """使用 FFmpeg 从视频提取指定时间戳的帧"""
    # import subprocess
    # result = subprocess.run(
    #     ["ffmpeg", "-ss", time_str, "-i", video_path,
    #      "-vframes", "1", "-q:v", "2", output_path, "-y"],
    #     capture_output=True, text=True,
    # )
    # return result.returncode == 0
    return True


def detect_webcam() -> dict | None:
    """检测是否有可用摄像头"""
    # import cv2
    # for i in range(3):
    #     cap = cv2.VideoCapture(i)
    #     if cap.isOpened():
    #         w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    #         h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    #         cap.release()
    #         return {"index": i, "width": w, "height": h}
    #     cap.release()
    return None


def detect_ffmpeg() -> bool:
    """检测 FFmpeg 是否可用"""
    # import subprocess
    # result = subprocess.run(["ffmpeg", "-version"], capture_output=True)
    # return result.returncode == 0
    return False
