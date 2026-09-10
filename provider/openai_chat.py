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
_TOOL_PAYLOAD_KEYS = frozenset({
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "functions",
    "function_call",
})
_TOOLS_UNSUPPORTED_PHRASES = (
    "tools are not supported",
    "tools is not supported",
    "tools not supported",
    "tool calling is not supported",
    "tool calling not supported",
    "function calling is not supported",
    "function calling not supported",
    "unsupported parameter: tools",
    "unknown parameter: tools",
    "unrecognized parameter: tools",
    "unknown argument: tools",
    "unrecognized request argument: tools",
    "unrecognized request argument supplied: tools",
)


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


def _safe_call_index(value: Any, fallback: int) -> int:
    if isinstance(value, (bool, float)):
        return fallback
    if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
        return fallback
    try:
        index = int(value)
    except (TypeError, ValueError):
        return fallback
    return index if index >= 0 else fallback


def _merge_tool_arguments(part: dict[str, Any], value: Any) -> None:
    """Merge standard string fragments and tolerate one complete JSON object."""

    if value is None:
        return
    already_seen = bool(part.get("arguments_seen"))
    part["arguments_seen"] = True
    if isinstance(value, dict):
        # A dict from a compatible endpoint is a complete argument object, not
        # a textual delta.  Keep the first object if the service repeats it.
        if not already_seen:
            part["arguments"] = json.dumps(value, ensure_ascii=False)
            part["arguments_object"] = True
        return
    if part.get("arguments_object"):
        return
    fragment = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=str)
    )
    part["arguments"] = str(part.get("arguments") or "") + fragment


def _tools_are_explicitly_unsupported(message: str) -> bool:
    normalized = str(message or "").strip().casefold()
    normalized = normalized.translate(
        str.maketrans({"'": "", '"': "", "`": ""})
    )
    return any(phrase in normalized for phrase in _TOOLS_UNSUPPORTED_PHRASES)


def _response_content_type(response: Any) -> str:
    headers = getattr(response, "headers", None)
    if headers is not None:
        get_content_type = getattr(headers, "get_content_type", None)
        if callable(get_content_type):
            try:
                return str(get_content_type() or "").strip().casefold()
            except (AttributeError, TypeError, ValueError):
                pass
        get_header = getattr(headers, "get", None)
        if callable(get_header):
            try:
                value = get_header("Content-Type")
                if value:
                    return str(value).split(";", 1)[0].strip().casefold()
            except (AttributeError, TypeError, ValueError):
                pass
    getheader = getattr(response, "getheader", None)
    if callable(getheader):
        try:
            value = getheader("Content-Type")
            if value:
                return str(value).split(";", 1)[0].strip().casefold()
        except (AttributeError, TypeError, ValueError):
            pass
    return ""


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
            if exc.code == 400 and _tools_are_explicitly_unsupported(message):
                category = "tools_unsupported"
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
        # Chat is a minimum-compatibility transport.  There is currently no
        # reliable opt-in that distinguishes user intent from the historical
        # default effort, so vendor reasoning fields are never forced.
        payload.pop("reasoning_effort", None)
        payload.pop("reasoning_enabled", None)
        if stream:
            options = payload.get("stream_options")
            if not isinstance(options, dict):
                options = {}
            payload["stream_options"] = {**options, "include_usage": True}
        return payload

    def _request(
        self,
        payload: dict[str, Any],
        *,
        stream: bool,
    ) -> urllib.request.Request:
        return urllib.request.Request(
            self._url(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(stream=stream),
            method="POST",
        )

    def _open_payload(self, payload: dict[str, Any], *, stream: bool):
        current = dict(payload)
        try:
            return self._open(self._request(current, stream=stream))
        except ProviderError as exc:
            if exc.category != "tools_unsupported" or not current.get("tools"):
                raise
        # The first request failed before any model output was available.
        # Retry exactly once as plain text and let any second failure surface.
        fallback = {
            key: value
            for key, value in current.items()
            if key not in _TOOL_PAYLOAD_KEYS
        }
        return self._open(self._request(fallback, stream=stream))

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
        with self._open_payload(payload, stream=False) as response:
            data = _decode_json(response.read(), "Chat Completions")
        return self._response(data, request)

    def _response_events(
        self,
        response: ChatResponse,
        *,
        done_marker_received: bool,
        response_format: str,
    ) -> Iterable[RunEvent]:
        if response.reasoning:
            yield RunEvent(type="reasoning_delta", content=response.reasoning)
        if response.text:
            yield RunEvent(type="text_delta", content=response.text)
        for index, call in enumerate(response.tool_calls):
            metadata: dict[str, Any] = {
                "index": index,
                "parse_error": (
                    safe_parse_error(call.parse_error) if call.parse_error else None
                ),
                "arguments_diagnostic": (
                    tool_arguments_diagnostic(call.arguments_raw)
                    if call.parse_error
                    else None
                ),
                "finish_reason": response.finish_reason,
            }
            if call.arguments_raw is not None:
                metadata["raw_arguments"] = call.arguments_raw
            yield RunEvent(
                type="tool_call_start",
                tool_call_id=call.id or f"tool-call-{index}",
                tool_name=call.name,
                arguments=call.arguments,
                metadata=metadata,
            )
        usage = response.usage.to_dict()
        yield RunEvent(type="usage", usage=usage)
        yield RunEvent(
            type="done",
            usage=usage,
            metadata={
                "finish_reason": response.finish_reason,
                "done_marker_received": done_marker_received,
                "model": response.model,
                "response_id": response.response_id,
                "response_format": response_format,
            },
        )

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
        response = self._open_payload(payload, stream=True)
        text_parts: list[str] = []
        tool_parts: dict[int, dict[str, Any]] = {}
        final_usage: Usage | None = None
        done_received = False
        finish_reason = ""
        response_model = request.model or self.model
        response_id = ""
        try:
            content_type = _response_content_type(response)
            if content_type == "application/json" or content_type.endswith("+json"):
                data = _decode_json(response.read(), "Chat Completions stream fallback")
                parsed = self._response(data, request)
                yield from self._response_events(
                    parsed,
                    done_marker_received=False,
                    response_format="json_fallback",
                )
                return
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
                        index = _safe_call_index(raw_call.get("index"), position)
                        part = tool_parts.setdefault(index, {"id": "", "name": ""})
                        if raw_call.get("id") and not part["id"]:
                            part["id"] = str(raw_call["id"])
                        function = (
                            raw_call.get("function")
                            if isinstance(raw_call.get("function"), dict)
                            else {}
                        )
                        if function.get("name") and not part["name"]:
                            part["name"] = str(function["name"])
                        if "arguments" in function:
                            _merge_tool_arguments(part, function.get("arguments"))
                    legacy_function = delta.get("function_call")
                    if isinstance(legacy_function, dict):
                        part = tool_parts.setdefault(
                            0,
                            {"id": "function-call-0", "name": ""},
                        )
                        if legacy_function.get("name") and not part["name"]:
                            part["name"] = str(legacy_function["name"])
                        if "arguments" in legacy_function:
                            _merge_tool_arguments(
                                part,
                                legacy_function.get("arguments"),
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
                        **(
                            {"raw_arguments": arguments_raw}
                            if arguments_raw is not None
                            else {}
                        ),
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
