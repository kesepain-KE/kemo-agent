"""Dependency-free loader for the mirrored Kemo 1.0 wire fixture."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
EXPECTED_FIXTURE_SET = "kemo-1.0-wire-2026-09-13"
EXPECTED_WIRE_SHA256 = "1c250d8b777547fa942b658e919672a736821f6580f8df2688f5876d6c15c96f"
TERMINAL_EVENTS = frozenset({
    "response.completed",
    "response.incomplete",
    "response.failed",
    "response.cancelled",
    "error",
})


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Kemo Fixture 根节点必须是对象：{path.name}")
    return value


def load_bundle(root: Path = FIXTURE_ROOT) -> dict[str, Any]:
    """Load the fixture only after its identity, digest and counts agree."""

    manifest = _json(root / "manifest.json")
    wire_path = root / "wire.json"
    payload = wire_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if manifest.get("fixture_set") != EXPECTED_FIXTURE_SET:
        raise ValueError("Kemo Fixture 集标识不匹配")
    if manifest.get("protocol_version") != "1.0":
        raise ValueError("Kemo Fixture 协议版本必须是 1.0")
    if digest != EXPECTED_WIRE_SHA256:
        raise ValueError("Kemo Fixture 正文偏离已审核基线；必须同步更新两端固定摘要")
    if manifest.get("wire_sha256") != digest:
        raise ValueError("Kemo Fixture 摘要不匹配；禁止只改一侧或只改清单")
    bundle = json.loads(payload.decode("utf-8"))
    if not isinstance(bundle, dict):
        raise ValueError("Kemo wire.json 根节点必须是对象")
    if bundle.get("fixture_set") != manifest["fixture_set"]:
        raise ValueError("Kemo Fixture 清单与正文标识不一致")
    if bundle.get("protocol_version") != manifest["protocol_version"]:
        raise ValueError("Kemo Fixture 清单与正文协议版本不一致")
    counts = {
        "valid_cases": len(bundle.get("valid_cases", [])),
        "invalid_cases": len(bundle.get("invalid_cases", [])),
        "valid_streams": len(bundle.get("valid_streams", [])),
        "invalid_streams": len(bundle.get("invalid_streams", [])),
    }
    if counts != manifest.get("counts"):
        raise ValueError("Kemo Fixture 清单计数与正文不一致")
    ids = [
        str(case.get("id"))
        for group in counts
        for case in bundle.get(group, [])
        if isinstance(case, dict)
    ]
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        raise ValueError("Kemo Fixture 用例 ID 必须非空且全局唯一")
    return bundle


def _target(document: Any, path: list[Any]) -> tuple[Any, Any]:
    if not path:
        raise ValueError("Fixture mutation.path 不能为空")
    parent = document
    for part in path[:-1]:
        if isinstance(parent, list) and isinstance(part, int):
            parent = parent[part]
        elif isinstance(parent, dict) and isinstance(part, str):
            parent = parent[part]
        else:
            raise ValueError(f"Fixture mutation.path 无效：{path!r}")
    return parent, path[-1]


def _mutate(document: Any, mutations: list[dict[str, Any]]) -> Any:
    result = copy.deepcopy(document)
    for mutation in mutations:
        operation = mutation.get("op")
        path = mutation.get("path")
        if not isinstance(path, list):
            raise ValueError("Fixture mutation.path 必须是数组")
        parent, key = _target(result, path)
        if operation == "set":
            if isinstance(parent, list) and isinstance(key, int):
                parent[key] = copy.deepcopy(mutation.get("value"))
            elif isinstance(parent, dict) and isinstance(key, str):
                parent[key] = copy.deepcopy(mutation.get("value"))
            else:
                raise ValueError(f"Fixture set 目标无效：{path!r}")
        elif operation == "delete":
            if isinstance(parent, list) and isinstance(key, int):
                del parent[key]
            elif isinstance(parent, dict) and isinstance(key, str):
                del parent[key]
            else:
                raise ValueError(f"Fixture delete 目标无效：{path!r}")
        else:
            raise ValueError(f"未知 Fixture mutation 操作：{operation!r}")
    return result


def materialize(bundle: dict[str, Any], reference: str | dict[str, Any]) -> dict[str, Any]:
    """Return an isolated document, optionally applying declarative mutations."""

    if isinstance(reference, str):
        name = reference
        mutations: list[dict[str, Any]] = []
    elif isinstance(reference, dict):
        name = str(reference.get("document") or "")
        raw_mutations = reference.get("mutations", [])
        if not isinstance(raw_mutations, list):
            raise ValueError("Fixture mutations 必须是数组")
        mutations = raw_mutations
    else:
        raise ValueError("Fixture document 引用必须是字符串或对象")
    documents = bundle.get("documents")
    if not isinstance(documents, dict) or name not in documents:
        raise ValueError(f"Fixture document 不存在：{name}")
    value = _mutate(documents[name], mutations)
    if not isinstance(value, dict):
        raise ValueError(f"Fixture document 必须是对象：{name}")
    return value


def stream_events(bundle: dict[str, Any], stream: dict[str, Any]) -> list[dict[str, Any]]:
    references = stream.get("events")
    if not isinstance(references, list):
        raise ValueError("Fixture stream.events 必须是数组")
    return [materialize(bundle, reference) for reference in references]


def validate_stream_envelope(events: list[dict[str, Any]]) -> list[int]:
    """Validate the transport invariants shared by producer and consumer."""

    seen: set[str] = set()
    accepted: list[int] = []
    request_id: str | None = None
    response_id: str | None = None
    terminal = False
    for event in events:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            raise ValueError("SSE event_id 不能为空")
        if event_id in seen:
            continue
        if terminal:
            raise ValueError("SSE 终态后不能继续接收新事件")
        current_request = str(event.get("request_id") or "")
        current_response = str(event.get("response_id") or "")
        if request_id is None:
            request_id, response_id = current_request, current_response
        elif current_request != request_id or current_response != response_id:
            raise ValueError("同一 SSE 流的 request_id/response_id 必须稳定")
        sequence = event.get("sequence")
        if type(sequence) is not int or sequence != len(accepted):
            raise ValueError("SSE sequence 必须从 0 开始连续递增")
        seen.add(event_id)
        accepted.append(sequence)
        terminal = str(event.get("type")) in TERMINAL_EVENTS
    if not accepted or not terminal:
        raise ValueError("SSE Fixture 必须包含统一终态")
    return accepted
