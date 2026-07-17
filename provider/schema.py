"""Provider-neutral request and response contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Protocol

from events import RunEvent


ProviderMode = Literal["openai", "kemo"]


class ProviderError(RuntimeError):
    """Base error raised by a provider implementation."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "provider_error",
        status_code: int | None = None,
        retryable: bool = False,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.retryable = retryable
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


@dataclass(slots=True)
class ChatRequest:
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

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
        payload.update(self.extra)
        return payload


@dataclass(slots=True)
class ChatResponse:
    text: str
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    response_id: str = ""
    raw: dict[str, Any] | None = None


class ChatProvider(Protocol):
    mode: ProviderMode

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
