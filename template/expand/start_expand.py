"""拓展模块操控入口模板。

主路径由框架的 ``expand_call`` 工具在隔离子进程中调用 ``execute()``。
本文件只演示协议边界，不规定命令系统或内部工程结构。可以重写整个文件，
也可以让 ``execute()`` 包装模块目录内的任意程序或完整开源项目。
人工调试推荐把完整 JSON 请求写入 stdin，其中命令名和参数由模块自行定义：

    echo '{"command":"<实际命令名>","params":{}}' | python start_expand.py

为兼容旧模块，也接受 ``python start_expand.py <command> [json_params]`` 和
``python start_expand.py '{"action":"<实际命令名>"}'``。
"""

from __future__ import annotations

import json
import sys
from typing import Any


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="strict")


def execute(command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """最小协议占位；按需求直接实现或转交任意内部工程。"""

    if not command:
        return {"ok": False, "error": "缺少命令参数"}
    return {
        "ok": False,
        "error": f"未知命令或拓展操控入口尚未实现: {command}",
        "command": command,
        "data": dict(params or {}),
    }


def _stdin_json() -> Any:
    if sys.stdin.isatty():
        return None
    raw = sys.stdin.read().strip()
    return json.loads(raw) if raw else None


def _request() -> tuple[str, dict[str, Any]]:
    stdin_value = _stdin_json()
    if isinstance(stdin_value, dict) and isinstance(stdin_value.get("command"), str):
        params = stdin_value.get("params", {})
        if not isinstance(params, dict):
            raise TypeError("params 必须是 JSON 对象")
        return stdin_value["command"], params
    if len(sys.argv) >= 2 and not sys.argv[1].lstrip().startswith("{"):
        if stdin_value is not None:
            if not isinstance(stdin_value, dict):
                raise TypeError("stdin 参数必须是 JSON 对象")
            return sys.argv[1], stdin_value
        params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        if not isinstance(params, dict):
            raise TypeError("params 必须是 JSON 对象")
        return sys.argv[1], params
    legacy = stdin_value
    if legacy is None and len(sys.argv) == 2:
        legacy = json.loads(sys.argv[1])
    if isinstance(legacy, dict):
        command = legacy.get("action") or legacy.get("command")
        if not isinstance(command, str) or not command:
            raise ValueError("JSON 指令缺少 command/action")
        params = dict(legacy.get("params") or {}) if "params" in legacy else dict(legacy)
        params.pop("action", None)
        params.pop("command", None)
        return command, params
    raise ValueError("用法: 通过 stdin 传入 JSON 请求，或 python start_expand.py <command> [json_params]")


def main() -> None:
    try:
        command, params = _request()
        result = execute(command, params)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, default=str))
    if isinstance(result, dict) and result.get("ok") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
