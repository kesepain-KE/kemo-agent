"""Paths owned by one registered module, including nested UI resources."""

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


def module_path(root: Path, relative: Any, *, file: bool = True) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("模块路径必须是非空的相对 POSIX 路径")
    parts = relative.split("/")
    if (
        PurePosixPath(relative).is_absolute()
        or PureWindowsPath(relative).drive
        or any(part in {"", ".", ".."} or ":" in part or "\x00" in part for part in parts)
    ):
        raise ValueError("模块路径不能包含绝对路径或路径穿越")
    current = root
    for part in parts:
        current /= part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("模块路径不能经过符号链接或目录联接")
    current.resolve().relative_to(root.resolve())
    if file and not current.is_file():
        raise ValueError(f"模块文件不存在：{relative}")
    return current
