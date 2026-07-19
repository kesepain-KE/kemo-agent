"""Native transport for the kemo unified protocol endpoint."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from provider.protocol.errors import StreamProtocolError
from provider.protocol.models import KemoRequest, KemoResponse, ModelCapabilities
from provider.protocol.serialization import parse_response, to_json_bytes
from provider.protocol.streaming import (
    ProviderStreamEvent,
    StreamSequenceGuard,
    parse_sse_events,
)
from provider.protocol.validation import validate_request
from provider.schema import (
    ProviderAuthError,
    ProviderError,
    ProviderTimeoutError,
)


_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class KemoGatewayAdapter:
    """Send protocol-v1 objects without translating them to Chat Completions."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.base_url = str(config["base_url"]).rstrip("/")
        self.api_key = str(config["api_key"])
        self.model = str(config["model"])
        self.timeout = float(config.get("timeout", 120))
        self.responses_url = f"{self.base_url}/model/responses"
        self.capabilities_url = f"{self.base_url}/model/capabilities"

    def _headers(
        self,
        *,
        stream: bool,
        last_event_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "X-Kemo-Protocol-Version": "1.0",
        }
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
            headers["X-Request-ID"] = idempotency_key
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
            message = f"Kemo gateway HTTP {exc.code}"
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or message)
            if exc.code in {401, 403}:
                raise ProviderAuthError(message, status_code=exc.code, body=body) from exc
            raise ProviderError(
                message,
                category="gateway_error",
                status_code=exc.code,
                retryable=exc.code in _RETRYABLE_STATUS,
                body=body,
            ) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ProviderTimeoutError(f"Kemo gateway 请求超时：{reason}") from exc
            raise ProviderError(
                f"Kemo gateway 连接失败：{reason}",
                category="connection_error",
                retryable=True,
            ) from exc

    def validate(self, request: KemoRequest) -> None:
        validate_request(request)

    def capabilities(self, model: str) -> ModelCapabilities:
        url = self.capabilities_url + "?" + urllib.parse.urlencode({"model": model})
        request = urllib.request.Request(
            url,
            headers=self._headers(stream=False),
            method="GET",
        )
        with self._open(request) as response:
            raw = response.read()
        try:
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("capabilities"), dict):
                data = data["capabilities"]
            return ModelCapabilities.model_validate(data)
        except Exception as exc:
            raise ProviderError(
                "Kemo gateway 返回了无效的模型能力声明",
                category="gateway_protocol_error",
                body=raw[:1000],
            ) from exc

    def create(self, request: KemoRequest) -> KemoResponse:
        self.validate(request)
        payload = request.model_copy(update={"stream": False})
        http_request = urllib.request.Request(
            self.responses_url,
            data=to_json_bytes(payload),
            headers=self._headers(stream=False, idempotency_key=request.request_id),
            method="POST",
        )
        with self._open(http_request) as response:
            return parse_response(response.read())

    def stream(
        self,
        request: KemoRequest,
        *,
        last_event_id: str | None = None,
        resume_from_sequence: int | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        self.validate(request)
        payload = request.model_copy(update={"stream": True})
        query = ""
        if resume_from_sequence is not None:
            query = "?" + urllib.parse.urlencode(
                {"resume_from_sequence": max(0, int(resume_from_sequence))}
            )
        http_request = urllib.request.Request(
            self.responses_url + query,
            data=to_json_bytes(payload),
            headers=self._headers(
                stream=True,
                last_event_id=last_event_id,
                idempotency_key=request.request_id,
            ),
            method="POST",
        )
        response = self._open(http_request)
        guard = StreamSequenceGuard()
        terminal = False
        try:
            lines = iter(response.readline, b"")
            for event in parse_sse_events(lines):
                if guard.accept(event):
                    terminal = terminal or event.terminal
                    yield event
            if not terminal:
                raise StreamProtocolError("Kemo gateway 流在统一终态事件前结束")
        finally:
            response.close()

    def get_response(self, response_id: str) -> KemoResponse:
        url = f"{self.responses_url}/{urllib.parse.quote(response_id, safe='')}"
        request = urllib.request.Request(
            url,
            headers=self._headers(stream=False),
            method="GET",
        )
        with self._open(request) as response:
            return parse_response(response.read())

    def cancel(self, response_id: str) -> KemoResponse:
        url = (
            f"{self.responses_url}/"
            f"{urllib.parse.quote(response_id, safe='')}/cancel"
        )
        request = urllib.request.Request(
            url,
            data=b"{}",
            headers=self._headers(stream=False),
            method="POST",
        )
        with self._open(request) as response:
            return parse_response(response.read())
