"""SSE chat stream orchestration extracted from :mod:`web.service`.

The implementation keeps the WebRunService method as the compatibility entry point
and resolves module globals lazily so existing dependency injection and monkeypatches
remain effective.
"""

from __future__ import annotations

from web.services.run_orchestration import run_source as _run_source_impl

def stream_chat(
    service,
    user: Any,
    session_id: Any,
    prompt: Any,
    *,
    cancel_event: threading.Event,
    run_id: Any = "",
    content: Any = None,
    uploaded_files: Any = None,
    task_plan_id: str = "",
    task_plan_mode: str = "",
    source: Any = "web",
    client_id: Any = "",
) -> Iterator[RunEvent]:
    import importlib
    _service = importlib.import_module("web.service")
    ActiveRun = _service.ActiveRun
    Any = _service.Any
    ConflictError = _service.ConflictError
    GuidanceMailbox = _service.GuidanceMailbox
    InvalidRequestError = _service.InvalidRequestError
    Iterator = _service.Iterator
    LONG_TASK_ACTIVE_STATUSES = _service.LONG_TASK_ACTIVE_STATUSES
    MAX_LONG_TASK_RUNS = _service.MAX_LONG_TASK_RUNS
    RunEvent = _service.RunEvent
    TooManyChatsError = _service.TooManyChatsError
    _WORKER_DONE = _service._WORKER_DONE
    activate_long_task = _service.activate_long_task
    continuation_request = _service.continuation_request
    copy = _service.copy
    finish_long_task = _service.finish_long_task
    get_long_task_state = _service.get_long_task_state
    is_continuable_terminal = _service.is_continuable_terminal
    long_task_event_metadata = _service.long_task_event_metadata
    queue = _service.queue
    record_long_task_run = _service.record_long_task_run
    session_lock = _service.session_lock
    set_long_task_current_run = _service.set_long_task_current_run
    terminal_run_stats = _service.terminal_run_stats
    threading = _service.threading
    uuid = _service.uuid
    name = service.require_user(user)
    normalized_source = service.require_source(source)
    normalized_session = service.require_session_id(session_id)
    normalized_client = service.require_client_id(client_id)
    if not isinstance(prompt, str):
        raise InvalidRequestError("prompt 必须是字符串")
    normalized_prompt = prompt.strip()
    normalized_content = service.require_content(content)
    normalized_uploaded_files = service.require_uploaded_files(name, uploaded_files)
    if not normalized_prompt and not normalized_content and not normalized_uploaded_files:
        raise InvalidRequestError("prompt、content 和 uploaded_files 不能同时为空")
    normalized_run_id = (
        service.require_run_id(run_id) if run_id else f"run_{uuid.uuid4().hex}"
    )
    gate = service._get_chat_gate(name)
    if not gate.acquire(cancel_event=cancel_event):
        if cancel_event.is_set():
            raise ConflictError("聊天请求已取消")
        raise TooManyChatsError(
            "当前用户并发聊天或等待队列已满，请稍后重试",
            retry_after=gate.pending_timeout,
        )
    active = ActiveRun(
        normalized_run_id,
        name,
        normalized_session,
        source=normalized_source,
        cancel_event=cancel_event,
    )
    with service._active_runs_lock:
        if normalized_run_id in service._active_runs:
            gate.release()
            raise ConflictError(f"run_id 已在使用：{normalized_run_id}")
        service._active_runs[normalized_run_id] = active
        service._touch_session_lease_locked(
            name, normalized_source, normalized_session, normalized_client
        )
    request = {
        "user": name,
        "source": normalized_source,
        "session_id": normalized_session,
        "prompt": normalized_prompt,
        "content": normalized_content,
        "uploaded_files": normalized_uploaded_files,
        "stream": True,
        "run_id": normalized_run_id,
        "_guidance_queue": active.guidance,
        "_history_active_key": service._interactive_active_key(
            name, normalized_client, source=normalized_source
        ),
    }
    if task_plan_id:
        request["_task_plan_id"] = task_plan_id
        request["_task_plan_mode"] = task_plan_mode or "agent_managed"
    if service._router_ref is not None:
        transport_registry = getattr(service._router_ref, "transports", None)
        if transport_registry is not None:
            request["_transport_registry"] = transport_registry
            # Run 生成器拥有线程仿射 RLock。  它的 next()/close()
            # 因此，调用必须保留在一个专用工作线程上
            # 在 asyncio.to_thread 工作线程之间跳转。
    output: queue.Queue[RunEvent | BaseException | object] = queue.Queue(maxsize=32)
    consumer_closed = threading.Event()

    def put(value: RunEvent | BaseException | object) -> bool:
        if isinstance(value, RunEvent):
            # The request scope is authoritative.  Provider/tool metadata
            # is diagnostic input and must not be able to redirect a
            # streamed event into another user's conversation space.
            scoped = copy.copy(value)
            scoped.metadata = {
                **dict(value.metadata or {}),
                "source": normalized_source,
                "session_id": normalized_session,
                # Provider/tool metadata is diagnostic input as well; the
                # run object owns the identifier used by the stream,
                # including after a long-task continuation hand-off.
                "run_id": active.run_id,
            }
            value = scoped
        provisional_error = (
            isinstance(value, RunEvent)
            and value.type == "error"
            and value.metadata.get("retryable") is True
            and value.metadata.get("committed") is False
        )
        terminal_value = (
            value is _WORKER_DONE
            or isinstance(value, BaseException)
            or (
                isinstance(value, RunEvent)
                and value.type in {"done", "error"}
                and not provisional_error
            )
        )
        if terminal_value:
            # Close the current mailbox before publishing the terminal
            # value.  Otherwise a control request that races with a final
            # SSE event could be acknowledged as accepted and then be
            # silently discarded by the worker's cleanup.
            with service._active_runs_lock:
                active.guidance.close()
        while True:
            if consumer_closed.is_set():
                return False
            if cancel_event.is_set() and not terminal_value:
                return False
            try:
                output.put(value, timeout=0.1)
                return True
            except queue.Full:
                continue

    worker = threading.Thread(
        target=_run_source_impl,
        kwargs={
            "service": service,
            "request": request,
            "active": active,
            "normalized_prompt": normalized_prompt,
            "normalized_run_id": normalized_run_id,
            "normalized_source": normalized_source,
            "normalized_session": normalized_session,
            "name": name,
            "task_plan_id": task_plan_id,
            "cancel_event": cancel_event,
            "put": put,
        },
        name=f"web-run-{name}-{normalized_session}",
        daemon=True,
    )
    try:
        worker.start()
    except BaseException:
        with service._active_runs_lock:
            service._active_runs.pop(normalized_run_id, None)
        gate.release()
        raise

    def events() -> Iterator[RunEvent]:
        try:
            while True:
                value = output.get()
                if value is _WORKER_DONE:
                    return
                if isinstance(value, BaseException):
                    raise value
                if isinstance(value, RunEvent):
                    yield value
        finally:
            consumer_closed.set()
            cancel_event.set()
            worker_stopped = worker.join(timeout=5.0)
            if not worker_stopped:
                # The producer is still alive (blocked in put() or inside
                # the Run generator).  Release the user gate so another
                # conversation is not blocked by a disconnected client,
                # but keep the ActiveRun until the worker's own finally
                # block removes it.  This preserves scoped status/guidance/
                # cancel control while a long-running provider or tool is
                # still unwinding; removing it here would make the durable
                # long-task state look orphaned and make cancellation a
                # no-op.
                pass
            gate.release()

    return events()
