"""Kemo Bridge terminal SSE classification and public failure payloads."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Iterator


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})

@dataclass(frozen=True)
class TerminalStatus:
    status: str
    message: str = ""
    code: str | None = None
    transient: bool = False
    phase: str = ""

    def __iter__(self) -> Iterator[str]:
        # tests/expand/test_kemo_bridge_expand.py still unpacks terminal results.
        return iter((self.status, self.message))


_FAILURES = {
    "upstream_auth_missing": ("上游已启用认证，但桥接未配置凭据。", False),
    "upstream_auth_rejected": ("上游拒绝了桥接的认证凭据。", False),
    "upstream_unreachable": ("桥接无法连接上游服务。", True),
    "upstream_timeout": ("等待上游响应超时。", True),
    "upstream_http_error": ("上游服务未能完成请求。", False),
    "upstream_invalid_payload": ("上游响应格式无效。", False),
    "upstream_stream_incomplete": ("上游对话流在完成前中断。", True),
    "bridge_internal": ("桥接对话处理失败。", False),
}
_FAILURE_PHASES = {"auth", "connect", "request", "stream", "bridge"}



def _failure(code: str, phase: str, *, http_status: int | None = None) -> TerminalStatus:
    message, transient = _FAILURES[code]
    if code == "upstream_http_error" and http_status is not None:
        transient = http_status in {408, 425, 429} or 500 <= http_status < 600
    return TerminalStatus("failed", message, code, transient, phase)


def _event_parts(value: dict) -> tuple[dict, dict, str]:
    nested = value["data"] if isinstance(value.get("data"), dict) else value
    metadata = nested["metadata"] if isinstance(nested.get("metadata"), dict) else {}
    kind = str(value.get("type") or value.get("event") or nested.get("type") or nested.get("event") or "")
    return nested, metadata, kind


def _terminal_data(outcome: TerminalStatus, original: dict | None = None) -> str:
    value: dict[str, Any] = {"type": "error"}
    if original is not None:
        _, metadata, kind = _event_parts(original)
        value = {"type": kind, "metadata": {
            "status": outcome.status,
            **{key: metadata[key] for key in ("committed", "cancelled", "terminal_committed")
               if isinstance(metadata.get(key), bool)},
        }}
    if outcome.code is not None:
        value["error"] = {"message": outcome.message, "code": outcome.code,
                          "transient": outcome.transient, "phase": outcome.phase}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _terminal_status(data: str | dict, *, upstream: bool = False) -> TerminalStatus | None:
    if isinstance(data, str) and data.strip() == "[DONE]":
        return TerminalStatus("completed")
    try:
        value = json.loads(data) if isinstance(data, str) else data
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    nested, metadata, event_type = _event_parts(value)
    if event_type in {"done", "completed"}:
        raw_status = metadata.get("status")
        if raw_status is None:
            raw_status = nested.get("status")
        if raw_status is None and nested is not value:
            raw_status = value.get("status")
        status = str(raw_status or "").strip().casefold()
        if status in {"cancelled", "canceled", "cancelling"}:
            return TerminalStatus("cancelled", phase="stream")
        if status in {"interrupted", "aborted", "stopped", "limited", "paused"}:
            outcome = replace(_failure("upstream_stream_incomplete", "stream"), status="interrupted")
        elif status in {"", "completed", "success", "succeeded", "done"}:
            return TerminalStatus("completed")
        else:
            # Unknown terminal states must not look like a successful completion.
            outcome = _failure("bridge_internal", "stream")
    elif event_type == "error":
        metadata = nested.get("metadata")
        # 生产上游 kemo-agent 在 error 事件 metadata 中恒写 retryable=False
        #（run/conversation/main_orchestration.py:865、run/conversation/runtime.py:39、
        #  web/services/run_orchestration.py:55），故本分支在当前生产链路不可达。
        # 但 tests/test_routes.py:145 的 fixture 会送 retryable=True，用它锁定
        # provisional 语义；若将来上游接通该通道，本分支自动生效。不要删除。
        if isinstance(metadata, dict) and metadata.get("retryable") is True and metadata.get("committed") is False:
            return None
        outcome = _failure("bridge_internal", "stream")
    else:
        return None
    if not upstream and isinstance(error := nested.get("error"), dict):
        code, phase = error.get("code"), error.get("phase")
        if (isinstance(code, str) and code in _FAILURES and isinstance(phase, str) and phase in _FAILURE_PHASES
                and isinstance(error.get("message"), str) and isinstance(error.get("transient"), bool)):
            return TerminalStatus(outcome.status, error["message"], code, error["transient"], phase)
    return outcome
