"""Run Web workspace action entry points without going through the model tool."""

import json
import sys
from pathlib import Path
from typing import Any

from run.config import load_config
from run.extensions import (
    EXPAND_CALL_RESULT_PREFIX, EXPAND_CALL_RUNNER, invoke_expand, module_execution_lock,
    module_update_timeout, resolve_expand, run_protocol_process,
)
from run.modules.spec import ModuleSpec
from web.errors import InvalidRequestError, WebServiceError
from web.services.message_workspace import MessageWorkspaceSpec


def invoke_expand_action(root: Path, user: str, scope: str, module: str, command: str, params: dict[str, Any],
                         *, source: str, spec: ModuleSpec | None = None, workspace_instance: bool = False) -> Any:
    if spec is None:
        _, spec = resolve_expand(root, user, scope, module)
    if spec.ui_action_entry is None:
        return invoke_expand(root=root, user=user, scope=scope, module=module, command=command, params=params,
                             timeout=module_update_timeout(load_config(user, root)))
    return invoke_ui_action(root, user, scope, module, spec, command, params,
                            source=source, workspace_instance=workspace_instance)


def invoke_ui_action(root: Path, user: str, scope: str, module: str, spec: ModuleSpec | MessageWorkspaceSpec, command: str,
                     params: dict[str, Any], *, source: str, workspace_instance: bool = False) -> Any:
    if spec.ui_action_entry is None:
        raise InvalidRequestError("模块未声明 UI 动作入口")
    request = json.dumps({"command": command, "params": params, "context": {
        "user": user, "scope": scope, "module": module, "source": source,
        **({"workspace_instance": True} if workspace_instance else {}),
    }}, ensure_ascii=False)
    timeout = module_update_timeout(load_config(user, root))
    with module_execution_lock(spec.module_dir, timeout=timeout):
        code, payload, _, _ = run_protocol_process(
            [sys.executable, "-I", "-c", EXPAND_CALL_RUNNER, str(spec.ui_action_entry), str(spec.module_dir)],
            cwd=spec.module_dir, timeout=timeout, result_prefix=EXPAND_CALL_RESULT_PREFIX,
            stdin_payload=request,
        )
    if code or not payload or payload.get("ok") is not True:
        raise WebServiceError("模块 UI 动作执行失败：" + str((payload or {}).get("reason", "无有效回执")))
    result = payload.get("result")
    if isinstance(result, dict) and (result.get("ok") is False or result.get("status") in {"error", "failed", "failure"}):
        error = WebServiceError("模块 UI 动作返回失败：" + str(result.get("error") or result.get("message") or "无错误详情"))
        status = result.get("status_code")
        if type(status) is int and 400 <= status < 500:
            error.status = status
        raise error
    return result
