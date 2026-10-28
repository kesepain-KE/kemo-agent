"""提供者中立的请求和响应合同。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from events import RunEvent


_RETRYABLE_UNSET = object()
_CHAT_EXTRA_FIELDS = frozenset({
    "reasoning_enabled", "reasoning_effort", "top_p", "top_k", "seed", "logit_bias",
    "presence_penalty", "frequency_penalty", "verbosity", "stop", "n", "logprobs",
    "top_logprobs", "tool_choice", "allowed_tools", "parallel_tool_calls",
    "response_format", "prediction", "service_tier", "prompt_cache_key",
    "safety_identifier", "store", "metadata", "max_completion_tokens",
    "web_search_options", "search_parameters", "enable_search", "thinking",
    "enable_thinking", "thinking_budget", "reasoning", "chat_template_kwargs",
    "min_p", "repetition_penalty",
})


class ProviderError(RuntimeError):
    """Base error raised by a provider implementation."""

    if TYPE_CHECKING:
        code: str
        retry_exhausted: bool
        retry_attempts: int
        retry_max_attempts: int

    def __init__(
        self,
        message: str,
        *,
        category: str = "provider_error",
        status_code: int | None = None,
        retryable: bool | object = _RETRYABLE_UNSET,
        retry_after_ms: int | None = None,
        attempt_count: int | None = None,
        retry_budget_exhausted: bool = False,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        # Keep the historical boolean attribute for callers, but remember
        # whether the provider explicitly supplied a retry decision.  An
        # omitted decision is classified by the runtime (usually as a
        # transient failure); an explicit false must remain authoritative.
        self.retryable_declared = isinstance(retryable, bool)
        self.retryable = retryable if self.retryable_declared else False
        self.retry_after_ms = retry_after_ms
        self.attempt_count = attempt_count
        self.retry_budget_exhausted = bool(retry_budget_exhausted)
        self.body = body


class ProviderAuthError(ProviderError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, category="auth_error", retryable=False, **kwargs)


class ProviderTimeoutError(ProviderError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, category="timeout", retryable=True, **kwargs)


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False
    source: str = "provider"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated": self.estimated,
            "source": self.source,
        }
        result.update(self.extra)
        return result


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    arguments_raw: str | None = None
    parse_error: dict[str, Any] | None = None


@dataclass(slots=True)
class ChatRequest:
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Trusted adapter configuration only; not serialized or read from options.
    adapter_extra_fields: frozenset[str] = field(default_factory=frozenset, repr=False, kw_only=True)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "stream": self.stream,
        }
        if self.tools:
            payload["tools"] = self.tools
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        protected = {"model", "messages", "stream", "tools", "temperature", "max_tokens",
                     "system", "input", "instructions", "api_key", "headers",
                     "base_url", "authorization"}
        if protected.intersection(str(key).casefold() for key in self.extra):
            raise ProviderError("extra 不得覆盖对话、身份或正式请求字段", retryable=False)
        if set(self.extra) - _CHAT_EXTRA_FIELDS - self.adapter_extra_fields:
            raise ProviderError("extra 包含未经适配器授权的字段", retryable=False)
        payload.update(self.extra)
        return payload


@dataclass(slots=True)
class ChatResponse:
    text: str
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Chat Completions response metadata which has a first-class Kemo 2.0
    # representation.  Keep the transport DTO deliberately permissive: the
    # adapter validates/normalizes these values against the protocol models.
    refusal: str | None = None
    annotations: list[dict[str, Any]] = field(default_factory=list)
    logprobs: dict[str, Any] | None = None
    finish_reason: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    response_id: str = ""
    system_fingerprint: str | None = None
    service_tier: str | None = None
    choice_index: int = 0
    choice_count: int = 1
    raw: dict[str, Any] | None = None
    reasoning_synthetic: bool = False


class ChatTransport(Protocol):

    def chat(self, request: ChatRequest) -> ChatResponse: ...

    def chat_stream(self, request: ChatRequest) -> Iterable[RunEvent]: ...


MULTIMODAL_CAPABILITIES = frozenset(
    {
        "vision.image",
        "vision.video",
        "image.generation",
        "image.edit",
        "audio.asr",
        "audio.tts",
        "audio.speech_to_speech",
        "video.generation",
        "embedding",
        "rerank",
    }
)
