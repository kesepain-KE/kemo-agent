## 注入层

本地摄像头视觉拓展可用。可通过本机摄像头拍照、截取屏幕、提取视频帧。使用方法见操作层。

## 操作层

### 可用设备

- `webcam` — 默认 USB/内置摄像头
- `screen` — 当前屏幕截图（主显示器）
- `screen_all` — 所有显示器截图

### 操控接口

通过运行 `start_expand.py` 并传入 JSON 指令来控制：

```bash
python start_expand.py '{"action": "capture", "device": "webcam"}'
python start_expand.py '{"action": "capture", "device": "screen"}'
python start_expand.py '{"action": "describe", "device": "webcam", "prompt": "描述画面中的人物和动作"}'
python start_expand.py '{"action": "video_frame", "video_path": "E:/video.mp4", "time": "00:01:30"}'
python start_expand.py '{"action": "status"}'
```

### 指令说明

| 指令 | 说明 | 参数 |
|------|------|------|
| `capture` | 拍照/截图并保存到 download 目录 | `device`：`webcam`/`screen`/`screen_all` |
| `describe` | 捕获画面并用多模态模型描述 | `device` + `prompt`（可选描述重点） |
| `video_frame` | 从视频文件中提取指定时间戳的帧 | `video_path`：视频文件绝对路径，`time`：`HH:MM:SS` 格式 |
| `status` | 查看所有设备在线状态 | 无 |

### 返回值

成功返回：
```json
{"status": "ok", "device": "webcam", "image_path": "users/kesepain/download/capture_webcam_20260721.jpg", "description": "摄像头画面：一人坐在桌前，正在使用电脑"}
```

失败返回：
```json
{"status": "error", "message": "未检测到摄像头设备"}
```
