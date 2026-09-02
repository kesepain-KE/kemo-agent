"""Request parsing and conversation identity setup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from run.conversation.run_state import RunIdentity
from run.infra import EngineError


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Validated request values used by all subsequent runtime stages."""

    base: Path
    user: str
    source: str
    session_id: str
    run_id: str
    prompt: str
    content_blocks: list[Any]
    compress_only: bool
    identity: RunIdentity


def build_request_context(
    request: dict[str, Any],
    *,
    root: Path | None,
    project_root_fn: Callable[[], Path],
    required_text_fn: Callable[[dict[str, Any], str], str],
    request_content_blocks_fn: Callable[[dict[str, Any]], list[Any]],
    content_display_fn: Callable[[list[Any]], str],
) -> RequestContext:
    """Validate the request and construct its stable conversation identity."""

    compress_only = bool(request.get("compress_only", False))
    user = required_text_fn(request, "user")
    content_blocks = (
        [] if compress_only else request_content_blocks_fn(request)
    )
    has_uploaded_files = bool(request.get("uploaded_files"))
    if not content_blocks and not has_uploaded_files and not compress_only:
        raise EngineError("请求必须包含非空 prompt、content[] 或 uploaded_files")
    source = required_text_fn(request, "source")
    session_id = required_text_fn(request, "session_id")
    run_id = str(request.get("run_id") or "")
    base = (root or project_root_fn()).resolve()
    return RequestContext(
        base=base,
        user=user,
        source=source,
        session_id=session_id,
        run_id=run_id,
        prompt=content_display_fn(content_blocks),
        content_blocks=content_blocks,
        compress_only=compress_only,
        identity=RunIdentity(
            root=base,
            user=user,
            source=source,
            session_id=session_id,
            run_id=run_id,
        ),
    )

