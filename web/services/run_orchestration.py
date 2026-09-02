"""Long-task continuation orchestration for Web chat streams."""

from __future__ import annotations

def run_source(
    service: Any,
    *,
    request: dict[str, Any],
    active: Any,
    normalized_prompt: str,
    normalized_run_id: str,
    normalized_source: str,
    normalized_session: str,
    name: str,
    task_plan_id: str,
    cancel_event: Any,
    put: Callable[[Any], bool],
) -> None:
    import importlib
    _service = importlib.import_module("web.service")
    Any = _service.Any
    Callable = _service.Callable
    GuidanceMailbox = _service.GuidanceMailbox
    Iterator = _service.Iterator
    LONG_TASK_ACTIVE_STATUSES = _service.LONG_TASK_ACTIVE_STATUSES
    MAX_LONG_TASK_RUNS = _service.MAX_LONG_TASK_RUNS
    RunEvent = _service.RunEvent
    _WORKER_DONE = _service._WORKER_DONE
    activate_long_task = _service.activate_long_task
    continuation_request = _service.continuation_request
    copy = _service.copy
    finish_long_task = _service.finish_long_task
    get_long_task_state = _service.get_long_task_state
    is_continuable_terminal = _service.is_continuable_terminal
    long_task_event_metadata = _service.long_task_event_metadata
    record_long_task_run = _service.record_long_task_run
    session_lock = _service.session_lock
    set_long_task_current_run = _service.set_long_task_current_run
    terminal_run_stats = _service.terminal_run_stats
    threading = _service.threading
    uuid = _service.uuid
    iterator: Iterator[RunEvent] | None = None
    current_request = request
    current_run_id = normalized_run_id
    run_index = 0

    def state_snapshot() -> dict[str, Any]:
        try:
            return get_long_task_state(
                service.root, name, normalized_source, normalized_session
            )
        except Exception:
            return {
                "enabled": False,
                "status": "disabled",
                "task_id": "",
                "current_run_id": current_run_id,
            }

    def enrich_terminal(
        event: RunEvent,
        state: dict[str, Any],
        *,
        status: str | None = None,
        stop_reason: str | None = None,
    ) -> RunEvent:
        rendered = copy.copy(event)
        rendered.metadata = {
            **dict(event.metadata or {}),
            **long_task_event_metadata(
                state,
                terminal=True,
                continuation=run_index > 0,
            ),
        }
        if status:
            rendered.metadata["status"] = status
        if stop_reason:
            rendered.metadata["stop_reason"] = stop_reason
        return rendered

    def settle_abandoned_long_task(
        *,
        stop_reason: str,
        error_code: str,
        exception_type: str = "",
    ) -> tuple[dict[str, Any], bool]:
        state = state_snapshot()
        if state.get("status") not in LONG_TASK_ACTIVE_STATUSES:
            return state, False
        cancelled = cancel_event.is_set() or bool(
            state.get("cancel_requested")
        )
        error = None
        if not cancelled:
            error = {
                "code": error_code,
                "message": "长任务运行意外中断，状态已安全收敛",
            }
            if exception_type:
                error["exception_type"] = str(exception_type)[:160]
        try:
            state = finish_long_task(
                service.root,
                name,
                normalized_source,
                normalized_session,
                status="cancelled" if cancelled else "interrupted",
                stop_reason=(
                    "user_emergency_stop" if cancelled else stop_reason
                ),
                error=error,
            )
        except Exception:
            # The original failure remains authoritative.  A final
            # best-effort pass runs again before the ActiveRun is
            # removed, and the read-side orphan reconciler remains a
            # durable fallback after restart.
            pass
        return state, True

    def abandoned_terminal_event(
        state: dict[str, Any],
        *,
        cancelled: bool,
        error_code: str,
        stop_reason: str,
    ) -> RunEvent:
        metadata = {
            "status": "cancelled" if cancelled else "interrupted",
            "committed": False,
            "stop_reason": (
                "user_emergency_stop" if cancelled else stop_reason
            ),
            **long_task_event_metadata(
                state,
                terminal=True,
                continuation=run_index > 0,
            ),
        }
        if cancelled:
            metadata["cancelled"] = True
            return RunEvent(type="done", metadata=metadata)
        return RunEvent(
            type="error",
            error={
                "message": "长任务运行意外中断，已安全停止",
                "exception_type": "LongTaskInterrupted",
                "phase": "run",
                "code": error_code,
            },
            metadata=metadata,
        )

    try:
        # Keep the complete logical task under the same session lock.
        # The single-Run engine takes the same RLock re-entrantly;
        # another request cannot slip between two automatic Runs.
        with session_lock(
            service.root, name, normalized_source, normalized_session
        ):
            while run_index < MAX_LONG_TASK_RUNS:
                if cancel_event.is_set():
                    state = state_snapshot()
                    if state.get("task_id") and state.get("status") in {
                        "running",
                        "pausing",
                        "cancelling",
                    }:
                        state = finish_long_task(
                            service.root,
                            name,
                            normalized_source,
                            normalized_session,
                            status="cancelled",
                            stop_reason="user_emergency_stop",
                        )
                        put(
                            RunEvent(
                                type="done",
                                metadata={
                                    "status": "cancelled",
                                    "cancelled": True,
                                    "stop_reason": "user_emergency_stop",
                                    **long_task_event_metadata(
                                        state,
                                        terminal=True,
                                        continuation=run_index > 0,
                                    ),
                                },
                            )
                        )
                    else:
                        put(
                            RunEvent(
                                type="done",
                                metadata={
                                    "status": "cancelled",
                                    "cancelled": True,
                                    "committed": False,
                                    "stop_reason": "user_emergency_stop",
                                },
                            )
                        )
                    return
                terminal_event: RunEvent | None = None
                # Give each event source an isolated request envelope.
                # Custom transports and plugins receive a mutable dict;
                # they must not be able to rewrite the canonical
                # conversation identity used by the next continuation
                # or by history persistence.
                source_request = dict(current_request)
                source_request.update(
                    {
                        "user": name,
                        "source": normalized_source,
                        "session_id": normalized_session,
                        "run_id": current_run_id,
                    }
                )
                iterator = iter(
                    service.event_source(
                        source_request,
                        root=service.root,
                        cancel_event=cancel_event,
                    )
                )
                try:
                    for event in iterator:
                        if event.type == "done":
                            terminal_event = event
                            break
                        if event.type == "error":
                            if (
                                event.metadata.get("retryable") is True
                                and event.metadata.get("committed") is False
                            ):
                                # The core runtime will emit a
                                # retrying event next.  Do not expose a
                                # provisional error as the Web/SSE
                                # terminal or close the long task.
                                continue
                            state = state_snapshot()
                            if state.get("task_id") and state.get("status") in {
                                "running",
                                "pausing",
                                "cancelling",
                            }:
                                final_status = (
                                    "cancelled"
                                    if cancel_event.is_set()
                                    or state.get("status") == "cancelling"
                                    else "failed"
                                )
                                state = finish_long_task(
                                    service.root,
                                    name,
                                    normalized_source,
                                    normalized_session,
                                    status=final_status,
                                    stop_reason="provider_error",
                                    error=copy.deepcopy(event.error or {}),
                                )
                                event = copy.copy(event)
                                event.metadata = {
                                    **dict(event.metadata or {}),
                                    **long_task_event_metadata(
                                        state,
                                        terminal=True,
                                        continuation=run_index > 0,
                                    ),
                                }
                            if not put(event):
                                return
                            return
                        if not put(event):
                            return
                finally:
                    close = getattr(iterator, "close", None)
                    if callable(close):
                        try:
                            close()
                        except BaseException:
                            pass
                    iterator = None

                if terminal_event is None:
                    state, was_active = settle_abandoned_long_task(
                        stop_reason="missing_terminal_event",
                        error_code="LONG_TASK_MISSING_TERMINAL",
                    )
                    if was_active:
                        put(
                            abandoned_terminal_event(
                                state,
                                cancelled=cancel_event.is_set(),
                                error_code="LONG_TASK_MISSING_TERMINAL",
                                stop_reason="missing_terminal_event",
                            )
                        )
                    return

                stats = terminal_run_stats(terminal_event)
                state = state_snapshot()
                limited = is_continuable_terminal(terminal_event.metadata)
                can_continue = (
                    limited
                    and not task_plan_id
                    and not cancel_event.is_set()
                    and bool(state.get("enabled"))
                    and state.get("status") not in {"pausing", "cancelling"}
                )
                if can_continue:
                    activated = activate_long_task(
                        service.root,
                        name,
                        normalized_source,
                        normalized_session,
                        original_prompt=normalized_prompt,
                    )
                    if activated is not None:
                        state = record_long_task_run(
                            service.root,
                            name,
                            normalized_source,
                            normalized_session,
                            run_id=current_run_id,
                            elapsed_ms=stats["elapsed_ms"],
                            tool_calls=stats["tool_calls"],
                            provider_requests=stats["provider_requests"],
                            usage=stats["usage"],
                            stop_reason=stats["stop_reason"],
                            continuation=run_index > 0,
                        )
                        if int(state.get("run_count") or 0) >= MAX_LONG_TASK_RUNS:
                            state = finish_long_task(
                                service.root,
                                name,
                                normalized_source,
                                normalized_session,
                                status="paused",
                                stop_reason="long_task_max_runs",
                            )
                            if not put(
                                enrich_terminal(
                                    terminal_event,
                                    state,
                                    status="limited",
                                    stop_reason="long_task_max_runs",
                                )
                            ):
                                return
                            return

                        previous_run_id = current_run_id
                        next_run_id = f"run_{uuid.uuid4().hex}"
                        next_guidance = GuidanceMailbox(maxsize=8)
                        with service._active_runs_lock:
                            # Close and replace the mailbox under the
                            # same lock used by submit_guidance().
                            # Keeping this transition atomic prevents a
                            # hand-off window where an old Run alias is
                            # reported as accepted but its guidance is
                            # put into an already-closed queue.
                            active.guidance.close()
                            active.run_id = next_run_id
                            active.guidance = next_guidance
                            # Keep the previous alias until the
                            # persisted current_run_id points at the
                            # replacement.  State polling can therefore
                            # never observe a healthy hand-off as an
                            # orphaned Run.
                            service._active_runs[next_run_id] = active
                        state = set_long_task_current_run(
                            service.root,
                            name,
                            normalized_source,
                            normalized_session,
                            next_run_id,
                        )
                        # Keep the previous id as a guarded alias until
                        # the logical long task reaches its terminal
                        # cleanup.  App clients may still hold the
                        # original run id while the hand-off event is
                        # in flight; removing it here makes guidance
                        # and cancel spuriously target a different
                        # (or already missing) run.
                        next_request = continuation_request(
                            request,
                            run_id=next_run_id,
                            task_id=str(state.get("task_id") or ""),
                            continuation=run_index + 1,
                            original_prompt=str(
                                state.get("original_prompt") or normalized_prompt
                            ),
                        )
                        next_request["_guidance_queue"] = next_guidance
                        current_request = next_request
                        current_run_id = next_run_id
                        run_index += 1
                        update_metadata = long_task_event_metadata(
                            state,
                            terminal=False,
                            continuation=True,
                        )
                        update_metadata.update(
                            {
                                "continuation": run_index,
                                "run_id": next_run_id,
                                "next_run_id": next_run_id,
                                "previous_run_id": previous_run_id,
                            }
                        )
                        if not put(
                            RunEvent(
                                type="long_task_update",
                                content=f"长任务自动续跑 · 第 {run_index + 1} 轮",
                                metadata=update_metadata,
                            )
                        ):
                            return
                        continue

                # A task that was activated earlier must be closed on
                # every non-continuable terminal boundary.
                if state.get("task_id") and state.get("status") in {
                    "running",
                    "pausing",
                    "cancelling",
                }:
                    state = record_long_task_run(
                        service.root,
                        name,
                        normalized_source,
                        normalized_session,
                        run_id=current_run_id,
                        elapsed_ms=stats["elapsed_ms"],
                        tool_calls=stats["tool_calls"],
                        provider_requests=stats["provider_requests"],
                        usage=stats["usage"],
                        stop_reason=stats["stop_reason"],
                        continuation=run_index > 0,
                    )
                    terminal_status = str(
                        terminal_event.metadata.get("status") or "completed"
                    ).casefold()
                    final_status = (
                        "cancelled"
                        if cancel_event.is_set()
                        or state.get("status") == "cancelling"
                        or state.get("cancel_requested")
                        or terminal_status == "cancelled"
                        else "failed"
                        if terminal_status in {"failed", "error"}
                        else "paused"
                        if limited
                        else "completed"
                    )
                    state = finish_long_task(
                        service.root,
                        name,
                        normalized_source,
                        normalized_session,
                        status=final_status,
                        stop_reason=stats["stop_reason"],
                    )
                    terminal_event = enrich_terminal(
                        terminal_event,
                        state,
                        status=(
                            "limited"
                            if final_status == "paused" and limited
                            else None
                        ),
                    )
                if not put(terminal_event):
                    return
                return

            # The bounded loop is an internal safety net.  It should
            # only be reachable after a pathological endless stream.
            state = state_snapshot()
            if state.get("task_id") and state.get("status") in {
                "running",
                "pausing",
            }:
                state = finish_long_task(
                    service.root,
                    name,
                    normalized_source,
                    normalized_session,
                    status="paused",
                    stop_reason="long_task_max_runs",
                )
                put(
                    RunEvent(
                        type="done",
                        metadata={
                            "status": "limited",
                            "stop_reason": "long_task_max_runs",
                            **long_task_event_metadata(
                                state, terminal=True, continuation=True
                            ),
                        },
                    )
                )
    except BaseException as exc:
        state, was_active = settle_abandoned_long_task(
            stop_reason="engine_exception",
            error_code="LONG_TASK_ENGINE_EXCEPTION",
            exception_type=type(exc).__name__,
        )
        if was_active and isinstance(exc, Exception):
            put(
                abandoned_terminal_event(
                    state,
                    cancelled=cancel_event.is_set(),
                    error_code="LONG_TASK_ENGINE_EXCEPTION",
                    stop_reason="engine_exception",
                )
            )
        else:
            put(exc)
    finally:
        settle_abandoned_long_task(
            stop_reason="worker_exited_without_terminal",
            error_code="LONG_TASK_WORKER_EXITED",
        )
        if iterator is not None:
            close = getattr(iterator, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException:
                    pass
        with service._active_runs_lock:
            # The final close must be atomic with alias removal.  An
            # alias still visible to a control request must never
            # acknowledge guidance after the worker has terminated.
            active.guidance.close()
            for key, value in list(service._active_runs.items()):
                if value is active:
                    service._active_runs.pop(key, None)
        put(_WORKER_DONE)


