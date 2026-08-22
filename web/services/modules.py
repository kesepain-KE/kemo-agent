"""外部消息与三层拓展领域服务。"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path, PurePosixPath
import shutil
import time
from typing import Any
import uuid

from message.identity import IdentityResolver
from message.plugin import FileMessageTransport, MessagePluginConfig, MessagePluginError
from run.config import load_config, read_json_object
from run.context import estimate_text_tokens
from run.extensions import read_expand_runtime, record_expand_runtime
from run.infra import LogStore
from run.extensions import (
    module_update_timeout,
    record_module_health,
    run_module_updater,
)
from run.config import INJECTION_MODE, parse_prompt_settings
from run.config import load_prompt_source_registry
from run.config import MainAgentSourcePolicy
from web.constants import (
    _BEIJING,
    _EXPAND_INJECTION_HEADING,
    _EXPAND_OPERATION_HEADING,
    _EXPAND_SCOPES,
    _MESSAGE_LOG_LIMIT,
)
from web.errors import InvalidRequestError, NotFoundError, WebServiceError
from web.services._paths import _flat_files, _reject_tree_links, _visible_children


class MessageExpandServiceMixin:
    def _message_module_directory(
        self, user: Any, module_name: Any
    ) -> tuple[str, str, Path, MessagePluginConfig]:
        name = self.require_user(user)
        if not isinstance(module_name, str) or not module_name.strip():
            raise InvalidRequestError("module_name 必须是非空字符串")
        logical_name = module_name.strip()
        pure = PurePosixPath(logical_name.replace("\\", "/"))
        if (
            len(pure.parts) != 1
            or pure.name in {".", "..", "__pycache__"}
            or pure.name.startswith(".")
            or "\x00" in logical_name
            or ":" in logical_name
        ):
            raise InvalidRequestError("消息模块名称必须是 message/out 下的直接目录名")
        base = (self.root / "message" / "out").resolve()
        target = base / logical_name
        if not target.is_dir():
            raise NotFoundError(f"消息模块不存在：{logical_name}")
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise InvalidRequestError("消息模块目录不能是符号链接或目录联接")
        try:
            target.resolve().relative_to(base)
        except ValueError:
            raise InvalidRequestError("消息模块路径越出 message/out") from None
        try:
            config = MessagePluginConfig.load(self.root, target)
        except MessagePluginError as exc:
            raise InvalidRequestError(str(exc)) from exc
        if config.bound_user != name:
            raise NotFoundError(f"当前用户未绑定消息模块：{logical_name}")
        return name, logical_name, target, config

    def _message_logs(
        self, config: MessagePluginConfig
    ) -> tuple[list[dict[str, Any]], bool, int]:
        """Read message-route logs from the authoritative SQLite store."""

        store = LogStore(self.root)
        entries = store.list_messages(
            config.machine_id,
            limit=_MESSAGE_LOG_LIMIT + 1,
        )
        today = datetime.now(_BEIJING).strftime("%Y-%m-%d")
        today_count = store.count_messages(
            config.machine_id,
            date_prefix=today,
        )
        return (
            entries[:_MESSAGE_LOG_LIMIT],
            len(entries) > _MESSAGE_LOG_LIMIT,
            today_count,
        )

    def _message_transport_item(
        self,
        config: MessagePluginConfig,
        directory: Path,
        components: dict[str, Any],
        issues: list[dict[str, str]],
    ) -> dict[str, Any]:
        try:
            state = LogStore(self.root).read_message_route_state(
                config.machine_id
            )
            if state is None:
                state = {}
        except Exception as exc:
            state = {}
            issues.append({"name": directory.name, "error": str(exc)})
        component = components.get(f"transport:{config.platform}")
        component = component if isinstance(component, dict) else {}
        runtime_state = str(component.get("state") or "")
        health = str(state.get("health") or "unknown")
        transport_state = (
            "running"
            if runtime_state == "running"
            else "error"
            if runtime_state == "failed" or health in {"dead", "degraded"}
            else "stopped"
        )
        connection_status = (
            "connected"
            if health == "healthy"
            else "error"
            if health in {"dead", "degraded"} or transport_state == "error"
            else "disconnected"
        )
        try:
            logs, logs_truncated, today_logs = self._message_logs(config)
        except Exception as exc:
            logs, logs_truncated, today_logs = [], False, 0
            issues.append({
                "name": directory.name,
                "error": f"消息日志数据库不可用：{exc}",
            })
        temporary_files = _flat_files(config.files_path, relative_to=self.root)
        return {
            "id": directory.name,
            "name": config.machine_id,
            "platform": config.platform,
            "display_name": config.display_name,
            "description": f"{config.display_name}，负责 {config.platform} 平台的文本与文件消息传输。",
            "capabilities": sorted(config.capabilities),
            "state": transport_state,
            "connection_status": connection_status,
            "bound_user": config.bound_user,
            "allowed_tools": (
                sorted(config.allowed_tools) if config.allowed_tools is not None else None
            ),
            "last_error": component.get("last_error") or state.get("error"),
            "health": health,
            "last_check": state.get("last_check"),
            "last_message_at": state.get("last_message_at"),
            "latency_ms": state.get("latency_ms"),
            "messages_received_today": int(state.get("messages_received_today") or 0),
            "messages_sent_today": int(state.get("messages_sent_today") or 0),
            "path": directory.relative_to(self.root).as_posix(),
            "files_path": config.files_path.relative_to(self.root).as_posix(),
            "log_path": "runtime/logs.sqlite3",
            "message_buffer": config.buffer_path.relative_to(self.root).as_posix(),
            "modules": dict(config.modules),
            "api_imported": True,
            "polling_interval": "1s",
            "health_interval": "30s",
            "file_relay_enabled": bool(
                {"receive_file", "send_file"}.intersection(config.capabilities)
            ),
            "log_rotation": "按保留期清理",
            "temporary_file_count": len(temporary_files),
            "temporary_file_bytes": sum(int(item["size"]) for item in temporary_files),
            "today_log_count": today_logs,
            "logs": logs,
            "logs_truncated": logs_truncated,
        }

    def message_status(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        message_config = read_json_object(
            self.root / "config" / "message_config.json",
            allow_empty=True,
        )
        resolver = IdentityResolver.from_config(self.root, message_config)
        bindings = [
            {
                "platform": binding.platform,
                "external_user_id": binding.external_user_id,
                "internal_user": binding.internal_user,
                "chat_type": binding.chat_type,
                "external_chat_id": binding.external_chat_id,
                "match_priority": (
                    2
                    + (1 if binding.chat_type is not None else 0)
                    + (2 if binding.external_chat_id is not None else 0)
                ),
            }
            for binding in resolver.bindings
            if binding.internal_user == name
        ]
        components = self._runtime_status().get("components") or {}
        components = components if isinstance(components, dict) else {}
        transports: list[dict[str, Any]] = []
        issues: list[dict[str, str]] = []
        base = self.root / "message" / "out"
        for directory in _visible_children(base):
            if not directory.is_dir():
                continue
            try:
                config = MessagePluginConfig.load(self.root, directory)
                if config.bound_user != name:
                    continue
                transports.append(
                    self._message_transport_item(config, directory, components, issues)
                )
            except MessagePluginError as exc:
                try:
                    raw = read_json_object(directory / "message.json", allow_empty=True)
                except Exception:
                    raw = {}
                if raw.get("bound_user") == name:
                    issues.append({"name": directory.name, "error": str(exc)})
        transports.sort(key=lambda item: (item["platform"], item["name"]))
        return {
            "user": name,
            "bindings": bindings,
            "transports": transports,
            "summary": {
                "total_bindings": len(bindings),
                "total_transports": len(transports),
                "running_transports": sum(item["state"] == "running" for item in transports),
                "stopped_transports": sum(item["state"] == "stopped" for item in transports),
                "error_transports": sum(item["state"] == "error" for item in transports),
                "connected_transports": sum(
                    item["connection_status"] == "connected" for item in transports
                ),
                "temporary_files": sum(item["temporary_file_count"] for item in transports),
                "today_logs": sum(item["today_log_count"] for item in transports),
            },
            "issues": issues,
        }

    def check_message_module(self, user: Any, module_name: Any) -> dict[str, Any]:
        name, logical_name, _, config = self._message_module_directory(user, module_name)
        try:
            if self.message_health_checker is not None:
                try:
                    state = self.message_health_checker(config.platform, name)
                except Exception:
                    state = FileMessageTransport(config).check_health()
            else:
                state = FileMessageTransport(config).check_health()
        except Exception as exc:
            raise WebServiceError(f"消息模块连接检测失败：{logical_name}（{exc}）") from exc
        refreshed = self.message_status(name)
        transport = next(
            (item for item in refreshed["transports"] if item["id"] == logical_name),
            None,
        )
        return {
            "user": name,
            "module": logical_name,
            "checked": True,
            "state": state,
            "transport": transport,
        }

    def delete_message_module(self, user: Any, module_name: Any) -> dict[str, Any]:
        name, logical_name, target, config = self._message_module_directory(user, module_name)
        _reject_tree_links(target)
        relative_path = target.relative_to(self.root).as_posix()
        tombstone = target.parent / f".{logical_name}.{uuid.uuid4().hex}.deleting"
        try:
            os.replace(target, tombstone)
            if self.message_transport_remover is not None:
                self.message_transport_remover(config.platform, name)
            shutil.rmtree(tombstone)
        except Exception as exc:
            if tombstone.exists() and not target.exists():
                try:
                    os.replace(tombstone, target)
                except OSError:
                    pass
            raise WebServiceError(f"消息模块删除失败：{logical_name}") from exc
        try:
            store = LogStore(self.root)
            store.delete_message_logs(
                config.machine_id,
                user=name,
            )
            store.delete_message_route_state(config.machine_id, user=name)
        except Exception:
            pass
        return {
            "user": name,
            "module": logical_name,
            "platform": config.platform,
            "path": relative_path,
            "deleted": True,
        }

    def expands(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        source_policy = MainAgentSourcePolicy.from_config(config)
        prompt_settings = parse_prompt_settings(config)
        registry = load_prompt_source_registry(self.root, name)
        selection = registry.select_expand(
            max_chars=prompt_settings.char_limits["expand_data"],
            mode=INJECTION_MODE,
            allow={
                "global": source_policy.global_expand.selector(),
                "shared": source_policy.shared_expand.selector(),
                "user": None,
            },
        )
        diagnostics = registry.selection_diagnostics().get("expand") or {}
        scope_roots = {
            "global": self.root / "global_expand",
            "shared": self.root / "shared_expand",
            "user": self.root / "users" / name / "expand",
        }
        expands: list[dict[str, Any]] = []
        scope_counts: dict[str, int] = {}
        injection_cursor = 0
        has_injection_piece = False
        for scope in ("global", "shared", "user"):
            directory = scope_roots[scope]
            scope_diagnostics = diagnostics.get(scope) or {}
            discovered = list(scope_diagnostics.get("discovered") or [])
            discovered_set = set(discovered)
            for module_dir in _visible_children(directory):
                if module_dir.is_dir() and module_dir.name not in discovered_set:
                    discovered.append(module_dir.name)
                    discovered_set.add(module_dir.name)
            selected = set(scope_diagnostics.get("selected") or [])
            health_status = scope_diagnostics.get("health_status") or {}
            items: list[dict[str, Any]] = []
            for module_name in discovered:
                module = directory / module_name
                health = health_status.get(module_name) or {
                    "name": module_name,
                    "valid": False,
                    "input_health": "异常",
                    "error": "模块未进入运行时注册表",
                }
                module_path_safe = (
                    module.is_dir()
                    and not module.is_symlink()
                    and not getattr(module, "is_junction", lambda: False)()
                )
                if module_path_safe:
                    try:
                        module.resolve().relative_to(directory.resolve())
                    except ValueError:
                        module_path_safe = False
                if not module_path_safe:
                    health = {
                        **health,
                        "valid": False,
                        "input_health": "异常",
                        "error": "拓展模块目录不能是符号链接、目录联接或越界路径",
                    }
                files = _flat_files(module, relative_to=directory) if module_path_safe else []
                valid = bool(health.get("valid"))
                whitelisted = scope == "user" or module_name in selected
                collected_markdown = self._read_expand_text(
                    module, health.get("input_data")
                )
                control_document = self._read_expand_text(
                    module, health.get("start_control")
                )
                control_injection, control_operation = self._expand_control_sections(
                    control_document
                )
                module_piece = self._expand_prompt_piece(
                    scope=scope,
                    module_name=module_name,
                    health=health,
                    collected_markdown=collected_markdown,
                    control_injection=control_injection,
                ) if valid and module_name in selected else ""
                injected_markdown = ""
                if module_piece:
                    piece_start = injection_cursor + (2 if has_injection_piece else 0)
                    piece_end = piece_start + len(module_piece)
                    if piece_start < len(selection.text):
                        injected_markdown = selection.text[
                            piece_start:min(piece_end, len(selection.text))
                        ]
                    injection_cursor = piece_end
                    has_injection_piece = True
                updated_at = max(
                    (float(item.get("updated_at") or 0) for item in files),
                    default=0.0,
                )
                runtime_state = (
                    read_expand_runtime(module)
                    if module_path_safe and module.is_dir()
                    else {"schema_version": 1}
                )
                items.append(
                    {
                        "id": f"{scope}:{module_name}",
                        "scope": scope,
                        "name": module_name,
                        "display_name": health.get("name") or module_name,
                        "description": health.get("explain") or "",
                        "type": "directory",
                        "root": self._project_path(directory),
                        "path": self._project_path(module),
                        "relative_path": module_name,
                        "has_register": (module / "expand.json").is_file(),
                        "valid": valid,
                        "error": health.get("error") or "",
                        "whitelisted": whitelisted,
                        "active_for_main_agent": valid and whitelisted,
                        "input_health": health.get("input_health") or "异常",
                        "open_input": bool(health.get("open_input")),
                        "open_control": bool(health.get("open_control")),
                        "input_data": health.get("input_data") or "",
                        "start_update": health.get("start_update") or "",
                        "start_expand": health.get("start_expand") or "",
                        "start_control": health.get("start_control") or "",
                        "control_document": control_document,
                        "control_injection_markdown": control_injection,
                        "control_operation_markdown": control_operation,
                        "collected_markdown": collected_markdown,
                        "injected_markdown": injected_markdown,
                        "injected_tokens": estimate_text_tokens(injected_markdown),
                        "runtime": runtime_state,
                        "files": files,
                        "updated_at": updated_at,
                    }
                )
            scope_counts[scope] = len(items)
            expands.append(
                {
                    "scope": scope,
                    "root": self._project_path(directory),
                    "items": items,
                }
            )
        return {
            "user": name,
            "summary": {"total": sum(scope_counts.values()), **scope_counts},
            "status_summary": {
                "enabled": sum(
                    item["active_for_main_agent"]
                    for scope in expands
                    for item in scope["items"]
                ),
                "healthy": sum(
                    item["valid"] and item["input_health"] == "正常"
                    for scope in expands
                    for item in scope["items"]
                ),
                "invalid": sum(
                    not item["valid"]
                    for scope in expands
                    for item in scope["items"]
                ),
            },
            "expands": expands,
            "injection": {
                "content": selection.text,
                "source_files": list(selection.source_files),
                "original_chars": selection.original_chars,
                "injected_chars": selection.injected_chars,
                "original_items": selection.original_items,
                "injected_items": selection.injected_items,
                "estimated_tokens": estimate_text_tokens(selection.text),
                "truncated": selection.truncated,
                "prompt_section": "expand_data",
                "prompt_position": "System Prompt / Expand Data",
            },
            "source_policy": source_policy.public_summary(),
        }

    @staticmethod
    def _read_expand_text(module_dir: Path, file_name: Any) -> str:
        if not isinstance(file_name, str) or not file_name.strip():
            return ""
        normalized = file_name.strip()
        if Path(normalized).name != normalized:
            return ""
        path = module_dir / normalized
        if not path.is_file() or path.is_symlink():
            return ""
        try:
            path.resolve().relative_to(module_dir.resolve())
            return path.read_text("utf-8-sig").strip()
        except (OSError, UnicodeError, ValueError):
            return ""

    @staticmethod
    def _expand_control_sections(content: str) -> tuple[str, str]:
        if not content:
            return "", ""
        injection_match = _EXPAND_INJECTION_HEADING.search(content)
        operation_match = _EXPAND_OPERATION_HEADING.search(
            content, injection_match.end() if injection_match else 0
        )
        injection = (
            content[
                injection_match.end():operation_match.start() if operation_match else len(content)
            ].strip()
            if injection_match
            else ""
        )
        operation = content[operation_match.end():].strip() if operation_match else ""
        return injection, operation

    @staticmethod
    def _expand_prompt_piece(
        *,
        scope: str,
        module_name: str,
        health: dict[str, Any],
        collected_markdown: str,
        control_injection: str,
    ) -> str:
        parts: list[str] = []
        if (
            health.get("open_input")
            and health.get("input_health") == "正常"
            and collected_markdown
        ):
            parts.append(f"## 数据采集\n{collected_markdown}")
        if health.get("open_control") and control_injection:
            parts.append(
                "## 操控能力\n"
                f"{control_injection}\n\n"
                f"调用入口：使用 `expand_call`，传入 `scope={scope}`、"
                f"`module={module_name}`，具体命令和参数按需读取操作层。"
            )
        return f"[{scope}:{module_name}]\n" + "\n\n".join(parts) if parts else ""

    def _expand_module_directory(
        self, user: Any, scope: Any, module_name: Any
    ) -> tuple[str, str, str, Path]:
        name = self.require_user(user)
        if not isinstance(scope, str) or scope not in _EXPAND_SCOPES:
            raise InvalidRequestError("scope 只允许 global、shared 或 user")
        if not isinstance(module_name, str) or not module_name.strip():
            raise InvalidRequestError("module_name 必须是非空字符串")
        logical_name = module_name.strip()
        pure = PurePosixPath(logical_name.replace("\\", "/"))
        if (
            len(pure.parts) != 1
            or pure.name in {".", "..", "__pycache__"}
            or pure.name.startswith(".")
            or "\x00" in logical_name
            or ":" in logical_name
        ):
            raise InvalidRequestError("拓展模块名称必须是对应拓展层的直接目录名")
        base = {
            "global": self.root / "global_expand",
            "shared": self.root / "shared_expand",
            "user": self.root / "users" / name / "expand",
        }[scope].resolve()
        target = base / logical_name
        if not target.is_dir():
            raise NotFoundError(f"拓展模块不存在：{scope}:{logical_name}")
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise InvalidRequestError("拓展模块目录不能是符号链接或目录联接")
        try:
            target.resolve().relative_to(base)
        except ValueError:
            raise InvalidRequestError("拓展模块路径越出对应拓展目录") from None
        return name, scope, logical_name, target

    def refresh_expand_module(
        self, user: Any, scope: Any, module_name: Any
    ) -> dict[str, Any]:
        name, normalized_scope, logical_name, target = self._expand_module_directory(
            user, scope, module_name
        )
        module = next(
            (
                item
                for group in self.expands(name)["expands"]
                if group["scope"] == normalized_scope
                for item in group["items"]
                if item["name"] == logical_name
            ),
            None,
        )
        if not module or not module["valid"]:
            raise InvalidRequestError(
                f"拓展模块配置无效，无法更新：{module['error'] if module else logical_name}"
            )
        updater = target / str(module["start_update"])
        if not updater.is_file():
            raise NotFoundError(f"拓展模块更新入口不存在：{module['start_update']}")
        if updater.is_symlink() or getattr(updater, "is_junction", lambda: False)():
            raise InvalidRequestError("拓展模块更新入口不能是符号链接或目录联接")
        try:
            updater.resolve().relative_to(target.resolve())
        except ValueError:
            raise InvalidRequestError("拓展模块更新入口越出模块目录") from None
        config = load_config(name, self.root)
        started = time.monotonic()
        result = run_module_updater(
            updater,
            target,
            timeout=module_update_timeout(config),
        )
        duration_ms = round((time.monotonic() - started) * 1000)
        if result.get("ok") is not True:
            reason = str(result.get("reason") or "未知错误")
            try:
                record_module_health(target / "expand.json", "expand", healthy=False)
                record_expand_runtime(
                    target,
                    "update",
                    ok=False,
                    duration_ms=duration_ms,
                    error=reason,
                )
            except Exception as state_exc:
                reason = f"{reason}；写回运行状态失败：{state_exc}"
            raise WebServiceError(
                f"拓展模块更新失败：{normalized_scope}:{logical_name}（{reason[-1000:]}）"
            )
        try:
            record_module_health(target / "expand.json", "expand", healthy=True)
            record_expand_runtime(
                target,
                "update",
                ok=True,
                duration_ms=duration_ms,
                result=result.get("result"),
            )
        except Exception as exc:
            raise WebServiceError(
                f"拓展模块更新成功，但运行状态写回失败：{normalized_scope}:{logical_name}"
            ) from exc
        refreshed = self.expands(name)
        refreshed_module = next(
            (
                item
                for group in refreshed["expands"]
                if group["scope"] == normalized_scope
                for item in group["items"]
                if item["name"] == logical_name
            ),
            None,
        )
        return {
            "user": name,
            "scope": normalized_scope,
            "module": logical_name,
            "updated": True,
            "item": refreshed_module,
            "injection": refreshed["injection"],
        }

    def set_expand_module_enabled(
        self, user: Any, scope: Any, module_name: Any, enabled: Any
    ) -> dict[str, Any]:
        name, normalized_scope, logical_name, _ = self._expand_module_directory(
            user, scope, module_name
        )
        if normalized_scope == "user":
            raise InvalidRequestError("用户拓展始终可用，不支持白名单开关")
        if not isinstance(enabled, bool):
            raise InvalidRequestError("enabled 必须是布尔值")
        inventory = self.expands(name)
        group = next(
            item for item in inventory["expands"] if item["scope"] == normalized_scope
        )
        current = next(
            item for item in group["items"] if item["name"] == logical_name
        )
        if not current["valid"]:
            raise InvalidRequestError("拓展模块配置无效，不能修改白名单")
        candidates = {item["name"] for item in group["items"] if item["valid"]}
        selected = {
            item["name"]
            for item in group["items"]
            if item["valid"] and item["whitelisted"]
        }
        if enabled:
            selected.add(logical_name)
        else:
            selected.discard(logical_name)
        whitelist = [] if selected == candidates else sorted(selected) or ["__kemo_none__"]
        self.patch_user_config(
            name, {"expand": {f"{normalized_scope}_whitelist": whitelist}}
        )
        return {
            "user": name,
            "scope": normalized_scope,
            "module": logical_name,
            "enabled": enabled,
            "whitelist": whitelist,
        }

    def delete_expand_module(
        self, user: Any, scope: Any, module_name: Any
    ) -> dict[str, Any]:
        name, normalized_scope, logical_name, target = self._expand_module_directory(
            user, scope, module_name
        )
        if normalized_scope != "user":
            raise InvalidRequestError("只有用户拓展允许从当前页面删除")
        _reject_tree_links(target)
        relative_path = target.relative_to(self.root).as_posix()
        tombstone = target.parent / f".{logical_name}.{uuid.uuid4().hex}.deleting"
        try:
            os.replace(target, tombstone)
            shutil.rmtree(tombstone)
        except OSError as exc:
            if tombstone.exists() and not target.exists():
                try:
                    os.replace(tombstone, target)
                except OSError:
                    pass
            raise WebServiceError(f"用户拓展删除失败：{logical_name}") from exc
        return {
            "user": name,
            "scope": normalized_scope,
            "module": logical_name,
            "path": relative_path,
            "deleted": True,
        }
