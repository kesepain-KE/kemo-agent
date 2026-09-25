"""Structured failure classification for Expand module invocations."""

from __future__ import annotations

import re
from typing import Any

_ERROR_CATEGORY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,159}$")
_RETRY_SAFE_OPERATIONS = frozenset({("global", "kemo_graph", "query"), ("global", "kemo_graph", "status")})

class ExpandRuntimeError(RuntimeError):
    pass


class ExpandOperationError(ExpandRuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: str = "expand_operation_error",
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        normalized_category = str(category or "").strip()
        self.category = (
            normalized_category
            if _ERROR_CATEGORY_RE.fullmatch(normalized_category)
            else "expand_operation_error"
        )
        self.status_code = _bounded_error_int(
            status_code,
            minimum=100,
            maximum=599,
        )
        self.retryable_declared = True
        self.retryable = bool(retryable)
        self.retry_after_ms = _bounded_error_int(
            retry_after_ms,
            minimum=0,
            maximum=120_000,
        )


def _bounded_error_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed < minimum:
        return None
    return min(parsed, maximum)


def _operation_item_failed(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().casefold()
    if item.get("ok") is False or status in {
        "error",
        "failed",
        "failure",
        "source_missing",
        "source_unavailable",
        "degraded",
    }:
        return True
    if status != "partial":
        return False
    failed = item.get("failed")
    return (
        isinstance(failed, int)
        and not isinstance(failed, bool)
        and failed > 0
    ) or bool(item.get("error"))


def _metadata_from_sources(sources: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    status_codes: list[int] = []
    retry_declarations: list[Any] = []
    for source in sources:
        if "category" not in result:
            category = str(source.get("category") or source.get("type") or "").strip()
            if _ERROR_CATEGORY_RE.fullmatch(category):
                result["category"] = category
        for key in ("status_code", "provider_status"):
            status_code = _bounded_error_int(
                source.get(key),
                minimum=100,
                maximum=599,
            )
            if status_code is not None:
                status_codes.append(status_code)
                break
        if "retryable" in source:
            retry_declarations.append(source.get("retryable"))
        if "retry_after_ms" not in result:
            retry_after_ms = _bounded_error_int(
                source.get("retry_after_ms"),
                minimum=0,
                maximum=120_000,
            )
            if retry_after_ms is not None:
                result["retry_after_ms"] = retry_after_ms
    deterministic_status = next(
        (
            status
            for status in status_codes
            if 400 <= status < 500 and status not in {408, 425, 429}
        ),
        None,
    )
    if status_codes:
        result["status_code"] = deterministic_status or status_codes[0]
    if retry_declarations:
        result["retryable"] = all(value is True for value in retry_declarations)
    if deterministic_status is not None:
        result["retryable"] = False
    return result


def _operation_failure_metadata(value: Any) -> dict[str, Any]:
    """Aggregate child failures conservatively before permitting a replay."""

    if not isinstance(value, dict):
        return {}
    nested = value.get("error")
    top = _metadata_from_sources(
        [nested, value] if isinstance(nested, dict) else [value]
    )
    child_failures: list[dict[str, Any]] = []
    for collection_name in ("domains", "failures", "results", "items", "libraries"):
        collection = value.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict) or not _operation_item_failed(item):
                continue
            item_error = item.get("error")
            sources = [item_error, item] if isinstance(item_error, dict) else [item]
            child_failures.append(_metadata_from_sources(sources))

    groups = child_failures or [top]
    result: dict[str, Any] = {}
    retry_decisions = [group.get("retryable") for group in groups]
    if "retryable" in top and child_failures:
        retry_decisions.append(top["retryable"])
    # Replaying an aggregate is safe only when every failed child explicitly
    # opts in. A missing, malformed or false declaration closes the gate.
    result["retryable"] = bool(retry_decisions) and all(
        decision is True for decision in retry_decisions
    )

    for key in ("category", "status_code"):
        if key in top:
            result[key] = top[key]
            continue
        values = {group[key] for group in child_failures if key in group}
        if len(values) == 1:
            result[key] = values.pop()
    retry_delays = [
        group["retry_after_ms"]
        for group in [top, *child_failures]
        if "retry_after_ms" in group
    ]
    if retry_delays:
        result["retry_after_ms"] = max(retry_delays)
    return result


def _expand_operation_error(
    reason: str,
    *,
    scope: str,
    module: str,
    command: str,
    metadata: Any = None,
) -> ExpandOperationError:
    structured = _operation_failure_metadata(metadata)
    operation = (
        str(scope or "").strip().casefold(),
        str(module or "").strip().casefold(),
        str(command or "").strip().casefold(),
    )
    retryable = bool(structured.get("retryable")) and operation in _RETRY_SAFE_OPERATIONS
    return ExpandOperationError(
        reason,
        category=str(structured.get("category") or "expand_operation_error"),
        status_code=structured.get("status_code"),
        retryable=retryable,
        retry_after_ms=structured.get("retry_after_ms"),
    )


def _failure_value_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, dict):
        code = value.get("code")
        for key in ("message", "reason", "detail", "error", "status"):
            detail = value.get(key)
            if isinstance(detail, str) and detail.strip():
                prefix = (
                    f"{str(code).strip()}: "
                    if isinstance(code, str) and code.strip()
                    else ""
                )
                return prefix + detail.strip()
        return "结构化失败详情"
    try:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value).strip()


def _operation_failure_reason(result: dict[str, Any]) -> str:
    """Keep structured child failures visible instead of replacing them with a generic error."""

    direct = result.get("error") or result.get("message") or result.get("reason")
    direct_text = _failure_value_text(direct)
    details: list[str] = [direct_text] if direct_text else []
    for collection_name in ("domains", "failures", "results", "items", "libraries"):
        collection = result.get(collection_name)
        if not isinstance(collection, list):
            continue
        for index, item in enumerate(collection):
            if not isinstance(item, dict):
                continue
            if not _operation_item_failed(item):
                continue
            identity = next(
                (
                    str(item.get(key)).strip()
                    for key in ("library_id", "domain_id", "source_uri", "name", "id")
                    if item.get(key) not in (None, "")
                ),
                f"{collection_name}[{index}]",
            )
            detail = (
                item.get("error")
                or item.get("message")
                or item.get("reason")
                or item.get("status")
                or "failed"
            )
            details.append(f"{identity}: {_failure_value_text(detail)}")
    return ("；".join(details) or "拓展操作返回失败状态")[:4000]
