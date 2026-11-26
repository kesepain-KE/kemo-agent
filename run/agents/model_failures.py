"""Project unsuccessful sub-agent Provider responses into runtime errors."""
from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from provider.protocol.diagnostics import incomplete_retry_metadata, safe_provider_message
from provider.protocol.enums import ResponseStatus
from provider.protocol.models import KemoResponse
from run.agents.contracts import AgentProviderError

if TYPE_CHECKING:
    from run.agents.continuation import ContinuationState


def raise_provider_failure(
    response: KemoResponse, continuation: ContinuationState | None, agent: str, model: str,
) -> NoReturn:
    if response.error is not None:
        provider_error = response.error
        message = safe_provider_message(
            provider_error.message,
            "Provider 响应失败",
        )
        retryable: bool | None = None
        fields_set = getattr(provider_error, "model_fields_set", set())
        if "retryable" in fields_set:
            retryable = provider_error.retryable
        elif isinstance(provider_error.details, dict):
            declared = provider_error.details.get("retryable")
            if isinstance(declared, bool):
                retryable = declared
        category = provider_error.type or "provider_error"
        code = provider_error.code
        status_code = provider_error.provider_status
        retry_after_ms = provider_error.retry_after_ms
        if response.status == ResponseStatus.INCOMPLETE:
            incomplete = incomplete_retry_metadata(
                response.incomplete_details,
                fallback_retryable=retryable,
                fallback_category=provider_error.type,
                fallback_code=provider_error.code,
                fallback_status_code=provider_error.provider_status,
            )
            retryable = incomplete["retryable"]
            category = str(incomplete.get("category") or category)
            code = code or str(incomplete["reason"])
            status_code = incomplete.get("status_code", status_code)
            retry_after_ms = incomplete.get(
                "retry_after_ms",
                retry_after_ms,
            )
        elif response.status == ResponseStatus.CANCELLED:
            retryable = False
        raise AgentProviderError(
            f"子代理 Provider 响应失败：{message}",
            category=category,
            code=code,
            status_code=status_code,
            retryable=retryable,
            retry_after_ms=retry_after_ms,
            cancelled=response.status == ResponseStatus.CANCELLED,
        )
    if response.status == ResponseStatus.INCOMPLETE:
        incomplete = incomplete_retry_metadata(
            response.incomplete_details,
            allow_output_truncated_retry=True,
        )
        error = AgentProviderError(
            f"子代理 Provider 响应未完整结束：{incomplete['reason']}",
            category=str(
                incomplete.get("category") or "provider_incomplete"
            ),
            code=str(incomplete["reason"]),
            status_code=incomplete.get("status_code"),
            retryable=incomplete["retryable"],
            retry_after_ms=incomplete.get("retry_after_ms"),
        )
        if incomplete["reason"] == "output_truncated" and continuation is not None:
            error.continuation = continuation
            error.partial_result = continuation.partial(agent, model)
            error.process_terminated = True
        raise error
    raise AgentProviderError(
        f"子代理 Provider 响应失败：{response.status}",
        category="provider_error",
        retryable=response.status != ResponseStatus.CANCELLED,
        cancelled=response.status == ResponseStatus.CANCELLED,
    )
