"""Web 领域服务共享的安全路径、目录和归档辅助函数。"""

from __future__ import annotations

import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Callable, NamedTuple
import zipfile

from web.constants import _WINDOWS_INVALID_PATH_CHARS, _WINDOWS_RESERVED_NAMES
from web.errors import InvalidRequestError
from run.infra import BoundedReadCache


_DIRECTORY_SCAN_MAX_ENTRIES = 10_000
_DIRECTORY_SCAN_MAX_DEPTH = 32
_DIRECTORY_CACHE_TTL_SECONDS = 10.0
_DIRECTORY_CACHE = BoundedReadCache(
    max_entries=256, max_bytes=16 * 1024 * 1024, per_owner=128,
    per_owner_bytes=8 * 1024 * 1024,
)


class _DirectoryItem(NamedTuple):
    name: str
    relative_path: str
    is_dir: bool
    size: int
    updated_at: float
    child_count: int = 0

    def __deepcopy__(self, memo):
        # The cache copies containers; these rows contain only immutable values.
        return self


def _directory_cache_key(directory: Path) -> str:
    return os.path.normcase(str(directory.resolve()))


def _invalidate_directory_summary(directory: Path) -> None:
    """Invalidate the recursive and direct listings for one file scope.

    All Web mutations call this explicitly.  The short TTL remains a bounded
    fallback for files written directly by plugins, agents, or external tools.
    An in-flight fence prevents a racing scan from restoring stale data.
    """

    key = _directory_cache_key(directory)
    _DIRECTORY_CACHE.invalidate(lambda candidate: isinstance(candidate, tuple) and candidate[0] == key)


def _scandir_children(directory: str | Path) -> list[os.DirEntry[str]]:
    children = []
    try:
        with os.scandir(directory) as scan:
            for item in scan:
                if item.name.startswith(".") or item.name == "__pycache__" or item.is_symlink():
                    continue
                is_junction = getattr(item, "is_junction", None)
                if is_junction is not None:
                    if is_junction():
                        continue
                elif os.name == "nt" and item.stat(follow_symlinks=False).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT:
                    continue
                children.append(item)
    except OSError:
        return []
    return children


def _scan_order(item: os.DirEntry[str]) -> tuple[bool, str, str]:
    return (not item.is_dir(follow_symlinks=False), item.name.casefold(), item.name)


def _scan_directory_snapshot(directory: Path) -> tuple[dict[str, Any], list[_DirectoryItem]]:
    summary = {
        "total_files": 0,
        "total_dirs": 0,
        "total_size": 0,
        "scanned_entries": 0,
        "truncated": False,
    }
    entries: list[_DirectoryItem] = []

    def visit(current: str | Path, prefix: str, depth: int) -> tuple[int, float, int]:
        if depth > _DIRECTORY_SCAN_MAX_DEPTH:
            summary["truncated"] = True
            return 0, 0.0, 0
        children = sorted(_scandir_children(current), key=_scan_order)
        size, updated_at = 0, 0.0
        for child in children:
            if summary["scanned_entries"] >= _DIRECTORY_SCAN_MAX_ENTRIES:
                summary["truncated"] = True
                break
            summary["scanned_entries"] += 1
            try:
                info = child.stat(follow_symlinks=False)
            except OSError:
                continue
            relative = prefix + child.name
            if stat.S_ISDIR(info.st_mode):
                summary["total_dirs"] += 1
                child_size, child_time, count = visit(child.path, relative + "/", depth + 1)
                entry = _DirectoryItem(child.name, relative, True, child_size, child_time, count)
            elif stat.S_ISREG(info.st_mode):
                summary["total_files"] += 1
                summary["total_size"] += info.st_size
                entry = _DirectoryItem(child.name, relative, False, info.st_size, info.st_mtime)
            else:
                continue
            entries.append(entry)
            size += entry.size
            updated_at = max(updated_at, entry.updated_at)
        return size, updated_at, len(children)

    visit(directory, "", 0)
    return summary, entries


def _scan_direct_children(directory: str, parent: str) -> tuple[list[_DirectoryItem], int, int, float]:
    children = _scandir_children(directory)
    entries = []
    size, updated_at = 0, -math.inf
    prefix = parent + "/" if parent else ""
    for child in children:
        try:
            info = child.stat(follow_symlinks=False)
        except OSError:
            continue
        updated_at = max(updated_at, info.st_mtime)
        if stat.S_ISDIR(info.st_mode):
            entries.append(_DirectoryItem(child.name, prefix + child.name, True, 0, info.st_mtime))
        elif stat.S_ISREG(info.st_mode):
            entries.append(_DirectoryItem(child.name, prefix + child.name, False, info.st_size, info.st_mtime))
            size += info.st_size
    return entries, len(children), size, updated_at


def _visible_children(directory: Path) -> list[Path]:
    # Callers operating on files (rather than listing them) still use Path.
    return [Path(item.path) for item in sorted(_scandir_children(directory), key=_scan_order)]


def _directory_listing(
    directory: Path,
    *,
    path: str = "",
    search: str = "",
    page: int = 1,
    page_size: int = 6,
    sort_by: str = "name",
    sort_order: str = "asc",
    refresh: bool = False,
) -> dict[str, Any]:
    if sort_by not in ("name", "updated_at", "size"):
        raise InvalidRequestError("sort_by 只允许 name、updated_at 或 size")
    if sort_order not in ("asc", "desc"):
        raise InvalidRequestError("sort_order 只允许 asc 或 desc")
    normalized_path = path.strip().replace("\\", "/") if isinstance(path, str) else ""
    normalized_search = search.strip().casefold() if isinstance(search, str) else ""
    current_directory = directory

    if normalized_path:
        normalized_path, current_directory = _safe_relative_target(directory, normalized_path)
        _reject_link_path(directory.resolve(), current_directory)
        if not current_directory.is_dir():
            normalized_path = ""
            current_directory = directory

    if refresh:
        _invalidate_directory_summary(directory)
    root = _directory_cache_key(directory)
    parent = "" if normalized_path == "." else normalized_path

    def cached(kind: str, path: str, load: Callable[[], Any]) -> Any:
        return _DIRECTORY_CACHE.get_or_load(
            (root, kind, path), load, owner=os.path.dirname(root), ttl=_DIRECTORY_CACHE_TTL_SECONDS,
        )[0]

    def scan_direct(path: str):
        current = os.fspath(current_directory) if path == parent else os.path.join(directory, path)
        if path != parent:
            # A cached directory entry may since have been replaced by a link.
            try:
                info = os.lstat(current)
            except OSError:
                return [], 0, 0, -math.inf
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", 0) == stat.IO_REPARSE_TAG_MOUNT_POINT:
                return [], 0, 0, -math.inf
        return _scan_direct_children(current, path)

    def direct(path: str):
        return cached("direct", path, lambda: scan_direct(path))

    def hydrate(entry: _DirectoryItem, load: Callable):
        if not entry.is_dir:
            return entry
        _, count, size, updated_at = load(entry.relative_path)
        return entry._replace(size=size, updated_at=max(entry.updated_at, updated_at), child_count=count)

    summary, snapshot = cached("recursive", "", lambda: _scan_directory_snapshot(directory))
    if normalized_search:
        entries = [entry for entry in snapshot if normalized_search in f"{entry.name} {entry.relative_path}".casefold()]
    elif sort_by == "name":
        entries = direct(parent)[0]
    else:
        # Numeric sorting needs every directory's direct stats, not just the current page.
        entries = cached("direct-numeric", parent, lambda: [hydrate(entry, scan_direct) for entry in direct(parent)[0]])
    entries.sort(
        key=lambda item: (item.name.casefold(), item.name, item.relative_path),
        reverse=sort_by == "name" and sort_order == "desc",
    )
    if sort_by != "name":
        entries.sort(key=lambda item: item.size if sort_by == "size" else item.updated_at, reverse=sort_order == "desc")
    # Keep folders first in either direction; ties retain deterministic names/paths.
    entries.sort(key=lambda item: not item.is_dir)
    requested_page = max(1, int(page))
    normalized_page_size = min(100, max(1, int(page_size)))
    total_items = len(entries)
    total_pages = max(1, math.ceil(total_items / normalized_page_size))
    current_page = min(requested_page, total_pages)
    start = (current_page - 1) * normalized_page_size
    paged_entries = entries[start : start + normalized_page_size]
    if not normalized_search and sort_by == "name":
        paged_entries = [hydrate(entry, direct) for entry in paged_entries]
    return {
        "summary": summary,
        "entries": [
            {
                "type": "directory" if entry.is_dir else "file",
                "name": entry.name,
                "relative_path": entry.relative_path,
                "parent_path": entry.relative_path.rpartition("/")[0],
                "size": entry.size,
                "updated_at": entry.updated_at,
                "extension": "" if entry.is_dir else PurePosixPath(entry.name).suffix.lower(),
                "child_count": entry.child_count,
            }
            for entry in paged_entries
        ],
        "path": normalized_path,
        "search": search.strip() if isinstance(search, str) else "",
        "sort_by": sort_by,
        "sort_order": sort_order,
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
