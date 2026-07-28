"""全局感知库存、预览与维护领域服务。"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from typing import Any
import uuid

from run.config import load_config
from run.context import estimate_text_tokens
from run.process_utils import hidden_subprocess_kwargs
from run.prompt import parse_prompt_settings
from run.prompt_sources import load_prompt_source_registry
from run.source_policy import MainAgentSourcePolicy
from web.errors import InvalidRequestError, NotFoundError, WebServiceError
from web.services._paths import _reject_tree_links


class SenseServiceMixin:
    def sense(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        source_policy = MainAgentSourcePolicy.from_config(config)
        core_dir = self.root / "global_sense"
        registry_available = (core_dir / "register.py").is_file()
        registry = load_prompt_source_registry(self.root, name)
        inventory = registry.perception_inventory(
            allow_modules=source_policy.global_perception.selector()
        )
        prompt_settings = parse_prompt_settings(config)
        selection = registry.select_perception(
            max_chars=prompt_settings.char_limits["perception"],
            mode=prompt_settings.injection_mode["perception"],
            allow_modules=source_policy.global_perception.selector(),
        )
        injected_files = set(selection.source_files)
        sources: list[dict[str, Any]] = []
        injection_cursor = 0
        has_injection_piece = False
        for item in inventory:
            collected_markdown = self._sense_markdown(item)
            injected_markdown = ""
            if item["active"] and collected_markdown:
                piece = f"[{item['name']}]\n{collected_markdown}"
                piece_start = injection_cursor + (2 if has_injection_piece else 0)
                piece_end = piece_start + len(piece)
                if piece_start < len(selection.text):
                    injected_markdown = selection.text[piece_start:min(piece_end, len(selection.text))]
                injection_cursor = piece_end
                has_injection_piece = True
            sources.append({
                "id": item["name"],
                "name": item["name"],
                "display_name": item["display_name"],
                "description": (
                    f"标准数据文件：{item['data_md']}"
                    if item["valid"]
                    else f"模块配置无效：{item['error']}"
                ),
                "layer": "global",
                "enabled": item["active"],
                "whitelisted": item["selected"],
                "active_for_main_agent": item["active"],
                "status": item["status"],
                "data_md": item["data_md"],
                "recent_update": item["recent_update"],
                "health": item["health"],
                "valid": item["valid"],
                "error": item["error"],
                "start_update": item["start_update"],
                "files": item["files"],
                "registered_items": item["files"],
                "injected_items": sum(
                    path in injected_files
                    for path in (
                        f"{item['root']}/{item['name']}/{relative_path}"
                        for relative_path in item["data_items"]
                    )
                ),
                "data_items": item["data_items"],
                "value_preview": self._sense_value_preview(item),
                "collected_markdown": collected_markdown,
                "injected_markdown": injected_markdown,
                "injected_tokens": estimate_text_tokens(injected_markdown),
                # Sense modules currently have no required refresh interval
                # in sense.json. Keep this explicit so clients can render a
                # truthful fallback instead of inventing one.
                "update_interval": "",
                "updated_at": item["updated_at"],
            })
        core_files = sum(item["files"] for item in inventory)
        preview_limit = 4000
        preview = selection.text[:preview_limit]
        return {
            "user": name,
            "registry_available": registry_available,
            "injection_enabled": any(item["active_for_main_agent"] for item in sources),
            "core_available": bool(sources),
            "core_files": core_files,
            "summary": {
                "registered": len(sources),
                "enabled": sum(item["enabled"] for item in sources),
                "user": sum(item["layer"] == "user" for item in sources),
                "shared": sum(item["layer"] == "shared" for item in sources),
                "global": sum(item["layer"] == "global" for item in sources),
                "healthy": sum(item["valid"] and item["health"] == "正常" for item in sources),
                "unhealthy": sum(item["health"] == "异常" for item in sources),
                "invalid": sum(not item["valid"] for item in sources),
                "registered_data": core_files,
                "injected_data": selection.injected_items,
            },
            "sources": sources,
            "injection": {
                "enabled": bool(selection.text),
                "registered_items": core_files,
                "injected_items": selection.injected_items,
                "original_chars": selection.original_chars,
                "injected_chars": selection.injected_chars,
                "estimated_tokens": estimate_text_tokens(selection.text),
                "truncated": selection.truncated,
                "preview": preview,
                "preview_truncated": len(selection.text) > preview_limit,
                "content": selection.text,
                "source_files": list(selection.source_files),
                "prompt_section": "perception",
                "prompt_position": "System Prompt / Global Sense",
            },
            "decisions": [],
            "source_policy": source_policy.public_summary(),
        }

    def _sense_module_directory(self, user: Any, module_name: Any) -> tuple[str, str, Path]:
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
            raise InvalidRequestError("感知模块名称必须是 global_sense 下的直接目录名")
        base = (self.root / "global_sense").resolve()
        target = base / logical_name
        if not target.is_dir():
            raise NotFoundError(f"感知模块不存在：{logical_name}")
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise InvalidRequestError("感知模块目录不能是符号链接或目录联接")
        try:
            target.resolve().relative_to(base)
        except ValueError:
            raise InvalidRequestError("感知模块路径越出 global_sense") from None
        return name, logical_name, target

    def refresh_sense_module(self, user: Any, module_name: Any) -> dict[str, Any]:
        name, logical_name, target = self._sense_module_directory(user, module_name)
        source = next(
            (item for item in self.sense(name)["sources"] if item["id"] == logical_name),
            None,
        )
        if not source or not source["valid"]:
            raise InvalidRequestError(
                f"感知模块配置无效，无法更新：{source['error'] if source else logical_name}"
            )
        updater = target / str(source["start_update"])
        if not updater.is_file():
            raise NotFoundError(f"感知模块更新入口不存在：{source['start_update']}")
        if updater.is_symlink() or getattr(updater, "is_junction", lambda: False)():
            raise InvalidRequestError("感知模块更新入口不能是符号链接或目录联接")
        try:
            completed = subprocess.run(
                [sys.executable, updater.name],
                cwd=str(target),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise WebServiceError(f"感知模块更新超时：{logical_name}") from exc
        except OSError as exc:
            raise WebServiceError(f"感知模块更新入口执行失败：{logical_name}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "未知错误").strip()[:1000]
            raise WebServiceError(f"感知模块更新失败：{logical_name}（{detail}）")
        refreshed = self.sense(name)
        refreshed_source = next(
            (item for item in refreshed["sources"] if item["id"] == logical_name),
            None,
        )
        return {
            "user": name,
            "module": logical_name,
            "updated": True,
            "source": refreshed_source,
            "injection": refreshed["injection"],
        }

    def set_sense_module_enabled(self, user: Any, module_name: Any, enabled: Any) -> dict[str, Any]:
        name, logical_name, _ = self._sense_module_directory(user, module_name)
        if not isinstance(enabled, bool):
            raise InvalidRequestError("enabled 必须是布尔值")
        sense = self.sense(name)
        candidates = {item["id"] for item in sense["sources"]}
        selected = {item["id"] for item in sense["sources"] if item["whitelisted"]}
        if enabled:
            selected.add(logical_name)
        else:
            selected.discard(logical_name)
        whitelist = [] if selected == candidates else sorted(selected) or ["__kemo_none__"]
        self.patch_user_config(name, {"perception": {"global_whitelist": whitelist}})
        return {
            "user": name,
            "module": logical_name,
            "enabled": enabled,
            "whitelist": whitelist,
        }

    def delete_sense_module(self, user: Any, module_name: Any) -> dict[str, Any]:
        name, logical_name, target = self._sense_module_directory(user, module_name)
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
            raise WebServiceError(f"感知模块删除失败：{logical_name}") from exc
        return {
            "user": name,
            "module": logical_name,
            "path": relative_path,
            "deleted": True,
        }

    def _sense_markdown(self, item: dict[str, Any]) -> str:
        if not item.get("valid") or not item.get("data_md"):
            return ""
        path = self.root / str(item.get("root") or "") / str(item.get("name") or "") / str(item["data_md"])
        try:
            return path.read_text("utf-8-sig").strip()
        except (OSError, UnicodeError):
            return ""

    def _sense_value_preview(self, item: dict[str, Any]) -> str:
        """Return a bounded, presentation-safe preview of a sense data file."""

        if not item.get("valid") or not item.get("data_md"):
            return ""
        path = self.root / str(item.get("root") or "") / str(item.get("name") or "") / str(item["data_md"])
        try:
            content = path.read_text("utf-8-sig")
        except (OSError, UnicodeError):
            return ""
        lines: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(">"):
                continue
            line = re.sub(r"^[-*]\s*", "", line)
            lines.append(line)
            if len(" · ".join(lines)) >= 160:
                break
        return " · ".join(lines)[:160]


