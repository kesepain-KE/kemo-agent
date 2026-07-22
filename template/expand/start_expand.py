"""
拓展模块操控入口模板。

== 用途 ==
复制到 */expand/<name>/start_expand.py，提供操控命令的统一调度。
主智能体通过 shell 调用：python start_expand.py <command> [json_params]

== 修 改 指 南 ==
1. 定义操控函数：每个函数接收参数 → 执行操作 → 返回 {"ok": True/False, ...}
2. 注册到 COMMANDS 字典：键名即命令名
3. 不需要修改 execute() 和 __main__ 块

== 返回值规范 ==
成功: {"ok": True, "action": "操作名", ...具体数据...}
失败: {"ok": False, "error": "错误描述"}
"""

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_MD = os.path.join(BASE_DIR, "input_data.md")
DATA_UPDATE = os.path.join(BASE_DIR, "data_update.py")


# ═══════════════════════════════════════════════════
# 修改此处：定义操控函数
# ═══════════════════════════════════════════════════

def example_action(param: str = "") -> dict:
    """
    【示例】替换为实际操控函数。

    Args:
        param: 参数说明

    Returns:
        {"ok": True, "action": "example_action", "result": ...}
    """
    # TODO: 实现实际操控逻辑
    return {"ok": True, "action": "example_action", "param": param}


# ═══════════════════════════════════════════════════
# 修改此处：注册命令（键=命令名，值=函数引用）
# ═══════════════════════════════════════════════════

COMMANDS = {
    "example_action": example_action,
}


# ═══════════════════════════════════════════════════
# 以下为统一调度入口，不需要修改
# ═══════════════════════════════════════════════════

def execute(command: str, params: dict = None) -> str:
    """统一入口：根据命令名分发到对应函数，返回 JSON 字符串"""
    if not command:
        return json.dumps({"ok": False, "error": "缺少命令参数"})

    if command not in COMMANDS:
        return json.dumps({
            "ok": False,
            "error": f"未知命令: {command}，可用: {list(COMMANDS.keys())}"
        })

    try:
        params = params or {}
        result = COMMANDS[command](**params)
        return json.dumps(result, ensure_ascii=False)
    except TypeError as e:
        return json.dumps({"ok": False, "error": f"参数错误: {e}"})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({
            "ok": False,
            "error": "用法: python start_expand.py <command> [json_params]"
        }))
        sys.exit(1)

    cmd = sys.argv[1]
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print(execute(cmd, params))
