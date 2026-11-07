"""One native Provider exchange; retries never restart the integration or tools."""
from __future__ import annotations

import threading
import time
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Iterator
from uuid import uuid4

from events import RunEvent
from provider.factory import provider_request_slot
from provider.protocol.diagnostics import safe_invalid_tool_details
from provider.protocol.enums import IncompleteReason, ResponseStatus
from provider.protocol.models import IncompleteDetails, KemoRequest, KemoResponse, ToolCallItem, Usage
from provider.retry import RetryCancelled, RetryManager, transient
from provider.schema import ProviderError
from run.conversation.provider_events import provider_events
from run.conversation.provider_projection import final_answer_messages
from run.infra import EngineError
from run.tools import (
    is_invalid_tool_arguments_error,
    response_invalid_tool_arguments_error,
)


@dataclass(frozen=True, slots=True)
class AcceptedResponse:
    response: KemoResponse
    attempts: int
    latency_ms: int
    request: KemoRequest


class ProviderResponseError(ProviderError):
    """Keep the protocol terminal's cancellation and retry evidence intact."""

    def __init__(self, detail: dict[str, Any]):
        super().__init__(
            str(detail.get("message") or "Provider response failed"),
            category=str(detail.get("category") or detail.get("type") or "protocol_error"),
            status_code=detail.get("status_code", detail.get("provider_status")),
            retryable=detail.get("retryable"),
            retry_after_ms=detail.get("retry_after_ms"),
            attempt_count=detail.get("attempt_count"),
            retry_budget_exhausted=detail.get("retry_budget_exhausted") is True,
        )
        self.cancelled = detail.get("cancelled") is True
        self.stop_reason = detail.get("stop_reason")
        self.incomplete_details = detail.get("incomplete_details")
        self.provider_exception_type = detail.get("exception_type")
        self.code = str(detail.get("code") or "")
        self.retry_scope = detail.get("retry_scope")


def _argument_correction(response: KemoResponse) -> bool:
    return response_invalid_tool_arguments_error(response) is not None


def _response_from_tool_argument_error(
    request: KemoRequest,
    event: RunEvent,
    *,
    usage: Usage | None,
) -> KemoResponse:
    error = event.error if isinstance(event.error, dict) else {}
    details = error.get("incomplete_details")
    if not isinstance(details, dict):
        details = error.get("details")
    if not isinstance(details, dict):
        details = {}
    nested = details.get("details")
    if isinstance(nested, dict):
        details = {**nested, **details}
    safe_details = safe_invalid_tool_details(details)
    return KemoResponse(
        id=event.response_id or "resp_" + uuid4().hex,
        request_id=request.request_id,
        status=ResponseStatus.INCOMPLETE,
        model=request.model,
        usage=usage or Usage(),
        incomplete_details=IncompleteDetails(
            reason=IncompleteReason.INVALID_TOOL_ARGUMENTS, details=safe_details,
        ),
    )


def exchange(
    provider, protocol_request: KemoRequest, config: dict[str, Any], *,
    root, user: str, retry: RetryManager, cancel_event: threading.Event | None,
    allow_interim: bool = False,
) -> Iterator[RunEvent]:
    """Return the full typed response, not a truncated diagnostic projection.

    Tools execute only after an accepted complete response. Partial text can be
    sealed as an incomplete snapshot before a new inference over the same input;
    an emitted media artifact or exhausted transport budget is never replayed.
    """
    started = time.monotonic()
    request = protocol_request
    for attempt in range(retry.max_retries + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RetryCancelled("主模型请求已取消")
        captured: list[KemoResponse] = []
        terminal = None
        partial = False
        media_emitted = False
        stream_usage: Usage | None = None
        try:
            with provider_request_slot(config, cancel_event=cancel_event), closing(provider_events(
                provider, request, root=root, user=user, cancel_event=cancel_event,
                on_response=captured.append,
                defer_tool_validation=True,
                allow_output_truncated_retry=True,
            )) as stream:
                for event in stream:
                    if event.type in {"done", "error"}:
                        terminal = event
                        if (
                            event.type == "error"
                            and captured
                            and _argument_correction(captured[-1])
                        ):
                            break
                        if (
                            event.type == "error"
                            and not captured
                            and is_invalid_tool_arguments_error(event.error)
                        ):
                            captured.append(
                                _response_from_tool_argument_error(
                                    request, event, usage=stream_usage,
                                )
                            )
                            break
                        if event.type == "error":
                            detail = event.error or {}
                            raise ProviderResponseError(detail)
                        break
                    if event.type in {"text_delta", "reasoning_delta"} and event.content:
                        partial = True
                    if event.type == "usage" and isinstance(event.usage, dict):
                        values = {
                            key: value
                            for key, value in event.usage.items()
                            if key in Usage.model_fields
                        }
                        try:
                            stream_usage = Usage.model_validate(values)
                        except (TypeError, ValueError):
                            pass
                    if event.type == "media_output":
                        media_emitted = True
                    yield event
            if terminal is None or not captured:
                raise ConnectionError("响应流在完整终态到达前结束")
            response = captured[-1]
            if (response.status not in {"completed", "requires_action"}
                    and not _argument_correction(response)):
                raise EngineError("Provider 未提供可接受的完整响应")
            if response.request_id != request.request_id:
                raise EngineError("Provider 响应不属于当前请求快照")
            if (
                not allow_interim
                and not _argument_correction(response)
                and not any(isinstance(item, ToolCallItem) for item in response.output)
                and not final_answer_messages(response)
            ):
                raise ProviderResponseError({
                    "message": "Provider 请求已结束，但未提供最终正文；空响应、仅思考或 commentary 不能完成整合",
                    "category": "provider_incomplete", "code": "missing_final_answer", "retryable": True,
                })
            return AcceptedResponse(response, attempt + 1, int((time.monotonic() - started) * 1000), request)
        except Exception as error:
            retryable = transient(error)
            failed_usage = captured[-1].usage if captured else stream_usage
            if media_emitted or attempt >= retry.max_retries or not retryable:
                if isinstance(error, ProviderError):
                    error.attempt_count = max(error.attempt_count or 0, attempt + 1)
                    error.retry_budget_exhausted = (
                        error.retry_budget_exhausted or retryable and attempt >= retry.max_retries)
                setattr(error, "failed_request_id", request.request_id)
                setattr(error, "partial_response", partial)
                if failed_usage is not None:
                    setattr(error, "failed_usage", failed_usage.model_dump(mode="json", exclude_none=True))
                raise
            failed_request = request
            # A completed failure is durably cached by Kemo under its request ID.
            # Transport replay keeps the exact body; a new inference after a
            # terminal or partial output needs a new identity over the same input.
            new_inference = terminal is not None or partial
            if new_inference:
                request = request.model_copy(update={
                    "request_id": "req_" + uuid4().hex,
                    "parent_request_id": failed_request.request_id,
                    "attempt": attempt + 2,
                })
            yield RunEvent(type="retrying", metadata={
                "phase": "provider", "retry_attempt": attempt + 1,
                "failed_attempt": attempt + 1, "next_attempt": attempt + 2,
                "max_attempts": retry.max_retries + 1, "max_retries": retry.max_retries,
                "replay_safe": not new_inference, "partial_response": partial,
                "failed_request_id": failed_request.request_id,
                "next_request_id": request.request_id,
                "exception_type": type(error).__name__, "committed": False,
            }, internal={
                "provider_request": request,
                "failed_usage": failed_usage.model_dump(mode="json", exclude_none=True)
                if new_inference and failed_usage is not None else None,
            })
            delay = retry.delay(attempt + 1, retry_after_ms=getattr(error, "retry_after_ms", None))
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    raise RetryCancelled("主模型退避期间取消") from error
            else:
                threading.Event().wait(delay)
    raise AssertionError("Provider retry loop exhausted unexpectedly")
