"""Shared Web UI config validation kept out of the settings composition root."""

from pathlib import Path
from typing import Any

from run.config import _contains_reparse_point, web_background, web_language
from web.errors import InvalidRequestError


def validate_web_ui_patch(updated: dict[str, Any], changes: dict[str, Any], path: Path, root: Path) -> None:
    if "web_ui" in changes and _contains_reparse_point(path, root):
        raise InvalidRequestError("网页配置不允许符号链接或目录联接")
    language_changes = changes.get("web_ui")
    explicit_language = isinstance(language_changes, dict) and "language" in language_changes
    try:
        web_language(updated, strict=True, allow_retired=not explicit_language)
        web_background(updated, strict=True)
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from None
