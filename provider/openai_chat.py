"""Low-level OpenAI-compatible ``/chat/completions`` HTTP transport."""

from __future__ import annotations

import http.client
import json
import math
import socket
import urllib.error
import urllib.request
from typing import Any, Iterable

from events import RunEvent
from provider.protocol.diagnostics import (
    safe_parse_error,
    safe_provider_message,
    safe_provider_body,
    tool_arguments_diagnostic,
)
from provider.schema import (
    ChatRequest,
    ChatResponse,
    ProviderAuthError,
    ProviderError,
    ProviderTimeoutError,
    ToolCall,
    Usage,
)
from provider.tool_arguments import MISSING, parse_tool_arguments


_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _error_detail(data: Any, fallback: str) -> tuple[str, str]:
    value = data
    if isinstance(value, dict) and "detail" in value:
        value = value["detail"]
    if isinstance(value, dict) and "error" in value:
        value = value["error"]
    if isinstance(value, dict):
        message = safe_provider_message(
            value.get("message") or value.get("detail"),
            fallback,
        )
        category = safe_provider_message(value.get("type"), "provider_error")
        return message, category
    if value not in (None, ""):
        return safe_provider_message(value, fallback), "provider_error"
    return fallback, "provider_error"


def _decode_json(raw: bytes, context: str) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError(f"{context} 返回了无效 JSON", body=safe_provider_body(raw)) from exc
    if not isinstance(data, dict):
        raise ProviderError(
            f"{context} 返回根节点必须是 JSON 对象",
            body=safe_provider_body(data),
        )
    return data


def _parse_arguments(
    value: Any,
) -> tuple[dict[str, Any], str | None, dict[str, Any] | None]:
    parsed = parse_tool_arguments(value)
    return parsed.arguments, parsed.arguments_raw, parsed.parse_error


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
        # 保守的无依赖性估计：CJK 倾向于每个代币一个代币
        # 字符，而拉丁文本倾向于每 4 个字符 1 个标记。
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    remaining = len(text) - cjk
    return cjk + math.ceil(remaining / 4)


def _messages_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.append(json.dumps(content, ensure_ascii=False, default=str))
    return "\n".join(parts)


class OpenAIChatTransport:
    def __init__(self, config: dict[str, Any]) -> None:
        self.base_url = str(config["base_url"]).rstrip("/")
        self.api_key = str(config["api_key"])
        self.model = str(config["model"])
        self.timeout = float(config.get("timeout", 120))
        self.default_stream = bool(config.get("stream", True))

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self, *, stream: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        return headers

    def _open(self, request: urllib.request.Request):
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                body: Any = json.loads(raw.decode("utf-8")) if raw else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = raw.decode("utf-8", errors="replace")[:1000]
            message, category = _error_detail(body, f"HTTP {exc.code}")
            safe_body = safe_provider_body(body)
            if exc.code in {401, 403}:
                raise ProviderAuthError(
                    message, status_code=exc.code, body=safe_body
                ) from exc
            raise ProviderError(
                message,
                category=category,
                status_code=exc.code,
                retryable=exc.code in _RETRYABLE_STATUS,
                body=safe_body,
            ) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ProviderTimeoutError(
                    safe_provider_message(
                        f"Provider 请求超时：{reason}",
                        "Provider 请求超时",
                    )
                ) from exc
            raise ProviderError(
                safe_provider_message(
                    f"Provider 连接失败：{reason}",
                    "Provider 连接失败",
                ),
                category="connection_error",
                retryable=True,
            ) from exc

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        payload = request.to_payload()
        payload["model"] = request.model or self.model
        payload["stream"] = stream
        if stream:
            options = payload.get("stream_options")
            if not isinstance(options, dict):
                options = {}
            payload["stream_options"] = {**options, "include_usage": True}
        return payload

    def _usage(self, raw: Any, request: ChatRequest, output: str) -> Usage:
        if isinstance(raw, dict) and raw:
            prompt = int(raw.get("prompt_tokens") or 0)
            completion = int(raw.get("completion_tokens") or 0)
            total = int(raw.get("total_tokens") or prompt + completion)
            extras = {
                key: value
                for key, value in raw.items()
                if key not in {"prompt_tokens", "completion_tokens", "total_tokens"}
            }
            return Usage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                estimated=False,
                source="provider",
                extra=extras,
            )
        prompt = _estimate_tokens(_messages_text(request.messages))
        completion = _estimate_tokens(output)
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            estimated=True,
            source="local_estimate",
        )

    def chat(self, request: ChatRequest) -> ChatResponse:
        payload = self._payload(request, stream=False)
        raw_request = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            self._url(), data=raw_request, headers=self._headers(stream=False), method="POST"
        )
        with self._open(http_request) as response:
            data = _decode_json(response.read(), "Chat Completions")
        return self._response(data, request)

    def _response(self, data: dict[str, Any], request: ChatRequest) -> ChatResponse:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                "Chat Completions 响应缺少 choices",
                body=safe_provider_body(data),
            )
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        text = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
        tool_calls: list[ToolCall] = []
        raw_tool_calls = message.get("tool_calls") or []
        legacy_function_call = message.get("function_call")
        if not raw_tool_calls and isinstance(legacy_function_call, dict):
            raw_tool_calls = [
                {
                    "id": "function-call-0",
                    "function": legacy_function_call,
                }
            ]
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
            arguments, arguments_raw, parse_error = _parse_arguments(
                function["arguments"] if "arguments" in function else MISSING
            )
            tool_calls.append(
                ToolCall(
                    id=str(raw_call.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                    arguments_raw=arguments_raw,
                    parse_error=parse_error,
                )
            )
        return ChatResponse(
            text=str(text),
            reasoning=str(reasoning),
            tool_calls=tool_calls,
            finish_reason=str(choice.get("finish_reason") or ""),
            usage=self._usage(data.get("usage"), request, str(text)),
            model=str(data.get("model") or request.model or self.model),
            response_id=str(data.get("id") or ""),
            raw=data,
        )

    def chat_stream(self, request: ChatRequest) -> Iterable[RunEvent]:
        payload = self._payload(request, stream=True)
        http_request = urllib.request.Request(
            self._url(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(stream=True),
            method="POST",
        )
        response = self._open(http_request)
        text_parts: list[str] = []
        tool_parts: dict[int, dict[str, Any]] = {}
        final_usage: Usage | None = None
        done_received = False
        finish_reason = ""
        response_model = request.model or self.model
        response_id = ""
        try:
            while True:
                try:
                    raw_line = response.readline()
                except (http.client.IncompleteRead, http.client.RemoteDisconnected, OSError) as exc:
                    raise ProviderError(
                        f"Provider 流式传输中断：{type(exc).__name__}: {exc}",
                        category="stream_interrupted",
                        retryable=not text_parts and not tool_parts,
                    ) from exc
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if value == "[DONE]":
                    done_received = True
                    break
                if not value:
                    continue
                try:
                    data = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ProviderError(
                        "Provider 返回了畸形 SSE JSON",
                        body=safe_provider_body(value),
                    ) from exc
                if not isinstance(data, dict):
                    continue
                if data.get("error"):
                    message, category = _error_detail(data, "流式上游错误")
                    raise ProviderError(
                        message,
                        category=category,
                        body=safe_provider_body(data),
                    )
                response_id = str(data.get("id") or response_id)
                response_model = str(data.get("model") or response_model)
                if data.get("usage"):
                    final_usage = self._usage(data["usage"], request, "".join(text_parts))
                choices = data.get("choices") or []
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    finish_reason = str(choice.get("finish_reason") or finish_reason)
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    reasoning = delta.get("reasoning_content") or ""
                    text = delta.get("content") or ""
                    if reasoning:
                        yield RunEvent(
                            type="reasoning_delta", content=str(reasoning), metadata={"raw": data}
                        )
                    if text:
                        text_parts.append(str(text))
                        yield RunEvent(type="text_delta", content=str(text), metadata={"raw": data})
                    for position, raw_call in enumerate(delta.get("tool_calls") or []):
                        if not isinstance(raw_call, dict):
                            continue
                        index = int(raw_call.get("index", position))
                        part = tool_parts.setdefault(index, {"id": "", "name": ""})
                        if raw_call.get("id"):
                            part["id"] += str(raw_call["id"])
                        function = (
                            raw_call.get("function")
                            if isinstance(raw_call.get("function"), dict)
                            else {}
                        )
                        if function.get("name"):
                            part["name"] += str(function["name"])
                        if "arguments" in function:
                            part["arguments_seen"] = True
                        if function.get("arguments") is not None:
                            part["arguments"] = str(part.get("arguments") or "") + str(
                                function["arguments"]
                            )
                    legacy_function = delta.get("function_call")
                    if isinstance(legacy_function, dict):
                        part = tool_parts.setdefault(
                            0,
                            {"id": "function-call-0", "name": ""},
                        )
                        if legacy_function.get("name"):
                            part["name"] += str(legacy_function["name"])
                        if "arguments" in legacy_function:
                            part["arguments_seen"] = True
                        if legacy_function.get("arguments") is not None:
                            part["arguments"] = str(part.get("arguments") or "") + str(
                                legacy_function["arguments"]
                            )
            # A few OpenAI-compatible services close the HTTP body cleanly after
            # the final choice instead of sending the optional literal [DONE].
            # A real finish_reason is sufficient; EOF without either marker is
            # still treated as an interrupted stream.
            if not done_received and not finish_reason:
                raise ProviderError(
                    "Provider 流在收到 [DONE] 前关闭",
                    category="stream_interrupted",
                    retryable=not text_parts and not tool_parts,
                )
            for index in sorted(tool_parts):
                part = tool_parts[index]
                arguments, arguments_raw, parse_error = _parse_arguments(
                    part["arguments"] if part.get("arguments_seen") else MISSING
                )
                yield RunEvent(
                    type="tool_call_start",
                    tool_call_id=part["id"] or f"tool-call-{index}",
                    tool_name=part["name"],
                    arguments=arguments,
                    metadata={
                        "index": index,
                        "parse_error": (
                            safe_parse_error(parse_error) if parse_error else None
                        ),
                        "arguments_diagnostic": (
                            tool_arguments_diagnostic(arguments_raw)
                            if parse_error
                            else None
                        ),
                        "finish_reason": finish_reason,
                    },
                )
            if final_usage is None:
                final_usage = self._usage(None, request, "".join(text_parts))
            yield RunEvent(type="usage", usage=final_usage.to_dict())
            yield RunEvent(
                type="done",
                usage=final_usage.to_dict(),
                metadata={
                    "finish_reason": finish_reason,
                    "done_marker_received": done_received,
                    "model": response_model,
                    "response_id": response_id,
                },
            )
        finally:
            response.close()
