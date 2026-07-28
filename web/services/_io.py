"""Web 领域服务共享的原子文件与文本边界工具。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import uuid

from web.constants import TEXT_DOCUMENT_MAX_CHARS
from web.errors import InvalidRequestError


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def validated_text(
    value: Any,
    *,
    field: str = "content",
    max_chars: int = TEXT_DOCUMENT_MAX_CHARS,
) -> str:
    if not isinstance(value, str):
        raise InvalidRequestError(f"{field} 必须是字符串")
    if len(value) > max_chars:
        raise InvalidRequestError(f"{field} 超过最大长度 {max_chars}")
    return value
