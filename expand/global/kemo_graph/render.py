"""Render the prompt-visible library catalog without any network access."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from errors import GraphExpandError
from registry import (
    INPUT_PATH,
    MANIFEST_PATH,
    GraphConfig,
    atomic_json,
    atomic_text,
    library_signature,
    load_config,
    unavailable_source_roots,
)


_MANIFEST_CHECKPOINT_SECONDS = 300


def _checkpoint_time() -> str:
    now = datetime.now().astimezone()
    checkpoint = int(now.timestamp()) // _MANIFEST_CHECKPOINT_SECONDS
    return datetime.fromtimestamp(
        checkpoint * _MANIFEST_CHECKPOINT_SECONDS,
        tz=now.tzinfo,
    ).strftime("%Y-%m-%d %H:%M:%S")


def _monotonic_timestamp(previous: Any, candidate: str) -> str:
    previous_text = str(previous or "").strip()
    if not previous_text:
        return candidate
    try:
        previous_value = datetime.strptime(previous_text, "%Y-%m-%d %H:%M:%S")
        candidate_value = datetime.strptime(candidate, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return candidate
    return previous_text if previous_value >= candidate_value else candidate


def _status_map() -> tuple[dict[str, dict[str, Any]], str, str]:
    from registry import STATUS_PATH

    if not STATUS_PATH.is_file():
        return {}, "", ""
    try:
        value = json.loads(STATUS_PATH.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}, "", ""
    if not isinstance(value, dict):
        return {}, "", ""
    rows = value.get("libraries")
    mapping = {
        str(item.get("id")): item
        for item in rows
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    } if isinstance(rows, list) else {}
    return (
        mapping,
        str(value.get("generated_at") or ""),
        str(value.get("base_url") or ""),
    )


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _known_document_summary(status: dict[str, Any]) -> str:
    result = status.get("result")
    sources = result.get("sources") if isinstance(result, dict) else None
    active = _nonnegative_int(sources.get("active")) if isinstance(sources, dict) else None
    total = _nonnegative_int(sources.get("total")) if isinstance(sources, dict) else None
    checked_at = str(status.get("checked_at") or "")
    timing_label = "检查于"
    if active is None or total is None:
        last_success = status.get("last_success")
        if not isinstance(last_success, dict):
            return "未检查"
        active = _nonnegative_int(last_success.get("active"))
        total = _nonnegative_int(last_success.get("total"))
        if active is None or total is None:
            return "未检查"
        failed = _nonnegative_int(last_success.get("failed")) or 0
        processing = _nonnegative_int(last_success.get("processing")) or 0
        pending = _nonnegative_int(last_success.get("pending")) or 0
        checked_at = str(last_success.get("checked_at") or "")
        timing_label = "上次成功于"
    else:
        failed = _nonnegative_int(status.get("document_failures")) or 0
        processing = _nonnegative_int(status.get("document_processing")) or 0
        pending = _nonnegative_int(status.get("document_pending")) or 0
    summary = (
        f"活动 {active} / 总计 {total}；失败 {failed}；"
        f"处理中 {processing}；待处理 {pending}"
    )
    return f"{summary}；{timing_label} {checked_at}" if checked_at else summary


def catalog_markdown(config: GraphConfig | None) -> str:
    if config is None:
        return "# Kemo Graph 外挂文档站\n\n当前未激活；不会影响本地知识库、记忆或正常对话。\n"
    status_by_id, checked_at, status_base_url = _status_map()
    if status_base_url != config.base_url:
        status_by_id, checked_at = {}, ""
    # This file is a global Prompt source shared by all users.  Never publish
    # user-restricted Library IDs or absolute paths here; callers discover
    # their authorized private libraries through the context-aware plugin.
    enabled = [
        library
        for library in config.libraries
        if library.enabled and library.public_for_all_users
    ]
    lines = [
        "# Kemo Graph 外挂文档站",
        "",
        "> 本模块只是按需查询的侧载文档站，不替换、不增强、也不缩减本地知识库或记忆。",
        "> 图谱扫描、同步和构建只在用户明确要求后执行；普通对话和 Prompt 刷新不会访问 kemo-graph。",
        "",
    ]
    if not enabled:
        lines.extend([
            "当前没有向所有用户公开的图谱库。",
            "私有库不会写入全局 Prompt；请调用 `kemo_graph libraries` 查看当前用户获准使用的库。",
        ])
        return "\n".join(lines) + "\n"
    lines.extend([
        "| Library ID | 名称 | 类型 | Store/服务位置 | 文档来源 | 文档统计（上次已知） | 上次已知状态 |",
        "|---|---|---|---|---|---|---|",
    ])
    for library in enabled[:20]:
        status = status_by_id.get(library.id, {})
        if status.get("registry_signature") != library_signature(library):
            status = {}
        known = str(status.get("status") or "未检查")
        store = library.store_root or "kemo-graph 内置库"
        unavailable = set(unavailable_source_roots(library))
        sources = (
            "<br>".join(
                f"{path}（当前不可用）" if path in unavailable else path
                for path in library.source_roots
            )
            if library.source_roots
            else "由上传接口管理"
        )
        cells = [
            library.id,
            library.display_name,
            library.kind,
            store,
            sources,
            _known_document_summary(status),
            known,
        ]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    if len(enabled) > 20:
        lines.extend(["", f"> 另有 {len(enabled) - 20} 个库未展开，请调用 `kemo_graph libraries` 查看。"])
    lines.extend([
        "",
        f"本地状态快照最近写入：{checked_at or '从未检查'}。各库统计时间见表格；该状态不是实时轮询结果。",
        "",
        "仅当用户明确要求查询此外挂文档站时，先按 Library ID 选择库，再调用 "
        "`expand_call(scope=\"global\", module=\"kemo_graph\", command=\"query\")`。",
        "同一轮默认合并为一次查询；仅“继续、下一步、重来”等指令不应触发新查询。",
    ])
    return "\n".join(lines) + "\n"


def _update_manifest(*, active: bool, healthy: bool) -> None:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        manifest = {}
    previous_update = manifest.get("recent_update")
    checkpoint = _checkpoint_time()
    manifest.update({
        "name": "Kemo Graph 外挂文档站",
        "explain": "注册外部或内置知识图谱文档库，仅在用户明确要求时查询、同步或构建",
        "open_input": active,
        "input_data": "input_data.md",
        "input_health": "正常" if healthy else "异常",
        "start_update": "data_update.py",
        "open_control": True,
        "start_expand": "start_expand.py",
        "start_control": "expand_control.md",
        "recent_update": _monotonic_timestamp(previous_update, checkpoint),
    })
    atomic_json(MANIFEST_PATH, manifest)


def refresh_catalog() -> dict[str, Any]:
    try:
        config = load_config()
        atomic_text(INPUT_PATH, catalog_markdown(config))
        active = bool(config and any(library.enabled for library in config.libraries))
        _update_manifest(active=active, healthy=True)
        return {
            "ok": True,
            "status": "active" if active else "inactive",
            "active": active,
            "libraries": len(config.libraries) if config else 0,
            "resources": [{"path": "input_data.md", "kind": "markdown"}] if active else [],
        }
    except GraphExpandError as exc:
        atomic_text(INPUT_PATH, f"# Kemo Graph 外挂文档站\n\n配置异常：{exc}\n")
        _update_manifest(active=False, healthy=False)
        return {"ok": False, "status": "error", "active": False, "error": str(exc), "resources": []}
