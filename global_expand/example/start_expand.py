#!/usr/bin/env python3
"""智能体操控外部拓展的唯一入口。

智能体通过传入 JSON 指令控制灯光，接口见 expand_control.md 操作层。

用法:
    python start_expand.py '{"action": "switch", "area": "living_room", "value": "on"}'
    python start_expand.py '{"action": "brightness", "area": "bedroom", "value": 60}'
"""

import json
import sys
from pathlib import Path


def execute(command: dict) -> dict:
    """执行操控指令（对接实际硬件/API）"""
    action = command.get("action")
    area = command.get("area")
    value = command.get("value")

    # ↓ 这里对接实际的硬件控制接口（MQTT / HTTP / 串口等）
    print(f"  执行: area={area}, action={action}, value={value}")

    if action == "switch":
        return {"status": "ok", "area": area, "state": value}
    elif action == "brightness":
        return {"status": "ok", "area": area, "brightness": value}
    elif action == "color_temp":
        return {"status": "ok", "area": area, "color_temp": value}
    elif action == "scene":
        return {"status": "ok", "area": area, "scene": value}
    else:
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
