## 注入层

智能灯光扩展可用。支持指令：开关灯、调亮度、调色温。使用方法见操作层。

## 操作层

### 可用区域
- `living_room` — 客厅灯组
- `bedroom` — 卧室灯组
- `hallway` — 走廊灯组

### 操控接口

通过运行 `start_expand.py` 并传入 JSON 指令来控制灯光：

```bash
python start_expand.py '{"action": "switch", "area": "living_room", "value": "on"}'
python start_expand.py '{"action": "brightness", "area": "bedroom", "value": 60}'
python start_expand.py '{"action": "color_temp", "area": "living_room", "value": 3500}'
python start_expand.py '{"action": "scene", "area": "hallway", "value": "night"}'
```

### 指令说明

| 指令 | 说明 | value 类型 |
|------|------|------------|
| `switch` | 开关灯 | `"on"` / `"off"` |
| `brightness` | 设置亮度 | 整数 0–100 |
| `color_temp` | 设置色温 | 整数 2700–6500（K） |
| `scene` | 场景模式 | `"night"` / `"reading"` / `"movie"` |

### 返回值

成功返回 `{"status": "ok"}`，失败返回 `{"status": "error", "message": "..."}`。
