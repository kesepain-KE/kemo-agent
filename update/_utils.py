"""Shared helpers for the modular kemo-agent updater."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent


class UpdateError(RuntimeError):
    """Raised for expected updater failures that should be shown to users."""


def green(text: str) -> str:
    return f"\033[0;32m{text}\033[0m"


def yellow(text: str) -> str:
    return f"\033[1;33m{text}\033[0m"


def red(text: str) -> str:
    return f"\033[0;31m{text}\033[0m"


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask_yes_no(prompt: str, *, default: bool, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not is_interactive():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    answer = input(prompt + suffix).strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def ask_choice(
    prompt: str,
    choices: dict[str, str],
    *,
    default: str,
    assume_yes: bool,
) -> str:
    if assume_yes or not is_interactive():
        return default
    print(prompt)
    for key, label in choices.items():
        mark = " (默认)" if key == default else ""
        print(f"  {key}) {label}{mark}")
    while True:
        answer = input("> ").strip().lower()
        if not answer:
            return default
        if answer in choices:
            return answer
        print("请选择: " + ", ".join(choices))


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    dry_run: bool = False,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd))
    if dry_run and not capture:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    kwargs: dict = {"text": True}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    return subprocess.run(cmd, cwd=str(cwd or ROOT), check=True, **kwargs)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def require_commands(names: Iterable[str]) -> None:
    missing = [name for name in names if not command_exists(name)]
    if missing:
        raise UpdateError(f"缺少必需命令: {', '.join(missing)}")


def read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise UpdateError(f"无法读取 JSON 文件 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise UpdateError(f"{path} 不是 JSON 对象")
    return data


def write_json_atomic(path: Path, data: dict) -> None:
    """Write a JSON object atomically without exposing a partial manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "kemo-agent-updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"无法读取远程版本文件 {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise UpdateError(f"远程 JSON 不是对象: {url}")
    return data


def parse_version(value: str) -> tuple[int, ...]:
    text = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.\d+)*", text):
        raise UpdateError(f"无效版本号: {value!r}")
    return tuple(int(part) for part in text.split("."))


def compare_versions(left: str, right: str) -> int:
    left_parts = parse_version(left)
    right_parts = parse_version(right)
    size = max(len(left_parts), len(right_parts))
    left_parts += (0,) * (size - len(left_parts))
    right_parts += (0,) * (size - len(right_parts))
    return (left_parts > right_parts) - (left_parts < right_parts)


def tree_digest(path: Path) -> str:
    if not path.exists():
        return ""
    if path.is_file():
        digest = hashlib.sha256()
        digest.update(path.read_bytes())
        return digest.hexdigest()
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def paths_differ(left: Path, right: Path) -> bool:
    return tree_digest(left) != tree_digest(right)


def _parse_excludes(excludes: Iterable[str]) -> tuple[set[str], set[str]]:
    directories: set[str] = set()
    files: set[str] = set()
    for pattern in excludes:
        normalized = pattern.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.endswith("/"):
            directories.add(normalized.rstrip("/"))
        else:
            files.add(normalized)
    return directories, files


def _is_excluded(rel: str, dir_patterns: set[str], file_patterns: set[str]) -> bool:
    normalized = rel.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in file_patterns:
        return True
    parts = normalized.split("/")
    for pattern in dir_patterns:
        if "/" in pattern:
            if normalized == pattern or normalized.startswith(pattern + "/"):
                return True
        elif pattern in parts:
            return True
    return False


def _short(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _walk_sync(
    source: Path,
    target: Path,
    *,
    dir_patterns: set[str],
    file_patterns: set[str],
    dry_run: bool,
) -> None:
    for root, dirs, files in os.walk(str(source)):
        root_path = Path(root)
        relative = root_path.relative_to(source).as_posix()
        if relative == ".":
            relative = ""
        if relative and _is_excluded(relative, dir_patterns, file_patterns):
            dirs.clear()
            continue
        dirs[:] = [
            name
            for name in dirs
            if not _is_excluded(f"{relative}/{name}" if relative else name, dir_patterns, file_patterns)
        ]
        target_dir = target / relative if relative else target
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
        for filename in files:
            relative_file = f"{relative}/{filename}" if relative else filename
            if _is_excluded(relative_file, dir_patterns, file_patterns):
                continue
            source_file = root_path / filename
            target_file = target_dir / filename
            if target_file.is_file() and tree_digest(source_file) == tree_digest(target_file):
                continue
            if dry_run:
                print(f"[dry-run]  复制  {_short(source_file)} -> {_short(target_file)}")
            else:
                shutil.copy2(source_file, target_file, follow_symlinks=False)


def _walk_delete(
    source: Path,
    target: Path,
    *,
    dir_patterns: set[str],
    file_patterns: set[str],
    dry_run: bool,
) -> None:
    if not target.is_dir():
        return
    for root, dirs, files in os.walk(str(target), topdown=False):
        root_path = Path(root)
        relative = root_path.relative_to(target).as_posix()
        if relative == ".":
            relative = ""
        if relative and _is_excluded(relative, dir_patterns, file_patterns):
            continue
        for filename in files:
            relative_file = f"{relative}/{filename}" if relative else filename
            if _is_excluded(relative_file, dir_patterns, file_patterns):
                continue
            if not (source / relative_file).exists():
                target_file = root_path / filename
                if dry_run:
                    print(f"[dry-run]  删除  {_short(target_file)}")
                else:
                    target_file.unlink()
        if relative and not _is_excluded(relative, dir_patterns, file_patterns):
            if not (source / relative).exists():
                try:
                    if dry_run:
                        print(f"[dry-run]  删除目录  {_short(root_path)}")
                    else:
                        root_path.rmdir()
                except OSError:
                    pass


def sync_directory(
    source: Path,
    target: Path,
    *,
    delete: bool = False,
    excludes: Iterable[str] = (),
    dry_run: bool = False,
) -> None:
    if not source.is_dir():
        raise UpdateError(f"源目录不存在: {source}")
    directory_patterns, file_patterns = _parse_excludes(excludes)
    action = "同步 (含删除)" if delete else "同步"
    print(f"  {action}  {_short(source)} -> {_short(target)}")
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
    _walk_sync(
        source,
        target,
        dir_patterns=directory_patterns,
        file_patterns=file_patterns,
        dry_run=dry_run,
    )
    if delete:
        _walk_delete(
            source,
            target,
            dir_patterns=directory_patterns,
            file_patterns=file_patterns,
            dry_run=dry_run,
        )


def copy_file_safe(source: Path, target: Path, *, dry_run: bool = False) -> bool:
    """Copy one file if it exists and differs, returning whether it changed."""
    if not source.is_file():
        return False
    if target.is_file() and tree_digest(source) == tree_digest(target):
        return False
    if dry_run:
        print(f"[dry-run]  复制  {_short(source)} -> {_short(target)}")
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=False)
    return True


def sync_file_only(source: Path, target: Path, *, dry_run: bool = False) -> None:
    """Synchronize one file; directories are explicitly rejected."""
    if source.is_dir():
        raise ValueError(f"sync_file_only 不支持目录: {source}")
    copy_file_safe(source, target, dry_run=dry_run)


def sync_files_by_names(
    source_dir: Path,
    target_dir: Path,
    names: list[str],
    *,
    dry_run: bool = False,
) -> list[str]:
    """Synchronize selected file names and return the names that changed."""
    changed: list[str] = []
    for name in names:
        source = source_dir / name
        if source.is_dir():
            raise ValueError(f"sync_files_by_names 只支持文件: {source}")
        if copy_file_safe(source, target_dir / name, dry_run=dry_run):
            changed.append(name)
    return changed


def sync_directory_except(
    source: Path,
    target: Path,
    except_relative: list[str],
    *,
    delete: bool = True,
    dry_run: bool = False,
) -> None:
    """Synchronize a directory while preserving selected relative paths."""
    sync_directory(
        source,
        target,
        delete=delete,
        excludes=except_relative,
        dry_run=dry_run,
    )


def _resolve_npm_command() -> str | None:
    resolved = shutil.which("npm")
    if not resolved:
        return None
    if os.name == "nt" and not resolved.lower().endswith(".exe"):
        command_path = resolved + ".cmd"
        if os.path.isfile(command_path):
            return command_path
    return resolved
