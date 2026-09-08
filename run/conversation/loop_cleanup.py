"""Terminal failure projection and best-effort conversation run cleanup."""

from __future__ import annotations

from typing import Any, Callable, Iterator


def terminal_failure_events(
    exc: BaseException,
    *,
    terminal_committer: Any,
    round_state: Any,
    cancel_event: Any,
    request: dict[str, Any],
    context_length_error_type: type[BaseException],
    failure_requires_immediate_commit: Callable[[BaseException], bool],
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
    defer_failure_commit = bool(request.get("_defer_failure_commit", False))
    context_limit = isinstance(exc, context_length_error_type)
    terminal_event = terminal_committer.commit_failed_round(
        exc,
        reason=("provider_context_recovery_failed" if context_limit else "runtime_exception"),
        persist=(
            not defer_failure_commit
            or failure_requires_immediate_commit(exc)
        ),
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
            pass
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
                if request.get("_defer_failure_commit") and not round_state.finalized
                else "idle"
            ),
            run_id=run_id or None,
        )
    except Exception:
        pass
