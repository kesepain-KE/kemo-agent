"""Read and partition the plain-text content exposed by Expand modules."""

from pathlib import Path
from typing import Any

from web.constants import _EXPAND_INJECTION_HEADING, _EXPAND_OPERATION_HEADING


def read_expand_text(module_dir: Path, file_name: Any) -> str:
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


def expand_control_sections(content: str) -> tuple[str, str]:
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
