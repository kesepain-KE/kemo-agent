## 注入层

共享摄像头视觉拓展可用。可通过摄像头拍照、获取画面描述、检测运动状态。使用方法见操作层。

## 操作层

### 可用摄像头

- `front_door` — 前门摄像头（1080p，红外夜视）
- `garage` — 车库摄像头（720p，广角）
- `backyard` — 后院摄像头（4K，云台可控）

### 操控接口

通过运行 `start_expand.py` 并传入 JSON 指令来控制摄像头：

```bash
python start_expand.py '{"action": "capture", "camera": "front_door"}'
python start_expand.py '{"action": "describe", "camera": "garage"}'
python start_expand.py '{"action": "status", "camera": "backyard"}'
python start_expand.py '{"action": "motion_check", "camera": "front_door"}'
python start_expand.py '{"action": "ptz", "camera": "backyard", "pan": 45, "tilt": 10}'
```

### 指令说明

| 指令 | 说明 | 参数 |
|------|------|------|
| `capture` | 拍照并保存到用户 download 目录 | `camera`：摄像头名称 |
| `describe` | 拍照并用多模态模型描述画面内容 | `camera`：摄像头名称 |
| `status` | 查询摄像头在线状态和画面摘要 | `camera`：摄像头名称（可选，不传返回全部） |
| `motion_check` | 检查最近 5 分钟内是否有运动事件 | `camera`：摄像头名称 |
| `ptz` | 控制云台转动 | `camera`：摄像头名称，`pan`：水平角度，`tilt`：垂直角度 |

### 返回值

成功返回：
```json
{"status": "ok", "camera": "front_door", "image_path": "users/kesepain/download/capture_front_door_20260721.jpg", "description": "前门画面：无人，画面清晰，光线充足"}
```

失败返回：
```json
{"status": "error", "message": "摄像头 offline", "camera": "garage"}
```
