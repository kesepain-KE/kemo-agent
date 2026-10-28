"""Terminal failure projection and best-effort conversation run cleanup."""

from __future__ import annotations

from typing import Any, Callable, Iterator
import logging
import json
from pathlib import Path
import uuid

_LOG = logging.getLogger(__name__)


def _cleanup_failure(request: dict[str, Any], marker: str, *, root: Any, user: str) -> None:
    request.setdefault("_cleanup_diagnostics", {})[marker] = True
    _LOG.exception("Conversation cleanup failed: %s", marker)
    try:
        from run.config import _contains_reparse_point, user_dir
        base = Path(root).resolve()
        directory = user_dir(user, base) / "history" / "run_failures"
        if _contains_reparse_point(directory, base):
            raise ValueError("诊断目录不能是链接")
        directory.mkdir(parents=True, exist_ok=True)
        # Keep an independent diagnostic even when the history transaction
        # itself is unavailable. Never persist exception bodies or prompts.
        with (directory / f"{uuid.uuid4().hex}.json").open("x", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, marker: True,
                       "run_id": str(request.get("run_id") or "")[:160]}, handle)
    except Exception:
        _LOG.error("Unable to persist cleanup diagnostic marker: %s", marker)


def terminal_failure_events(
    exc: BaseException,
    *,
    terminal_committer: Any,
    round_state: Any,
    cancel_event: Any,
    request: dict[str, Any],
    context_length_error_type: type[BaseException],
    committed_failure_event: Callable[[Any, Any], Any],
    error_event: Callable[..., Any],
) -> Iterator[Any]:
    """Build the one terminal event for an exception inside a locked run."""

    if terminal_committer is None or round_state.finalized:
        yield error_event(exc, phase="run")
        return
    if cancel_event is not None and cancel_event.is_set():
        yield terminal_committer.commit_cancelled_round()
        return
    context_limit = isinstance(exc, context_length_error_type)
    terminal_event = terminal_committer.commit_failed_round(
        exc,
        reason=("provider_context_recovery_failed" if context_limit else "runtime_exception"),
    )
    yield committed_failure_event(
        error_event(exc, phase="provider" if context_limit else "run"),
        terminal_event,
    )


def cleanup_run_registration(
    *,
    terminal_committer: Any,
    round_state: Any,
    cancel_event: Any,
    request: dict[str, Any],
    update_run_state: Callable[..., Any],
    base: Any,
    user: str,
    source: str,
    session_id: str,
    run_id: str,
) -> None:
    """Finish cancellation and registry state without masking the run result."""

    if (
        cancel_event is not None
        and cancel_event.is_set()
        and not round_state.finalized
        and terminal_committer is not None
    ):
        try:
            terminal_committer.commit_cancelled_round()
        except Exception:
            _cleanup_failure(request, "cancel_commit_failed", root=base, user=user)
    if not round_state.history_run_registered:
        return
    try:
        update_run_state(
            base,
            user,
            source,
            session_id,
            run_state=(
                "running"
                if round_state.retry_follows and not round_state.finalized
                else "idle"
            ),
            run_id=run_id or None,
        )
    except Exception:
        _cleanup_failure(request, "run_state_update_failed", root=base, user=user)
