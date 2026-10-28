"""Persist completed large outputs without repeating side-effecting tools."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
import uuid

from run.config import _contains_reparse_point, user_dir


def save_result_artifact(rendered: str, context: dict[str, Any]) -> dict[str, Any]:
    root = Path(str(context["root"])).resolve()
    user = str(context["user"])
    directory = user_dir(user, root) / "download" / "tool_results"
    if _contains_reparse_point(directory, root):
        raise ValueError("工具结果目录不能通过链接访问")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"result_{uuid.uuid4().hex}.json"
    data = rendered.encode("utf-8")
    with path.open("xb") as handle:
        handle.write(data)
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
