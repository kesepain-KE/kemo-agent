"""Pending-memory recovery orchestration extracted from maintenance scheduler."""

from __future__ import annotations

def recover_pending_memory(scheduler, user: str) -> dict[str, Any]:
    import importlib
    _maintenance = importlib.import_module("run.scheduler.maintenance")
    AgentRunner = _maintenance.AgentRunner
    Any = _maintenance.Any
    MEMORY_RECOVERY_ROUNDS_PER_SCAN = _maintenance.MEMORY_RECOVERY_ROUNDS_PER_SCAN
    MaintenanceError = _maintenance.MaintenanceError
    Path = _maintenance.Path
    analyze_memory_batch_resilient = _maintenance.analyze_memory_batch_resilient
    analyze_round_memory = _maintenance.analyze_round_memory
    claim_pending_memory = _maintenance.claim_pending_memory
    find_record = _maintenance.find_record
    finish_memory_claim = _maintenance.finish_memory_claim
    history_directory = _maintenance.history_directory
    load_config = _maintenance.load_config
    load_window = _maintenance.load_window
    memory_batch_operation_id = _maintenance.memory_batch_operation_id
    memory_extraction_batch_rounds = _maintenance.memory_extraction_batch_rounds
    memory_extraction_mode = _maintenance.memory_extraction_mode
    memory_round_data = _maintenance.memory_round_data
    memory_round_payload = _maintenance.memory_round_payload
    patch_archive_metadata = _maintenance.patch_archive_metadata
    persist_round_memory_analysis = _maintenance.persist_round_memory_analysis
    session_lock = _maintenance.session_lock
    config = load_config(user, scheduler.root)
    extraction_mode = memory_extraction_mode(config)
    if extraction_mode == "disabled":
        return {
            "mode": extraction_mode,
            "claimed": 0,
            "processed": [],
            "failed": [],
        }
    claimable_statuses = {"failed", "processing", "queued"}
    remaining_status = "deferred"
    if extraction_mode in {"background", "on_commit"}:
        claimable_statuses.add("pending")
        remaining_status = "pending"
    raw_limit = (config.get("memory") or {}).get(
        "recovery_max_rounds_per_scan", MEMORY_RECOVERY_ROUNDS_PER_SCAN
    )
    try:
        limit = max(1, min(20, int(raw_limit)))
    except (TypeError, ValueError):
        limit = MEMORY_RECOVERY_ROUNDS_PER_SCAN
    batch_size = memory_extraction_batch_rounds(config)
    runner: AgentRunner | None = None
    processed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    claimed = 0
    batches = 0

    while claimed < limit:
        if scheduler._stop_event.is_set():
            break
        claim = claim_pending_memory(
            scheduler.root,
            user,
            statuses=claimable_statuses,
            max_rounds=min(batch_size, limit - claimed),
        )
        if claim is None:
            break
        source = str(claim.get("source") or "")
        session_id = str(claim.get("session_id") or "")
        archive_name = str(claim.get("archive_window") or "")
        claim_id = str(claim.get("memory_claim_id") or "")
        round_start = int(
            claim.get("memory_claim_start_round")
            or claim.get("memory_claim_round")
            or 0
        )
        round_end = int(
            claim.get("memory_claim_end_round")
            or claim.get("memory_claim_round")
            or round_start
        )
        claimed_count = max(1, round_end - round_start + 1)
        claimed += claimed_count
        batches += 1
        target_round = max(0, int(claim.get("memory_target_round") or 0))
        claim_remaining_status = (
            "queued"
            if target_round > 0
            or (
                extraction_mode == "compression_only"
                and str(claim.get("lifecycle") or "") == "closed"
            )
            else remaining_status
        )
        archive_path = history_directory(scheduler.root, user) / archive_name
        identity = {
            "source": source,
            "session_id": session_id,
            "round": round_start,
            "round_start": round_start,
            "round_end": round_end,
        }
        extraction: dict[str, Any] | None = None
        extraction_error: dict[str, Any] | None = None
        archive_committed = False
        stale_result = False
        analysis: dict[str, Any] | None = None
        batch_rounds: list[dict[str, Any]] = []
        skipped_rounds: list[int] = []
        try:
            if (
                not source
                or not session_id
                or not claim_id
                or round_start < 1
                or round_end < round_start
                or not archive_name
                or Path(archive_name).name != archive_name
            ):
                raise MaintenanceError("记忆恢复领取记录缺少有效会话身份")
            with session_lock(scheduler.root, user, source, session_id):
                window = load_window(archive_path)
                data = window.get("data") or {}
                if (
                    data.get("source") != source
                    or data.get("session_id") != session_id
                ):
                    raise MaintenanceError("记忆恢复领取记录与归档身份不一致")
                archive_rounds = max(0, int(data.get("rounds") or 0))
                archive_cursor = max(
                    0, int(data.get("memory_processed_round") or 0)
                )
                if round_end > archive_rounds:
                    raise MaintenanceError(
                        f"待提取轮次 {round_start}-{round_end} 超过归档轮数 {archive_rounds}"
                    )
                if archive_cursor >= round_end:
                    extraction = {
                        "status": "already_processed",
                        "candidate_count": 0,
                        "round_start": round_start,
                        "round_end": round_end,
                    }
                else:
                    if archive_cursor != round_start - 1:
                        raise MaintenanceError(
                            f"记忆提取游标已变化：{archive_cursor} != {round_start - 1}"
                        )
                    cancelled_rounds = {
                        int(item.get("round") or 0)
                        for item in data.get("round_metrics", [])
                        if isinstance(item, dict)
                        and item.get("status") == "cancelled"
                    }
                    for round_number in range(round_start, round_end + 1):
                        if round_number in cancelled_rounds:
                            skipped_rounds.append(round_number)
                            continue
                        batch_rounds.append(
                            memory_round_data(
                                round_number=round_number,
                                **memory_round_payload(window, round_number),
                            )
                        )
            if batch_rounds:
                if runner is None:
                    runner = AgentRunner(
                        scheduler.root,
                        user,
                        config=config,
                        provider_factory=scheduler.provider_factory,
                    )
                if len(batch_rounds) == 1:
                    only_round = batch_rounds[0]
                    messages = only_round.get("messages") or []
                    analysis = analyze_round_memory(
                        round_number=int(only_round["round"]),
                        prompt=str((messages[0] or {}).get("content") or ""),
                        text=str((messages[1] or {}).get("content") or ""),
                        reasoning=str(
                            ((only_round.get("think") or {}).get("content") or "")
                        ),
                        tool_records=list(only_round.get("tools") or []),
                        agent_runner=runner,
                        cancel_event=scheduler._stop_event,
                        agent_source=source,
                        session_id=session_id,
                    )
                else:
                    analysis = analyze_memory_batch_resilient(
                        rounds=batch_rounds,
                        agent_runner=runner,
                        cancel_event=scheduler._stop_event,
                        source={
                            "source": "round_commit",
                            "channel": source,
                            "session_id": session_id,
                            "round_start": round_start,
                            "round_end": round_end,
                        },
                        agent_source=source,
                        session_id=session_id,
                    )
                if analysis.get("status") != "completed":
                    raw_error = analysis.get("error")
                    extraction_error = (
                        dict(raw_error)
                        if isinstance(raw_error, dict)
                        else {
                            "message": "记忆提取失败",
                            "exception_type": "MaintenanceError",
                        }
                    )
                    raise MaintenanceError(
                        str(extraction_error.get("message") or "记忆提取失败")
                    )
            elif extraction is None:
                extraction = {
                    "status": "skipped",
                    "candidate_count": 0,
                    "reason": "cancelled_rounds",
                    "round_start": round_start,
                    "round_end": round_end,
                    "rounds": [],
                    "skipped_rounds": skipped_rounds,
                }

            with session_lock(scheduler.root, user, source, session_id):
                window = load_window(archive_path)
                data = window.setdefault("data", {})
                archive_cursor = max(
                    0, int(data.get("memory_processed_round") or 0)
                )
                current_claim = find_record(
                    scheduler.root,
                    user,
                    source,
                    session_id,
                )
                if (
                    not isinstance(current_claim, dict)
                    or current_claim.get("memory_claim_id") != claim_id
                    or int(
                        current_claim.get("memory_claim_start_round")
                        or current_claim.get("memory_claim_round")
                        or 0
                    )
                    != round_start
                    or int(
                        current_claim.get("memory_claim_end_round")
                        or current_claim.get("memory_claim_round")
                        or 0
                    )
                    != round_end
                ):
                    stale_result = True
                    raise MaintenanceError("记忆提取 claim 已失效，丢弃陈旧结果")
                if archive_cursor >= round_end:
                    extraction = {
                        "status": "already_processed",
                        "candidate_count": 0,
                        "round_start": round_start,
                        "round_end": round_end,
                    }
                elif archive_cursor != round_start - 1:
                    raise MaintenanceError(
                        f"记忆提取游标已变化：{archive_cursor} != {round_start - 1}"
                    )
                elif analysis is not None:
                    extraction = persist_round_memory_analysis(
                        root=scheduler.root,
                        user=user,
                        config=config,
                        analysis=analysis,
                        operation_id=memory_batch_operation_id(
                            user,
                            source,
                            session_id,
                            round_start,
                            round_end,
                            batch_rounds,
                        ),
                    )
                    extraction["skipped_rounds"] = skipped_rounds
                    if extraction.get("status") != "completed":
                        raw_error = extraction.get("error")
                        extraction_error = (
                            dict(raw_error)
                            if isinstance(raw_error, dict)
                            else {
                                "message": "记忆持久化失败",
                                "exception_type": "MaintenanceError",
                            }
                        )
                        raise MaintenanceError(
                            str(extraction_error.get("message") or "记忆持久化失败")
                        )
                finished = finish_memory_claim(
                    scheduler.root,
                    user,
                    source,
                    session_id,
                    claim_id=claim_id,
                    processed_round=round_end,
                    remaining_status=claim_remaining_status,
                )
                if finished is None:
                    raise MaintenanceError("记忆提取 claim 在提交时已失效")
                data["memory_processed_round"] = int(
                    finished.get("memory_processed_round") or round_end
                )
                data["memory_status"] = str(
                    finished.get("memory_status") or claim_remaining_status
                )
                data.pop("memory_error", None)
                if isinstance(finished.get("memory_last_error"), dict):
                    data["memory_last_error"] = dict(finished["memory_last_error"])
                for field in (
                    "memory_queue_reason",
                    "memory_target_round",
                    "memory_queued_at",
                ):
                    if finished.get(field) not in {None, "", 0}:
                        data[field] = finished[field]
                    else:
                        data.pop(field, None)
                updates = {
                    "memory_processed_round": data["memory_processed_round"],
                    "memory_status": data["memory_status"],
                }
                removals = ["memory_error"]
                if "memory_last_error" in data:
                    updates["memory_last_error"] = data["memory_last_error"]
                for field in (
                    "memory_queue_reason",
                    "memory_target_round",
                    "memory_queued_at",
                ):
                    if field in data:
                        updates[field] = data[field]
                    else:
                        removals.append(field)
                patch_archive_metadata(
                    archive_path,
                    window,
                    updates=updates,
                    removals=tuple(removals),
                )
                archive_committed = True
            processed.append(
                {
                    **identity,
                    "status": str((extraction or {}).get("status") or "completed"),
                    "candidate_count": int(
                        (extraction or {}).get("candidate_count") or 0
                    ),
                    "claim_applied": finished is not None,
                    "archive_committed": archive_committed,
                }
            )
        except Exception as exc:
            error = extraction_error or {
                "message": str(exc),
                "exception_type": type(exc).__name__,
            }
            if stale_result:
                failed.append({**identity, "stale": True, "error": error})
                continue
            try:
                finished = finish_memory_claim(
                    scheduler.root,
                    user,
                    source,
                    session_id,
                    claim_id=claim_id,
                    error=error,
                )
            except Exception as index_exc:
                error["index_error"] = {
                    "message": str(index_exc),
                    "exception_type": type(index_exc).__name__,
                }
                finished = None
            try:
                with session_lock(scheduler.root, user, source, session_id):
                    window = load_window(archive_path)
                    data = window.setdefault("data", {})
                    diagnostic = (
                        finished.get("memory_last_error")
                        if isinstance(finished, dict)
                        and isinstance(finished.get("memory_last_error"), dict)
                        else error
                    )
                    data["memory_status"] = "failed"
                    data["memory_error"] = dict(diagnostic)
                    data["memory_last_error"] = dict(diagnostic)
                    patch_archive_metadata(
                        archive_path,
                        window,
                        updates={
                            "memory_status": "failed",
                            "memory_error": dict(diagnostic),
                            "memory_last_error": dict(diagnostic),
                        },
                    )
            except Exception as archive_exc:
                error["archive_error"] = {
                    "message": str(archive_exc),
                    "exception_type": type(archive_exc).__name__,
                }
            failed.append({**identity, "error": error})
            scheduler._report_error(f"maintenance:{user}:memory", exc)
    return {
        "mode": extraction_mode,
        "claimed": claimed,
        "batches": batches,
        "processed": processed,
        "failed": failed,
    }

