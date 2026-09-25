"""Control entry for the Kemo Graph sidecar document station."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any
import urllib.parse

from client import verify_service
from errors import GraphExpandError
from library_sync import scan_libraries, sync_libraries
from operations import (
    cache_operation,
    config_read,
    document_operation,
    entity_operation,
    graph_operation,
    import_file,
    ingest_library,
    initialize_libraries,
    jobs_operation,
    logs_operation,
    maintenance_operation,
    project_operation,
    query_libraries,
    status_libraries,
    update_status,
    upload_markdown,
)
from registry import (
    CONFIG_PATH,
    LAST_RUN_PATH,
    atomic_json,
    config_payload,
    config_from_mapping,
    configured_admin_users,
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


def _panel_endpoint(base_url: str) -> tuple[str, str, int | str]:
    parsed = urllib.parse.urlsplit(base_url)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        port = ""
    return parsed.scheme or "http", parsed.hostname or "", port


def write_panel_status(
    *,
    online: bool | None = None,
    error: str = "",
    checked_at: str = "",
) -> dict[str, Any]:
    config = load_config()
    if config is None:
        payload = {
            "configuration": "未配置",
            "graph_ip": "—",
            "graph_port": "—",
            "scheme": "http",
            "base_url": "—",
            "allow_remote": False,
            "backend_status": "未检测",
            "last_checked": checked_at,
            "last_error": error,
        }
    else:
        scheme, host, port = _panel_endpoint(config.base_url)
        payload = {
            "configuration": "已配置",
            "graph_ip": host or "—",
            "graph_port": port or "—",
            "scheme": scheme,
            "base_url": config.base_url,
            "allow_remote": config.allow_remote,
            "backend_status": "在线" if online is True else "离线" if online is False else "未检测",
            "last_checked": checked_at,
            "last_error": error,
        }
    atomic_json(CONFIG_PATH.parent / "module" / "status.json", payload)
    return payload


def _check_panel_backend(config) -> dict[str, Any]:
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        response = verify_service(config)
        write_panel_status(online=True, checked_at=checked_at)
        return {
            "ok": True,
            "online": True,
            "checked_at": checked_at,
            "service_status": str(response.get("status") or response.get("object") or "ok") if isinstance(response, dict) else "ok",
        }
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        write_panel_status(online=False, error=error, checked_at=checked_at)
        return {"ok": False, "online": False, "checked_at": checked_at, "error": error}


def _configure_panel_endpoint(arguments: dict[str, Any], caller_user: str | None) -> dict[str, Any]:
    current = _active_config()
    _require_admin(current, caller_user, "panel_configure_endpoint")
    scheme = str(arguments.get("scheme") or "http").strip().casefold()
    host = str(arguments.get("graph_ip") or "").strip()
    raw_port = arguments.get("graph_port")
    if isinstance(raw_port, bool):
        raise GraphExpandError("graph_port 必须是整数")
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise GraphExpandError("graph_port 必须是整数") from exc
    if not 1 <= port <= 65535:
        raise GraphExpandError("graph_port 必须在 1～65535 之间")
    payload = config_payload(current)
    payload["allow_remote"] = bool(arguments.get("allow_remote", current.allow_remote))
    payload["base_url"] = f"{scheme}://{host}:{port}/api/v1"
    candidate = config_from_mapping(payload, require_source_roots_exist=False)
    save_config(candidate)
    catalog = refresh_catalog()
    check = _check_panel_backend(candidate)
    return {
        "ok": True,
        "configured": True,
        "base_url": candidate.base_url,
        "allow_remote": candidate.allow_remote,
        "catalog_updated": bool(catalog.get("ok")),
        "backend_online": bool(check.get("online")),
        "backend_error": str(check.get("error") or ""),
    }


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
            if (
                caller_user is not None
                and caller_user not in configured_admin_users()
            ):
                raise PermissionError(
                    "当前注册表无效，只有原 admin_users 可以提交完整配置修复"
                ) from None
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
    elif normalized == "panel_configure_endpoint":
        result = _configure_panel_endpoint(arguments, caller_user)
    elif normalized == "panel_check_backend":
        config = _active_config()
        _require_admin(config, caller_user, normalized)
        result = _check_panel_backend(config)
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
        if document_action in {"update", "delete", "move"}:
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
    elif normalized == "projects":
        result = project_operation(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "graph":
        result = graph_operation(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "entities":
        result = entity_operation(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "cache":
        result = cache_operation(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "maintenance":
        result = maintenance_operation(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "logs":
        result = logs_operation(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "config":
        result = config_read(
            _active_config(),
            arguments,
            caller_user=caller_user,
        )
    elif normalized == "update_status":
        result = update_status(
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
