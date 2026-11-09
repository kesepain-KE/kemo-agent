"""Reviewed local ZIP installation using the existing module directory roots."""

from __future__ import annotations

import hashlib
import io
import os
import secrets
import shutil
import stat
import tempfile
import time
import zipfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import yaml

from run.extensions import module_execution_lock
from run.modules import read_manifest
from run.modules.spec import ModuleKind
from run.modules.paths import module_path
from web.errors import ConflictError, InvalidRequestError
from web.services._paths import _reject_tree_links
from web.services._io import atomic_write
from web.services.module_panels import load_module_panel
from web.services.module_workspace import ModuleWorkspaceService
from web.services.module_workspace_schema import fingerprint, workspace_declaration

ARCHIVE_MAX_BYTES = 16 * 1024 * 1024
_EXPANDED_MAX = 64 * 1024 * 1024


def _base(service: ModuleWorkspaceService, user: str, kind: str, scope: str) -> Path:
    service.backend.require_user(user)
    service._kind(kind)
    roots = {
        ("sense", "global"): "sense",
        ("expand", "global"): "expand/global",
        ("expand", "shared"): "expand/share",
        ("expand", "user"): f"users/{user}/expand",
    }
    relative = roots.get((kind, scope))
    if relative is None:
        raise InvalidRequestError("该模块类型不支持所选安装作用域")
    path = module_path(service.root, relative, file=False)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _revision(path: Path) -> str:
    if not path.exists():
        return ""
    _reject_tree_links(path)
    files = {}
    for file in path.rglob("*"):
        relative = file.relative_to(path).as_posix()
        if not file.is_file() or any(p.startswith(".") or p == "__pycache__" for p in file.relative_to(path).parts):
            continue
        if relative == "module.yaml":
            raw = yaml.safe_load(file.read_text("utf-8-sig"))
            if isinstance(raw, dict):
                raw = {k: v for k, v in raw.items() if k not in {"health", "recent_update"}}
            files[relative] = fingerprint(raw)
        elif relative.startswith("ui/") or file.suffix == ".py" or relative in {"module/panel.json", "module/workspace.json", "README.md"}:
            files[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    return fingerprint(files)


def _extract(data: bytes, filename: str, stage: Path, kind: ModuleKind, scope: str) -> tuple[Path, Any, dict[str, Any]]:
    if len(data) > ARCHIVE_MAX_BYTES or not filename.lower().endswith(".zip"):
        raise InvalidRequestError("请上传不超过 16 MiB 的 ZIP 模块包")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 4096 or sum(e.file_size for e in entries) > _EXPANDED_MAX:
                raise InvalidRequestError("安装包超过 4096 文件或解压后 64 MiB")
            names = [e.filename.rstrip("/") for e in entries if not e.is_dir()]
            if "module.yaml" in names:
                module = Path(filename).stem
                prefix = ""
            else:
                roots = {n.split("/")[0] for n in names}
                if len(roots) != 1:
                    raise InvalidRequestError("ZIP 必须包含一个模块根目录或根目录 module.yaml")
                module = next(iter(roots))
                prefix = module + "/"
            if module.startswith(".") or module in {"", "__pycache__"} or "/" in module or "\\" in module:
                raise InvalidRequestError("非法模块目录名")
            root = module_path(stage, module, file=False)
            root.mkdir()
            seen = set()
            for entry in entries:
                name = entry.filename.rstrip("/")
                module_path(stage, name, file=False)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                    raise InvalidRequestError("安装包不允许链接或特殊文件")
                relative = name[len(prefix):] if prefix and name.startswith(prefix) else name
                if not relative and entry.is_dir():
                    continue
                if prefix and not name.startswith(prefix):
                    raise InvalidRequestError("ZIP 含模块目录外文件")
                if entry.is_dir():
                    continue
                identity = relative.casefold()
                if identity in seen:
                    raise InvalidRequestError("安装包包含重复或大小写冲突文件")
                seen.add(identity)
                if any(p.startswith(".") or p == "__pycache__" for p in relative.split("/")):
                    raise InvalidRequestError("安装包不允许隐藏文件、凭据或运行锁")
                target = module_path(root, relative, file=False)
                target.parent.mkdir(parents=True, exist_ok=True)
                content = archive.read(entry)
                target.write_bytes(content)
            spec = read_manifest(root, kind_hint=kind, scope=scope)
            declaration = workspace_declaration(spec)
            return root, spec, declaration
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError) as exc:
        raise InvalidRequestError(f"模块包校验失败：{exc}") from exc


def preview_import(service: ModuleWorkspaceService, user: str, kind: str, scope: str, filename: str, data: bytes) -> dict[str, Any]:
    base = _base(service, user, kind, scope)
    temporary_root = service.root / "tmp"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="module-import-", dir=temporary_root) as temporary:
        root, spec, declaration = _extract(data, filename, Path(temporary), service._kind(kind), scope)
        target = module_path(base, root.name, file=False)
        result = {
            "module": root.name, "title": spec.name, "kind": kind, "scope": scope,
            "updating": target.exists(), "target_revision": _revision(target),
            "files": sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()),
            "permissions": declaration["requested_permissions"],
            "entrypoints": [str(p.relative_to(root)) for p in (spec.update_script, spec.control_script, spec.ui_action_entry) if p],
        }
    with service.lock:
        now = time.monotonic()
        service.imports = {key: value for key, value in service.imports.items() if value["expires"] > now}
        if len(service.imports) >= 4:
            raise ConflictError("待确认安装包过多，请先完成或等待过期")
        token = secrets.token_urlsafe(24)
        service.imports[token] = {
            "user": user, "kind": kind, "scope": scope, "filename": filename,
            "data": data, "preview": result, "expires": now + 600,
        }
        return {**result, "import_id": token, "expires_in": 600}


def install_import(service: ModuleWorkspaceService, user: str, token: str) -> dict[str, Any]:
    with service.lock:
        package = service.imports.get(token)
        if package is None or package["user"] != user or package["expires"] <= time.monotonic():
            raise ConflictError("安装预览已失效，请重新上传")
        kind, scope = package["kind"], package["scope"]
        base = _base(service, user, kind, scope)
        target = module_path(base, package["preview"]["module"], file=False)
        temporary_root = service.root / "tmp"
        with tempfile.TemporaryDirectory(prefix="module-install-", dir=temporary_root) as temporary:
            stage, _, _ = _extract(package["data"], package["filename"], Path(temporary), service._kind(kind), scope)
            with module_execution_lock(target, timeout=15) if target.exists() else nullcontext():
                if _revision(target) != package["preview"]["target_revision"]:
                    raise ConflictError("目标模块在预览后发生变化，请重新审查")
                if target.exists():
                    old_spec = read_manifest(target, kind_hint=service._kind(kind), scope=scope)
                    protected = {".env", "_runtime.json", "_last_run.json"}
                    if old_spec.input_file is not None:
                        protected.add(old_spec.input_file.relative_to(target).as_posix())
                    old_panel, _ = load_module_panel(target, service._kind(kind))
                    for container in (old_panel or {}).get("containers", []):
                        if container["kind"] in {"config", "status"}:
                            protected.add(container["values" if container["kind"] == "config" else "source"])
                    declaration_paths = {"module/panel.json", "module/workspace.json"}
                    if old_spec.ui_workspace is not None:
                        declaration_paths.add(old_spec.ui_workspace.relative_to(target).as_posix())
                    for path in target.rglob("*"):
                        relative = path.relative_to(target).as_posix()
                        if path.is_file() and (
                            relative in protected
                            or path.relative_to(target).parts[0] in {"data", "artifacts", "logs", "runtime"}
                            or (relative.startswith("module/") and relative not in declaration_paths and path.suffix != ".py")
                            or path.suffix in {".sqlite", ".sqlite3", ".db"}
                        ):
                            destination = module_path(stage, relative, file=False)
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(path, destination)
                backup = temporary_root / f"module-backup-{target.name}-{secrets.token_hex(8)}"
                moved = False
                if target.exists():
                    # Windows cannot rename a directory containing the open
                    # execution lock. Keep that lock and replace reviewed files
                    # atomically, publishing module.yaml last. Host inventory is
                    # serialized by service.lock for the whole installation.
                    shutil.copytree(target, backup, ignore=shutil.ignore_patterns("__pycache__", ".module.execution.lock", ".runtime.lock"))
                    files = sorted((p for p in stage.rglob("*") if p.is_file()), key=lambda p: p.name == "module.yaml")
                    replaced: list[tuple[Path, bool]] = []
                    try:
                        for source in files:
                            relative = source.relative_to(stage).as_posix()
                            destination = module_path(target, relative, file=False)
                            existed = destination.exists()
                            atomic_write(destination, source.read_bytes())
                            replaced.append((destination, existed))
                    except OSError:
                        for destination, existed in reversed(replaced):
                            if existed:
                                original = backup / destination.relative_to(target)
                                atomic_write(destination, original.read_bytes())
                            else:
                                destination.unlink(missing_ok=True)
                        raise
                    moved = True
                else:
                    os.replace(stage, target)
            service.invalidate(user, kind, scope, target.name)
            del service.imports[token]
        return {
            "installed": True, "module": target.name, "kind": kind, "scope": scope,
            "backup": str(backup.relative_to(service.root)) if moved else None,
        }
