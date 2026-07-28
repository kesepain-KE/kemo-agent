"""Web 领域服务共享的安全路径、目录和归档辅助函数。"""

from __future__ import annotations

import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any
import zipfile

from web.constants import _WINDOWS_INVALID_PATH_CHARS, _WINDOWS_RESERVED_NAMES
from web.errors import InvalidRequestError


def _visible_children(directory: Path) -> list[Path]:
    try:
        children = [
            item
            for item in directory.iterdir()
            if not item.name.startswith(".")
            and item.name != "__pycache__"
            and not item.is_symlink()
            and not getattr(item, "is_junction", lambda: False)()
        ]
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError:
        return []
    children.sort(
        key=lambda item: (
            0 if item.is_dir() else 1,
            item.name.casefold(),
            item.name,
        )
    )
    return children
def _directory_listing(
    directory: Path,
    *,
    path: str = "",
    search: str = "",
    page: int = 1,
    page_size: int = 6,
) -> dict[str, Any]:
    summary = {"total_files": 0, "total_dirs": 0, "total_size": 0}
    entries: list[dict[str, Any]] = []
    normalized_path = path.strip().replace("\\", "/") if isinstance(path, str) else ""
    normalized_search = search.strip().casefold() if isinstance(search, str) else ""

    if normalized_path:
        normalized_path, current_directory = _safe_relative_target(directory, normalized_path)
        _reject_link_path(directory.resolve(), current_directory)
        if not current_directory.is_dir():
            normalized_path = ""

    def visit(current: Path) -> tuple[int, float, int]:
        total_size = 0
        updated_at = 0.0
        children = _visible_children(current)
        for item in children:
            relative = item.relative_to(directory).as_posix()
            parent_path = item.parent.relative_to(directory).as_posix()
            if parent_path == ".":
                parent_path = ""
            try:
                if item.is_dir():
                    summary["total_dirs"] += 1
                    child_size, child_updated_at, child_count = visit(item)
                    entry = {
                        "type": "directory",
                        "name": item.name,
                        "relative_path": relative,
                        "parent_path": parent_path,
                        "size": child_size,
                        "updated_at": child_updated_at,
                        "extension": "",
                        "child_count": child_count,
                    }
                    total_size += child_size
                    updated_at = max(updated_at, child_updated_at)
                elif item.is_file():
                    stat = item.stat()
                    summary["total_files"] += 1
                    summary["total_size"] += stat.st_size
                    entry = {
                        "type": "file",
                        "name": item.name,
                        "relative_path": relative,
                        "parent_path": parent_path,
                        "size": stat.st_size,
                        "updated_at": stat.st_mtime,
                        "extension": item.suffix.lower(),
                        "child_count": 0,
                    }
                    total_size += stat.st_size
                    updated_at = max(updated_at, stat.st_mtime)
                else:
                    continue
            except OSError:
                continue

            if normalized_search:
                haystack = f"{entry['name']} {entry['relative_path']}".casefold()
                if normalized_search in haystack:
                    entries.append(entry)
            elif parent_path == normalized_path:
                entries.append(entry)
        return total_size, updated_at, len(children)

    visit(directory)
    entries.sort(
        key=lambda item: (
            0 if item["type"] == "directory" else 1,
            str(item["name"]).casefold(),
            item["name"],
            item["relative_path"],
        )
    )
    requested_page = max(1, int(page))
    normalized_page_size = min(100, max(1, int(page_size)))
    total_items = len(entries)
    total_pages = max(1, math.ceil(total_items / normalized_page_size))
    current_page = min(requested_page, total_pages)
    start = (current_page - 1) * normalized_page_size
    paged_entries = entries[start : start + normalized_page_size]
    return {
        "summary": summary,
        "entries": paged_entries,
        "path": normalized_path,
        "search": search.strip() if isinstance(search, str) else "",
        "pagination": {
            "page": current_page,
            "page_size": normalized_page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_previous": current_page > 1,
            "has_next": current_page < total_pages,
        },
    }
def _flat_files(directory: Path, *, relative_to: Path | None = None) -> list[dict[str, Any]]:
    base = relative_to or directory
    files: list[dict[str, Any]] = []

    def visit(current: Path) -> None:
        for item in _visible_children(current):
            try:
                if item.is_dir():
                    visit(item)
                elif item.is_file():
                    stat = item.stat()
                    files.append(
                        {
                            "name": item.name,
                            "relative_path": item.relative_to(base).as_posix(),
                            "size": stat.st_size,
                            "updated_at": stat.st_mtime,
                        }
                    )
            except OSError:
                continue

    visit(directory)
    files.sort(key=lambda item: (str(item["relative_path"]).casefold(), item["relative_path"]))
    return files
def _agent_registration_value(content: str, label: str) -> str:
    pattern = re.compile(
        rf"^\s*-\s*\*\*{re.escape(label)}\*\*\s*[:：]\s*(.+?)\s*$",
        re.MULTILINE,
    )
    match = pattern.search(content or "")
    return match.group(1).strip() if match else ""
def _reject_tree_links(directory: Path) -> None:
    for current, directories, files in os.walk(directory, topdown=True, followlinks=False):
        for name in [*directories, *files]:
            path = Path(current) / name
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise InvalidRequestError("用户子代理目录不能包含符号链接或目录联接")
def _safe_relative_target(directory: Path, value: Any) -> tuple[str, Path]:
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError("path 必须是非空相对路径")
    normalized = value.strip().replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        pure.is_absolute()
        or Path(normalized).is_absolute()
        or ".." in pure.parts
        or "\x00" in normalized
        or (pure.parts and ":" in pure.parts[0])
    ):
        raise InvalidRequestError("path 必须位于允许的目录内且不能包含 ..")
    root = directory.resolve()
    candidate = root.joinpath(*pure.parts)
    target = candidate.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise InvalidRequestError("path 越出允许的目录") from None
    return pure.as_posix(), candidate
def _reject_link_path(root: Path, target: Path) -> None:
    """Reject existing symlink/junction components for every Web mutation."""

    current = root.resolve()
    try:
        relative = target.relative_to(root)
    except ValueError:
        raise InvalidRequestError("path 越出允许的目录") from None
    for part in relative.parts:
        current = current / part
        if current.exists() and (
            current.is_symlink() or getattr(current, "is_junction", lambda: False)()
        ):
            raise InvalidRequestError("Web 文件操作不允许符号链接或目录联接")
def _validated_skill_archive_path(value: str) -> PurePosixPath:
    """Normalize one ZIP member while keeping extraction portable and contained."""

    normalized = str(value or "").replace("\\", "/")
    if not normalized or "\x00" in normalized or normalized.startswith("/"):
        raise InvalidRequestError("技能压缩包包含无效路径")
    pure = PurePosixPath(normalized.rstrip("/"))
    if not pure.parts or pure.is_absolute() or ".." in pure.parts:
        raise InvalidRequestError("技能压缩包包含路径穿越或绝对路径")
    for part in pure.parts:
        if part in {"", ".", ".."}:
            raise InvalidRequestError("技能压缩包包含无效路径片段")
        if part.endswith((" ", ".")) or any(char in _WINDOWS_INVALID_PATH_CHARS for char in part):
            raise InvalidRequestError(f"技能压缩包路径不兼容 Windows：{part}")
        device_name = part.split(".", 1)[0].upper()
        if device_name in _WINDOWS_RESERVED_NAMES:
            raise InvalidRequestError(f"技能压缩包包含 Windows 保留名称：{part}")
    return pure
def _zip_member_kind(info: zipfile.ZipInfo) -> str:
    """Return file/directory and reject links or other special archive entries."""

    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK or (info.external_attr & 0x400):
        raise InvalidRequestError("技能压缩包不能包含符号链接、目录联接或重解析点")
    is_directory = info.is_dir() or file_type == stat.S_IFDIR
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise InvalidRequestError("技能压缩包不能包含设备、套接字等特殊文件")
    return "directory" if is_directory else "file"
def _skill_package_name(value: str) -> str:
    candidate = str(value or "").strip()
    _validated_skill_archive_path(candidate)
    if "/" in candidate or "\\" in candidate:
        raise InvalidRequestError("技能目录名必须是单个文件夹名称")
    if candidate.startswith("."):
        raise InvalidRequestError("技能目录名不能以点开头")
    return candidate
def _image_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None

