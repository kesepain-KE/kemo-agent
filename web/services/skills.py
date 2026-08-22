"""工具与 Prompt 技能目录领域服务。"""

from __future__ import annotations

import io
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
from typing import Any
import uuid
import zipfile

from run.config import load_config
from run.config import load_prompt_source_registry, parse_skill_descriptor
from run.config import MainAgentSourcePolicy
from run.tools import discover_tools
from web.constants import (
    SKILL_ARCHIVE_MAX_BYTES,
    SKILL_ARCHIVE_MAX_EXPANDED_BYTES,
    SKILL_ARCHIVE_MAX_FILES,
    SKILL_ARCHIVE_MAX_RATIO,
    SKILL_ARCHIVE_MAX_SKILLS,
    _EDITABLE_SKILL_CATEGORIES,
    _SKILL_CATEGORIES,
)
from web.errors import ConflictError, InvalidRequestError, NotFoundError, WebServiceError
from web.services._io import (
    atomic_write as _atomic_write,
    validated_text as _validated_text,
)
from web.services._paths import (
    _reject_link_path,
    _reject_tree_links,
    _safe_relative_target,
    _skill_package_name,
    _validated_skill_archive_path,
    _zip_member_kind,
)


class SkillServiceMixin:
    def skills(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        source_policy = MainAgentSourcePolicy.from_config(config)
        registry = discover_tools(self.root, name)
        tools = []
        for tool in sorted(registry.tools.values(), key=lambda item: item.name.casefold()):
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "version": tool.version,
                    "enabled": bool(tool.enabled and source_policy.plugins.allows(tool.name)),
                    "source": tool.source,
                    "layer": "core",
                    "overrides": len(tool.overrides),
                }
            )
        prompt_skills = []
        prompt_sources = load_prompt_source_registry(self.root, name)
        for descriptor in prompt_sources.select_skills():
            base = (
                self.root / "shared_skills"
                if descriptor.scope == "shared"
                else self.root / "users" / name / "user_skills"
            )
            logical_name = descriptor.path.parent.relative_to(base).as_posix()
            allowed = (
                source_policy.shared_skills
                if descriptor.scope == "shared"
                else source_policy.user_skills
            )
            prompt_skills.append(
                {
                    "name": logical_name,
                    "title": descriptor.title,
                    "description": descriptor.description,
                    "scope": descriptor.scope,
                    "category": (
                        "shared"
                        if descriptor.scope == "shared"
                        else "agent_generated"
                        if logical_name == "agent_create" or logical_name.startswith("agent_create/")
                        else "user_created"
                    ),
                    "path": descriptor.path.parent.relative_to(self.root).as_posix(),
                    "active_for_main_agent": allowed.allows(logical_name),
                }
            )
        items = [
            {
                "id": f"builtin:{tool['name']}",
                "name": tool["name"],
                "title": tool["name"],
                "description": tool["description"],
                "category": "builtin",
                "version": tool["version"],
                "enabled": tool["enabled"],
                "editable": False,
                "toggleable": True,
                "downloadable": True,
                "path": registry.tools[tool["name"]].directory.relative_to(self.root).as_posix(),
            }
            for tool in tools
        ]
        items.extend(
            {
                "id": f"{skill['category']}:{skill['name']}",
                "name": skill["name"],
                "title": skill["title"],
                "description": skill["description"],
                "category": skill["category"],
                "version": "",
                "enabled": skill["active_for_main_agent"],
                "editable": skill["category"] in _EDITABLE_SKILL_CATEGORIES,
                "toggleable": skill["category"] == "shared",
                "downloadable": skill["category"] == "shared",
                "path": skill["path"],
            }
            for skill in prompt_skills
        )
        category_counts = {
            category: sum(item["category"] == category for item in items)
            for category in _SKILL_CATEGORIES
        }
        return {
            "user": name,
            "summary": {
                "registered": len(tools),
                "enabled": sum(item["enabled"] for item in tools),
                "user": sum(item["layer"] == "user" for item in tools),
                "shared": sum(item["layer"] == "shared" for item in tools),
                "core": sum(item["layer"] == "core" for item in tools),
            },
            "tools": tools,
            "catalog_summary": {
                "total": len(items),
                "enabled": sum(item["enabled"] for item in items),
                **category_counts,
            },
            "items": items,
            "prompt_summary": {
                "registered": len(prompt_skills),
                "active": sum(item["active_for_main_agent"] for item in prompt_skills),
                "user": sum(item["scope"] == "user" for item in prompt_skills),
                "shared": sum(item["scope"] == "shared" for item in prompt_skills),
            },
            "prompt_skills": prompt_skills,
            "source_policy": source_policy.public_summary(),
        }

    def _skill_directory(self, user: Any, category: Any, skill_name: Any) -> tuple[str, str, str, Path]:
        name = self.require_user(user)
        normalized_category = str(category or "").strip()
        if normalized_category not in _SKILL_CATEGORIES:
            raise InvalidRequestError(f"技能分类无效：{category}")
        logical_name = str(skill_name or "").strip().replace("\\", "/")
        if normalized_category == "builtin":
            registry = discover_tools(self.root, name)
            tool = registry.tools.get(logical_name)
            if tool is None:
                raise NotFoundError(f"基础插件不存在：{logical_name}")
            target = tool.directory
            root = self.root / "plugins"
        else:
            root = (
                self.root / "shared_skills"
                if normalized_category == "shared"
                else self.root / "users" / name / "user_skills"
            )
            relative, target = _safe_relative_target(root, logical_name)
            logical_name = relative
            if normalized_category == "agent_generated" and not (
                logical_name == "agent_create" or logical_name.startswith("agent_create/")
            ):
                raise InvalidRequestError("智能体生成技能必须位于 agent_create 目录")
            if normalized_category == "user_created" and (
                logical_name == "agent_create" or logical_name.startswith("agent_create/")
            ):
                raise InvalidRequestError("智能体生成技能不能按用户自建技能管理")
        _reject_link_path(root.resolve(), target)
        if not target.is_dir() or not (target / "SKILL.md").is_file():
            raise NotFoundError(f"技能不存在：{logical_name}")
        return name, normalized_category, logical_name, target

    def skill_document(self, user: Any, category: Any, skill_name: Any) -> dict[str, Any]:
        name, normalized_category, logical_name, target = self._skill_directory(user, category, skill_name)
        skill_file = target / "SKILL.md"
        content = skill_file.read_text("utf-8")
        return {
            "user": name,
            "category": normalized_category,
            "name": logical_name,
            "path": skill_file.relative_to(self.root).as_posix(),
            "content": content,
            "size": len(content.encode("utf-8")),
            "updated_at": skill_file.stat().st_mtime,
            "editable": normalized_category in _EDITABLE_SKILL_CATEGORIES,
        }

    def put_skill_document(self, user: Any, category: Any, skill_name: Any, content: Any) -> dict[str, Any]:
        name, normalized_category, logical_name, target = self._skill_directory(user, category, skill_name)
        if normalized_category not in _EDITABLE_SKILL_CATEGORIES:
            raise InvalidRequestError("基础插件与共享技能只允许预览和下载")
        text = _validated_text(content, field="content")
        skill_file = target / "SKILL.md"
        previous = skill_file.read_bytes()
        _atomic_write(skill_file, text.encode("utf-8"))
        try:
            load_prompt_source_registry(self.root, name).select_skills()
        except Exception as exc:
            _atomic_write(skill_file, previous)
            raise InvalidRequestError(f"技能文件校验失败：{exc}") from None
        return self.skill_document(name, normalized_category, logical_name)

    def upload_user_skills(self, user: Any, filename: Any, data: Any) -> dict[str, Any]:
        name = self.require_user(user)
        archive_name = PurePosixPath(str(filename or "").strip().replace("\\", "/")).name
        if not archive_name or Path(archive_name).suffix.casefold() != ".zip":
            raise InvalidRequestError("用户技能只支持 ZIP 压缩包")
        if not isinstance(data, bytes) or not data:
            raise InvalidRequestError("技能压缩包不能为空")
        if len(data) > SKILL_ARCHIVE_MAX_BYTES:
            raise InvalidRequestError(
                f"技能压缩包不能超过 {SKILL_ARCHIVE_MAX_BYTES // (1024 * 1024)} MB"
            )
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise InvalidRequestError("上传内容不是有效的 ZIP 压缩包")

        user_skills_root = self.root / "users" / name / "user_skills"
        destination_root = user_skills_root / "user_create"
        with self._skill_upload_lock:
            user_skills_root.mkdir(parents=True, exist_ok=True)
            _reject_link_path((self.root / "users" / name).resolve(), user_skills_root)
            destination_root.mkdir(parents=True, exist_ok=True)
            _reject_link_path(user_skills_root.resolve(), destination_root)
            staging_root = destination_root / f".upload-{uuid.uuid4().hex}"
            archive_root = staging_root / "archive"
            packages_root = staging_root / "packages"
            moved_targets: list[Path] = []
            try:
                with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
                    infos = archive.infolist()
                    if not infos:
                        raise InvalidRequestError("技能压缩包不能为空")

                    members: list[tuple[zipfile.ZipInfo, PurePosixPath, str]] = []
                    seen_paths: dict[str, tuple[PurePosixPath, str]] = {}
                    expanded_bytes = 0
                    file_count = 0
                    for info in infos:
                        if info.flag_bits & 0x1:
                            raise InvalidRequestError("技能压缩包不能加密")
                        pure = _validated_skill_archive_path(info.filename)
                        kind = _zip_member_kind(info)
                        key = pure.as_posix().casefold()
                        if key in seen_paths:
                            raise InvalidRequestError(
                                f"技能压缩包存在重复或大小写冲突路径：{pure.as_posix()}"
                            )
                        seen_paths[key] = (pure, kind)
                        if kind == "file":
                            file_count += 1
                            expanded_bytes += info.file_size
                            if file_count > SKILL_ARCHIVE_MAX_FILES:
                                raise InvalidRequestError(
                                    f"技能压缩包文件数不能超过 {SKILL_ARCHIVE_MAX_FILES}"
                                )
                            if expanded_bytes > SKILL_ARCHIVE_MAX_EXPANDED_BYTES:
                                raise InvalidRequestError(
                                    "技能压缩包解压后内容超过安全上限"
                                )
                            ratio = info.file_size / max(1, info.compress_size)
                            if info.file_size and ratio > SKILL_ARCHIVE_MAX_RATIO:
                                raise InvalidRequestError("技能压缩包包含异常压缩比文件")
                        members.append((info, pure, kind))

                    file_keys = {
                        pure.as_posix().casefold()
                        for _, pure, kind in members
                        if kind == "file"
                    }
                    for _, pure, _ in members:
                        parent_parts = pure.parts[:-1]
                        for index in range(1, len(parent_parts) + 1):
                            parent_key = PurePosixPath(*parent_parts[:index]).as_posix().casefold()
                            if parent_key in file_keys:
                                raise InvalidRequestError(
                                    "技能压缩包中同一路径不能同时作为文件和目录"
                                )

                    skill_files = [
                        pure
                        for _, pure, kind in members
                        if kind == "file" and pure.name.casefold() == "skill.md"
                    ]
                    if not skill_files:
                        raise InvalidRequestError("技能压缩包中没有找到 SKILL.md")
                    if len(skill_files) > SKILL_ARCHIVE_MAX_SKILLS:
                        raise InvalidRequestError(
                            f"单个压缩包最多包含 {SKILL_ARCHIVE_MAX_SKILLS} 个技能"
                        )
                    if any(
                        part.startswith(".")
                        for skill_file in skill_files
                        for part in skill_file.parent.parts
                    ):
                        raise InvalidRequestError("SKILL.md 不能位于隐藏目录中")

                    candidate_roots = sorted(
                        {skill_file.parent for skill_file in skill_files},
                        key=lambda path: (len(path.parts), path.as_posix().casefold()),
                    )
                    skill_roots: list[PurePosixPath] = []
                    for candidate in candidate_roots:
                        if any(
                            candidate == parent
                            or candidate.is_relative_to(parent)
                            for parent in skill_roots
                        ):
                            continue
                        skill_roots.append(candidate)

                    archive_stem = _skill_package_name(Path(archive_name).stem)
                    packages: list[tuple[str, PurePosixPath]] = []
                    package_names: set[str] = set()
                    for skill_root in skill_roots:
                        package_name = _skill_package_name(
                            archive_stem if str(skill_root) == "." else skill_root.name
                        )
                        key = package_name.casefold()
                        if key in package_names:
                            raise InvalidRequestError(
                                f"压缩包内多个技能会安装到同名目录：{package_name}"
                            )
                        package_names.add(key)
                        packages.append((package_name, skill_root))

                    existing_names = {
                        path.name.casefold(): path.name
                        for path in destination_root.iterdir()
                        if not path.name.startswith(".upload-")
                    }
                    for package_name, _ in packages:
                        existing = existing_names.get(package_name.casefold())
                        if existing is not None:
                            raise ConflictError(f"用户自建技能已存在：user_create/{existing}")

                    archive_root.mkdir(parents=True)
                    for info, pure, kind in members:
                        target = archive_root.joinpath(*pure.parts)
                        if kind == "directory":
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        written = 0
                        with archive.open(info, "r") as source, target.open("wb") as output:
                            while chunk := source.read(1024 * 1024):
                                written += len(chunk)
                                if written > info.file_size:
                                    raise InvalidRequestError("技能压缩包文件大小声明无效")
                                output.write(chunk)
                        if written != info.file_size:
                            raise InvalidRequestError("技能压缩包文件内容不完整")

                    canonical_skill_files: list[Path] = []
                    for skill_file in skill_files:
                        extracted = archive_root.joinpath(*skill_file.parts)
                        canonical = extracted.with_name("SKILL.md")
                        if extracted.name != "SKILL.md":
                            temporary_manifest = extracted.with_name(
                                f".skill-{uuid.uuid4().hex}.tmp"
                            )
                            os.replace(extracted, temporary_manifest)
                            os.replace(temporary_manifest, canonical)
                        canonical_skill_files.append(canonical)
                    try:
                        for skill_file in canonical_skill_files:
                            parse_skill_descriptor(skill_file, scope="user", root=self.root)
                    except Exception as exc:
                        raise InvalidRequestError(f"技能文件校验失败：{exc}") from None

                    packages_root.mkdir()
                    for package_name, skill_root in packages:
                        source = (
                            archive_root
                            if str(skill_root) == "."
                            else archive_root.joinpath(*skill_root.parts)
                        )
                        shutil.copytree(source, packages_root / package_name)

                for package_name, _ in packages:
                    target = destination_root / package_name
                    if target.exists():
                        raise ConflictError(f"用户自建技能已存在：user_create/{package_name}")
                    os.replace(packages_root / package_name, target)
                    moved_targets.append(target)
                try:
                    descriptors = load_prompt_source_registry(self.root, name).select_skills()
                except Exception as exc:
                    raise InvalidRequestError(f"技能注册校验失败：{exc}") from None

                descriptor_by_path = {descriptor.path.parent.resolve(): descriptor for descriptor in descriptors}
                installed = []
                for target in moved_targets:
                    descriptor = descriptor_by_path.get(target.resolve())
                    if descriptor is None:
                        raise InvalidRequestError(f"技能未被注册器发现：{target.name}")
                    installed.append(
                        {
                            "name": f"user_create/{target.name}",
                            "title": descriptor.title,
                            "path": target.relative_to(self.root).as_posix(),
                        }
                    )
                return {
                    "user": name,
                    "category": "user_created",
                    "installed": installed,
                    "count": len(installed),
                }
            except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
                if isinstance(exc, WebServiceError):
                    raise
                raise InvalidRequestError(f"技能压缩包处理失败：{exc}") from None
            finally:
                if sys.exc_info()[0] is not None:
                    for target in reversed(moved_targets):
                        if target.exists():
                            shutil.rmtree(target)
                if staging_root.exists():
                    shutil.rmtree(staging_root)

    def delete_skill(self, user: Any, category: Any, skill_name: Any) -> dict[str, Any]:
        name, normalized_category, logical_name, target = self._skill_directory(user, category, skill_name)
        if normalized_category not in _EDITABLE_SKILL_CATEGORIES:
            raise InvalidRequestError("基础插件与共享技能不允许从用户页面删除")
        _reject_tree_links(target)
        relative_path = target.relative_to(self.root).as_posix()
        shutil.rmtree(target)
        return {
            "user": name,
            "category": normalized_category,
            "name": logical_name,
            "path": relative_path,
            "deleted": True,
        }

    def set_skill_enabled(self, user: Any, category: Any, skill_name: Any, enabled: Any) -> dict[str, Any]:
        name, normalized_category, logical_name, _ = self._skill_directory(user, category, skill_name)
        if normalized_category not in {"builtin", "shared"}:
            raise InvalidRequestError("只有基础插件和共享技能支持白名单启用或禁用")
        if not isinstance(enabled, bool):
            raise InvalidRequestError("enabled 必须是布尔值")
        inventory = self.skills(name)
        candidates = {
            item["name"]
            for item in inventory["items"]
            if item["category"] == normalized_category
        }
        selected = {
            item["name"]
            for item in inventory["items"]
            if item["category"] == normalized_category and item["enabled"]
        }
        if enabled:
            selected.add(logical_name)
        else:
            selected.discard(logical_name)
        whitelist = [] if selected == candidates else sorted(selected) or ["__kemo_none__"]
        changes = (
            {"plugins": {"whitelist": whitelist}}
            if normalized_category == "builtin"
            else {"skills": {"shared_whitelist": whitelist}}
        )
        self.patch_user_config(name, changes)
        return {
            "user": name,
            "category": normalized_category,
            "name": logical_name,
            "enabled": enabled,
            "whitelist": whitelist,
        }

    def skill_archive(self, user: Any, category: Any, skill_name: Any) -> tuple[str, bytes]:
        _, normalized_category, logical_name, target = self._skill_directory(user, category, skill_name)
        if normalized_category not in {"builtin", "shared"}:
            raise InvalidRequestError("用户技能请通过编辑器管理，不提供系统技能下载入口")
        _reject_tree_links(target)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(target.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts or path.name.startswith("."):
                    continue
                archive.write(path, (Path(target.name) / path.relative_to(target)).as_posix())
        filename = f"{Path(logical_name).name}.zip"
        return filename, buffer.getvalue()


