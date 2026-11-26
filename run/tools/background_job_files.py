"""Directory boundaries and filesystem artifacts for managed Shell jobs."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Any

from run.config import user_dir


_JOB_ID_RE = re.compile(r"^job_[a-f0-9]{32}$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_JOB_ARTIFACT_SUFFIXES = (".json", ".request.json", ".stdout.log", ".stderr.log")


def _is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("后台作业目录不可检查") from exc
    return bool(stat.S_ISLNK(info.st_mode)) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _assert_safe_directory_chain(root: Path, user: str) -> Path:
    root = root.resolve()
    users_root = root / "users"
    base = user_dir(user, root)
    for candidate in (users_root, base):
        if _is_reparse_point(candidate):
            raise ValueError("用户目录不能是符号链接或目录联接")
    try:
        resolved_users = users_root.resolve(strict=True)
        resolved_base = base.resolve(strict=True)
        resolved_base.relative_to(resolved_users)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError("用户目录越出 users 根目录") from exc
    return resolved_base


def _jobs_dir(root: Path, user: str, *, create: bool = True) -> Path:
    base = _assert_safe_directory_chain(root, user)
    directory = base / "runtime" / "background_jobs"
    runtime = base / "runtime"
    if _is_reparse_point(runtime) or _is_reparse_point(directory):
        raise ValueError("后台作业目录不能是符号链接或目录联接")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    resolved = directory.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError("后台作业目录越出用户目录") from exc
    return resolved


def _validate_job_id(job_id: Any) -> str:
    value = str(job_id or "").strip()
    if not _JOB_ID_RE.fullmatch(value):
        raise ValueError("job_id 格式无效")
    return value


def job_paths(root: Path, user: str, job_id: str) -> dict[str, Path]:
    normalized = _validate_job_id(job_id)
    directory = _jobs_dir(root, user)
    return {
        "record": directory / f"{normalized}.json",
        "request": directory / f"{normalized}.request.json",
        "stdout": directory / f"{normalized}.stdout.log",
        "stderr": directory / f"{normalized}.stderr.log",
    }


def _artifact_paths(directory: Path, job_id: str) -> tuple[Path, ...]:
    normalized = _validate_job_id(job_id)
    return tuple(directory / f"{normalized}{suffix}" for suffix in _JOB_ARTIFACT_SUFFIXES)


def _remove_job_artifacts(directory: Path, job_id: str) -> None:
    for path in _artifact_paths(directory, job_id):
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
        except OSError:
            continue


def _storage_bytes(directory: Path) -> int:
    total = 0
    try:
        candidates = tuple(directory.iterdir())
    except OSError:
        return 0
    for path in candidates:
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            total += max(0, int(info.st_size))
    return total
