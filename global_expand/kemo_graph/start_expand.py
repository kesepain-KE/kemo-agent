"""Control entry for the Kemo Graph sidecar document station."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

from errors import GraphExpandError
from library_sync import scan_libraries, sync_libraries
from operations import (
    document_operation,
    import_file,
    ingest_library,
    initialize_libraries,
    jobs_operation,
    query_libraries,
    status_libraries,
    upload_markdown,
)
from registry import (
    CONFIG_PATH,
    LAST_RUN_PATH,
    atomic_json,
    config_from_mapping,
    configuration_status,
    load_config,
    save_config,
)
from render import refresh_catalog


def _active_config():
    config = load_config()
    if config is None:
        raise RuntimeError("Kemo Graph 外挂文档站尚未激活")
    return config


def _require_admin(config, caller_user: str | None, operation: str) -> None:
    if not config.is_admin(caller_user):
        raise PermissionError(f"只有 admin_users 可以执行 {operation}")


def _caller_user(context: dict[str, Any] | None) -> str | None:
    user = str((context or {}).get("user") or "").strip()
    # Direct local CLI and the module contract validator have no framework
    # caller envelope; they are trusted local-administrator entry points.
    return user or None


def execute(
    command: str,
    params: dict[str, Any] | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    arguments = dict(params or {})
    normalized = str(command or "").strip().casefold()
    caller_user = _caller_user(context)
    if normalized == "activate":
        try:
            current = load_config()
        except GraphExpandError:
            if caller_user is not None:
                raise
            current = None
        if current is not None:
            _require_admin(current, caller_user, "activate")
        config = config_from_mapping(arguments)
        if not config.is_admin(caller_user):
            raise PermissionError("activate 的调用用户必须列入 admin_users")
        save_config(config)
        result = {
            **configuration_status(caller_user),
            "catalog": refresh_catalog(),
        }
    elif normalized in {"configuration_status", "libraries"}:
        result = configuration_status(caller_user)
    elif normalized == "refresh":
        result = refresh_catalog()
    elif normalized == "status":
        result = status_libraries(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
        refresh_catalog()
    elif normalized == "initialize":
        config = _active_config()
        _require_admin(config, caller_user, normalized)
        result = initialize_libraries(
            config,
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "scan":
        result = scan_libraries(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "sync":
        config = _active_config()
        _require_admin(config, caller_user, normalized)
        result = sync_libraries(
            config,
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "ingest":
        config = _active_config()
        _require_admin(config, caller_user, normalized)
        result = ingest_library(
            config,
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "query":
        result = query_libraries(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "upload":
        config = _active_config()
        _require_admin(config, caller_user, normalized)
        result = upload_markdown(
            config,
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "import_file":
        config = _active_config()
        _require_admin(config, caller_user, normalized)
        result = import_file(
            config,
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "documents":
        config = _active_config()
        document_action = str(arguments.get("action") or "list").strip().casefold()
        if document_action in {"update", "delete"}:
            _require_admin(config, caller_user, f"documents.{document_action}")
        result = document_operation(
            config,
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "jobs":
        result = jobs_operation(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "deactivate":
        config = _active_config()
        _require_admin(config, caller_user, normalized)
        CONFIG_PATH.unlink(missing_ok=True)
        result = {
            **refresh_catalog(),
            "deactivated": True,
            "external_stores_deleted": False,
            "local_sync_state_preserved": True,
        }
    else:
        result = {"ok": False, "error": f"未知命令: {command}"}
    atomic_json(
        LAST_RUN_PATH,
        {
            **result,
            "command": normalized,
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    return result


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
