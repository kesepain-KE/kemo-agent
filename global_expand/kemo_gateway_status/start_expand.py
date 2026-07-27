"""Control entry for the Kemo gateway status extension."""

from __future__ import annotations

import json
import sys
from typing import Any

from gateway_status import activate, configuration_status, deactivate, update_snapshot


def execute(command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    arguments = dict(params or {})
    normalized = str(command or "").strip().casefold()
    if normalized == "activate":
        return activate(arguments)
    if normalized in {"refresh", "status"}:
        result = update_snapshot(target_date=str(arguments.get("date") or "").strip() or None)
        if result.get("ok") and result.get("status") == "active":
            result = {
                **result,
                "artifacts": [{
                    "path": "artifacts/gateway_status.png",
                    "kind": "image",
                    "name": "kemo-gateway-status.png",
                }],
            }
        return result
    if normalized == "configuration_status":
        return configuration_status()
    if normalized == "deactivate":
        return deactivate()
    return {"ok": False, "error": f"未知命令: {command}"}


def _request() -> tuple[str, dict[str, Any]]:
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError("请求必须是 JSON 对象")
            command = payload.get("command") or payload.get("action")
            params = payload.get("params", {})
            if not isinstance(command, str) or not command:
                raise ValueError("请求缺少 command")
            if not isinstance(params, dict):
                raise TypeError("params 必须是 JSON 对象")
            return command, params
    if len(sys.argv) < 2:
        raise ValueError("用法: python start_expand.py <command> [json_params]")
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    if not isinstance(params, dict):
        raise TypeError("params 必须是 JSON 对象")
    return sys.argv[1], params


def main() -> None:
    try:
        command, params = _request()
        result = execute(command, params)
    except Exception as exc:
        result = {"ok": False, "error": str(exc) or type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, default=str))
    if result.get("ok") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

