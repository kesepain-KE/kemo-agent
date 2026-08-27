"""Native transport for the kemo unified protocol endpoint."""

from __future__ import annotations

import json
import hashlib
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from provider.adapters.reliability import (
    CANCEL_REQUEST_TIMEOUT_SECONDS,
    NETWORK_READ_ERRORS,
    KemoNetworkRetryPolicy,
    parse_retry_after_ms,
    start_cancel_watcher,
    transport_error,
)
from provider.protocol.errors import StreamProtocolError
from provider.protocol.assets import AssetDescriptor
from provider.protocol.diagnostics import safe_provider_body, safe_provider_message
from provider.protocol.models import (
    EmbeddingRequest,
    EmbeddingResponse,
    KemoRequest,
    KemoResponse,
    ModelCapabilities,
    ModelCatalogResponse,
    RerankRequest,
    RerankResponse,
)
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


_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class _MultipartUpload:
    """Replay-free multipart body that streams one file in bounded chunks."""

    def __init__(self, prefix: bytes, path: Path, suffix: bytes, cancel_event=None) -> None:
        self.prefix = prefix
        self.path = path
        self.suffix = suffix
        self.cancel_event = cancel_event

    @property
    def length(self) -> int:
        return len(self.prefix) + self.path.stat().st_size + len(self.suffix)

    def __iter__(self):
        yield self.prefix
        with self.path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                if self.cancel_event is not None and self.cancel_event.is_set():
                    raise ProviderError(
                        "Kemo Asset 上传已取消",
                        category="cancelled",
                        retryable=False,
                    )
                yield chunk
        yield self.suffix


class KemoGatewayAdapter:
    """Send protocol-v1 objects without translating them to Chat Completions."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.base_url = str(config["base_url"]).rstrip("/")
        self.api_key = str(config["api_key"])
        self.model = str(config["model"])
        self.timeout = float(config.get("timeout", 120))
        self._retry_policy = KemoNetworkRetryPolicy()
        self.responses_url = f"{self.base_url}/model/responses"
        self.embeddings_url = f"{self.base_url}/model/embeddings"
        self.rerank_url = f"{self.base_url}/model/rerank"
        self.capabilities_url = f"{self.base_url}/model/capabilities"
        self.models_url = f"{self.base_url}/model/models"
        self.assets_url = f"{self.base_url}/assets"

    def _headers(
        self,
        *,
        stream: bool,
        last_event_id: str | None = None,
        idempotency_key: str | None = None,
        content_type: str = "application/json",
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": content_type,
            "Accept": "text/event-stream" if stream else "application/json",
            "X-Kemo-Protocol-Version": "1.0",
        }
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
            headers["X-Request-ID"] = idempotency_key
        return headers

    @staticmethod
    def _parse_asset(raw: bytes) -> AssetDescriptor:
        try:
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("asset"), dict):
                data = data["asset"]
            return AssetDescriptor.model_validate(data)
        except Exception as exc:
            raise ProviderError(
                "Kemo gateway 返回了无效的 Asset 描述",
                category="gateway_protocol_error",
                body=safe_provider_body(raw),
            ) from exc

    def _open(self, request: urllib.request.Request):
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                body: Any = json.loads(raw.decode("utf-8")) if raw else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = raw.decode("utf-8", errors="replace")[:1000]
            fallback = f"Kemo gateway HTTP {exc.code}"
            message = fallback
            declared_retryable: bool | None = None
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    message = safe_provider_message(
                        error.get("message"),
                        fallback,
                    )
                    if isinstance(error.get("retryable"), bool):
                        declared_retryable = bool(error["retryable"])
            safe_body = safe_provider_body(body)
            if exc.code in {401, 403}:
                raise ProviderAuthError(
                    message,
                    status_code=exc.code,
                    body=safe_body,
                ) from exc
            raise ProviderError(
                message,
                category="gateway_error",
                status_code=exc.code,
                retryable=(
                    declared_retryable
                    if declared_retryable is not None
                    else exc.code in _RETRYABLE_STATUS
                ),
                retry_after_ms=parse_retry_after_ms(exc, body),
                body=safe_body,
            ) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ProviderTimeoutError(
                    safe_provider_message(
                        f"Kemo gateway 请求超时：{reason}",
                        "Kemo gateway 请求超时",
                    )
                ) from exc
            raise ProviderError(
                safe_provider_message(
                    f"Kemo gateway 连接失败：{reason}",
                    "Kemo gateway 连接失败",
                ),
                category="connection_error",
                retryable=True,
            ) from exc

    def validate(self, request: KemoRequest) -> None:
        validate_request(request)

    def _read_all(
        self,
        response: Any,
        *,
        action: str,
        cancel_event: threading.Event | None,
    ) -> bytes:
        """Read one blocking Kemo response with cooperative cancellation."""

        watcher = start_cancel_watcher(response, cancel_event)
        try:
            try:
                raw = response.read()
            except NETWORK_READ_ERRORS as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise self._retry_policy.cancelled_error() from exc
                raise transport_error(exc, action=action) from exc
            if cancel_event is not None and cancel_event.is_set():
                raise self._retry_policy.cancelled_error()
            return raw
        finally:
            if watcher is not None:
                watcher.close()

    def _model_capabilities_url(
        self,
        model: str,
        capabilities_url: str | None,
    ) -> str:
        if not capabilities_url:
            return self.capabilities_url + "?" + urllib.parse.urlencode(
                {"model": model}
            )
        configured = urllib.parse.urlsplit(self.base_url)
        resolved = urllib.parse.urljoin(f"{self.base_url}/", capabilities_url)
        target = urllib.parse.urlsplit(resolved)
        configured_origin = (
            configured.scheme.casefold(),
            (configured.hostname or "").casefold(),
            configured.port,
        )
        target_origin = (
            target.scheme.casefold(),
            (target.hostname or "").casefold(),
            target.port,
        )
        if (
            target.scheme not in {"http", "https"}
            or target.username is not None
            or target.password is not None
            or target_origin != configured_origin
        ):
            raise ProviderError(
                "Kemo 模型目录返回了跨源 capabilities_url，已拒绝携带网关密钥访问",
                category="gateway_protocol_error",
                retryable=False,
            )
        return urllib.parse.urlunsplit(target._replace(fragment=""))

    def capabilities(
        self,
        model: str,
        *,
        capabilities_url: str | None = None,
    ) -> ModelCapabilities:
        url = self._model_capabilities_url(model, capabilities_url)
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
            capabilities = ModelCapabilities.model_validate(data)
            if capabilities.model != model:
                raise ValueError(
                    f"能力声明模型不匹配：{capabilities.model!r} != {model!r}"
                )
            return capabilities
        except Exception as exc:
            raise ProviderError(
                "Kemo gateway 返回了无效的模型能力声明",
                category="gateway_protocol_error",
                body=safe_provider_body(raw),
            ) from exc

    def models(self, *, task: str | None = None) -> ModelCatalogResponse:
        query = urllib.parse.urlencode({"task": task}) if task else ""
        url = self.models_url + (f"?{query}" if query else "")
        request = urllib.request.Request(
            url,
            headers=self._headers(stream=False),
            method="GET",
        )
        with self._open(request) as response:
            raw = response.read()
        try:
            return ModelCatalogResponse.model_validate_json(raw)
        except Exception as exc:
            raise ProviderError(
                "Kemo gateway 返回了无效的模型目录",
                category="gateway_protocol_error",
                body=safe_provider_body(raw),
            ) from exc

    def create(
        self,
        request: KemoRequest,
        *,
        cancel_event: threading.Event | None = None,
    ) -> KemoResponse:
        self.validate(request)
        payload = request.model_copy(update={"stream": False})
        body = to_json_bytes(payload)

        def send() -> KemoResponse:
            http_request = urllib.request.Request(
                self.responses_url,
                data=body,
                headers=self._headers(
                    stream=False,
                    idempotency_key=request.request_id,
                ),
                method="POST",
            )
            with self._open(http_request) as response:
                raw = self._read_all(
                    response,
                    action="读取响应",
                    cancel_event=cancel_event,
                )
            response = parse_response(raw)
            if response.request_id != request.request_id:
                raise ProviderError(
                    "Kemo gateway 响应的 request_id 与请求不一致",
                    category="gateway_protocol_error",
                    retryable=False,
                )
            return response

        return self._retry_policy.run(
            send,
            cancel_event=cancel_event,
        )

    def embeddings(
        self,
        request: EmbeddingRequest,
        *,
        cancel_event: threading.Event | None = None,
    ) -> EmbeddingResponse:
        """Call the native Kemo vectorization endpoint without translation."""

        body = to_json_bytes(request)

        def send() -> EmbeddingResponse:
            http_request = urllib.request.Request(
                self.embeddings_url,
                data=body,
                headers=self._headers(
                    stream=False,
                    idempotency_key=request.request_id,
                ),
                method="POST",
            )
            with self._open(http_request) as response:
                raw = self._read_all(
                    response,
                    action="读取 embedding 响应",
                    cancel_event=cancel_event,
                )
            try:
                return EmbeddingResponse.model_validate_json(raw)
            except Exception as exc:
                raise ProviderError(
                    "Kemo gateway 返回了无效的 embedding 响应",
                    category="gateway_protocol_error",
                    body=safe_provider_body(raw),
                ) from exc

        return self._retry_policy.run(send, cancel_event=cancel_event)

    # Provider packages use ``embed`` as the task-level method name.  Keep the
    # endpoint-named alias as well so callers can use either vocabulary.
    def embed(
        self,
        request: EmbeddingRequest,
        *,
        cancel_event: threading.Event | None = None,
    ) -> EmbeddingResponse:
        return self.embeddings(request, cancel_event=cancel_event)

    def rerank(
        self,
        request: RerankRequest,
        *,
        cancel_event: threading.Event | None = None,
    ) -> RerankResponse:
        """Call the native Kemo reranking endpoint without translation."""

        body = to_json_bytes(request)

        def send() -> RerankResponse:
            http_request = urllib.request.Request(
                self.rerank_url,
                data=body,
                headers=self._headers(
                    stream=False,
                    idempotency_key=request.request_id,
                ),
                method="POST",
            )
            with self._open(http_request) as response:
                raw = self._read_all(
                    response,
                    action="读取 rerank 响应",
                    cancel_event=cancel_event,
                )
            try:
                return RerankResponse.model_validate_json(raw)
            except Exception as exc:
                raise ProviderError(
                    "Kemo gateway 返回了无效的 rerank 响应",
                    category="gateway_protocol_error",
                    body=safe_provider_body(raw),
                ) from exc

        return self._retry_policy.run(send, cancel_event=cancel_event)

    def _cancel_response_best_effort(self, response_id: str) -> None:
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
        try:
            with urllib.request.urlopen(
                request,
                timeout=min(self.timeout, CANCEL_REQUEST_TIMEOUT_SECONDS),
            ) as response:
                response.read()
        except Exception:
            # Local cancellation must not turn into a second Provider failure.
            pass

    def stream(
        self,
        request: KemoRequest,
        *,
        last_event_id: str | None = None,
        resume_from_sequence: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        self.validate(request)
        payload = request.model_copy(update={"stream": True})
        body = to_json_bytes(payload)
        current_last_event_id = str(last_event_id or "").strip() or None
        if resume_from_sequence is not None and int(resume_from_sequence) < 0:
            raise ValueError("resume_from_sequence 不能小于 0")
        if resume_from_sequence is not None and current_last_event_id is None:
            raise ValueError(
                "Kemo 1.0 续传必须提供 Last-Event-ID；不能只使用 resume_from_sequence"
            )
        current_resume_sequence = (
            int(resume_from_sequence) if resume_from_sequence is not None else None
        )
        guard = StreamSequenceGuard(
            start_after_sequence=current_resume_sequence,
            allow_initial_offset=(
                current_last_event_id is not None
                and current_resume_sequence is None
            ),
        )
        terminal = False
        expected_response_id: str | None = None
        remote_cancel_id: str | None = None

        try:
            for attempt in range(1, self._retry_policy.max_attempts + 1):
                if cancel_event is not None and cancel_event.is_set():
                    raise self._retry_policy.cancelled_error(
                        attempt_count=attempt - 1
                    )
                http_request = urllib.request.Request(
                    self.responses_url,
                    data=body,
                    headers=self._headers(
                        stream=True,
                        last_event_id=current_last_event_id,
                        idempotency_key=request.request_id,
                    ),
                    method="POST",
                )
                response = None
                watcher = None
                failure: ProviderError | None = None
                try:
                    response = self._open(http_request)
                    watcher = start_cancel_watcher(
                        response,
                        cancel_event,
                    )
                    saw_eof = False

                    def lines():
                        nonlocal saw_eof
                        while True:
                            if cancel_event is not None and cancel_event.is_set():
                                raise self._retry_policy.cancelled_error(
                                    attempt_count=attempt
                                )
                            line = response.readline()
                            if cancel_event is not None and cancel_event.is_set():
                                raise self._retry_policy.cancelled_error(
                                    attempt_count=attempt
                                )
                            if not line:
                                saw_eof = True
                                return
                            yield line

                    event_iterator = parse_sse_events(lines())
                    while True:
                        try:
                            event = next(event_iterator)
                        except StopIteration:
                            break
                        except StreamProtocolError as exc:
                            if not saw_eof:
                                raise
                            raise ProviderError(
                                "Kemo gateway SSE 事件在传输中被截断",
                                category="connection_error",
                                retryable=True,
                            ) from exc
                        if event.request_id != request.request_id:
                            raise StreamProtocolError(
                                "Kemo gateway 流事件的 request_id 与请求不一致",
                                details={
                                    "expected": request.request_id,
                                    "actual": event.request_id,
                                },
                            )
                        if expected_response_id is None:
                            expected_response_id = event.response_id
                            remote_cancel_id = event.response_id
                        elif event.response_id != expected_response_id:
                            raise StreamProtocolError(
                                "Kemo gateway 续传返回了不同的 response_id，已拒绝拼接",
                                details={
                                    "expected": expected_response_id,
                                    "actual": event.response_id,
                                },
                            )
                        if guard.accept(event):
                            current_last_event_id = event.event_id
                            current_resume_sequence = event.sequence
                            terminal = terminal or event.terminal
                            yield event
                    if terminal:
                        return
                    failure = ProviderError(
                        "Kemo gateway SSE 在统一终态事件前断开",
                        category="connection_error",
                        retryable=True,
                    )
                except ProviderError as exc:
                    failure = exc
                except NETWORK_READ_ERRORS as exc:
                    failure = transport_error(exc, action="读取 SSE")
                finally:
                    if response is not None:
                        try:
                            response.close()
                        except Exception:
                            pass
                    if watcher is not None:
                        watcher.close()

                if cancel_event is not None and cancel_event.is_set():
                    raise self._retry_policy.cancelled_error(
                        attempt_count=attempt
                    )
                if failure is None:
                    raise StreamProtocolError("Kemo gateway SSE 未知状态")
                self._retry_policy.retry_or_raise(
                    failure,
                    failed_attempt=attempt,
                    cancel_event=cancel_event,
                )
            raise AssertionError("Kemo gateway SSE 重试循环异常退出")
        finally:
            if (
                cancel_event is not None
                and cancel_event.is_set()
                and not terminal
                and remote_cancel_id
            ):
                self._cancel_response_best_effort(remote_cancel_id)

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

    def upload_asset(
        self,
        path: str | Path,
        *,
        metadata: dict[str, Any],
        idempotency_key: str,
        checksum_sha256: str,
        mime_type: str,
        cancel_event: threading.Event | None = None,
    ) -> AssetDescriptor:
        target = Path(path).resolve(strict=True)
        if not target.is_file():
            raise ProviderError("Kemo Asset 上传目标不是文件", category="asset_error")
        boundary = "----kemo-agent-" + uuid.uuid4().hex
        safe_name = target.name.replace('"', "_")
        metadata_json = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        prefix = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="metadata"\r\n'
            "Content-Type: application/json; charset=utf-8\r\n\r\n"
            f"{metadata_json}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        body = _MultipartUpload(prefix, target, suffix, cancel_event)
        headers = self._headers(
            stream=False,
            idempotency_key=idempotency_key,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        headers["Content-Length"] = str(body.length)
        headers["X-Content-SHA256"] = checksum_sha256
        request = urllib.request.Request(
            self.assets_url,
            data=body,
            headers=headers,
            method="POST",
        )
        with self._open(request) as response:
            return self._parse_asset(response.read())

    def get_asset(self, asset_id: str) -> AssetDescriptor:
        url = f"{self.assets_url}/{urllib.parse.quote(asset_id, safe='')}"
        request = urllib.request.Request(
            url,
            headers=self._headers(stream=False),
            method="GET",
        )
        with self._open(request) as response:
            return self._parse_asset(response.read())

    def wait_asset_ready(
        self,
        asset: AssetDescriptor | str,
        *,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
    ) -> AssetDescriptor:
        current = asset if isinstance(asset, AssetDescriptor) else self.get_asset(asset)
        deadline = time.monotonic() + (self.timeout if timeout is None else max(1.0, timeout))
        while current.status in {"uploading", "processing"}:
            if cancel_event is not None and cancel_event.wait(0.25):
                raise ProviderError(
                    "等待 Kemo Asset 就绪时已取消",
                    category="cancelled",
                    retryable=False,
                )
            if time.monotonic() >= deadline:
                raise ProviderTimeoutError(f"等待 Kemo Asset 就绪超时：{current.id}")
            current = self.get_asset(current.id)
        if current.status != "ready":
            message = safe_provider_message(
                (current.error or {}).get("message"),
                "Asset 不可用",
            )
            raise ProviderError(
                f"Kemo Asset {current.id} 状态为 {current.status}：{message}",
                category="asset_error",
                retryable=False,
                body=safe_provider_body(
                    current.model_dump(mode="json", exclude_none=True)
                ),
            )
        return current

    def download_asset(
        self,
        asset_id: str,
        destination: str | Path,
        *,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        max_bytes: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        url = (
            f"{self.assets_url}/{urllib.parse.quote(asset_id, safe='')}/content"
        )
        request = urllib.request.Request(
            url,
            headers=self._headers(stream=False, content_type="application/octet-stream"),
            method="GET",
        )
        digest = hashlib.sha256()
        received = 0
        try:
            with self._open(request) as response, part.open("xb") as handle:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ProviderError(
                            "Kemo Asset 下载已取消",
                            category="cancelled",
                            retryable=False,
                        )
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if max_bytes is not None and received > max_bytes:
                        raise ProviderError(
                            f"Kemo Asset 下载超过本地限制：{asset_id}",
                            category="request_too_large",
                            retryable=False,
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            actual = digest.hexdigest()
            if expected_size is not None and received != expected_size:
                raise ProviderError(
                    f"Kemo Asset 下载大小不一致：{asset_id}",
                    category="asset_integrity_error",
                    retryable=False,
                )
            if expected_sha256 and actual != expected_sha256.casefold():
                raise ProviderError(
                    f"Kemo Asset 校验和不一致：{asset_id}",
                    category="asset_integrity_error",
                    retryable=False,
                )
            os.replace(part, target)
            return target
        finally:
            part.unlink(missing_ok=True)

    def delete_asset(self, asset_id: str) -> bool:
        url = f"{self.assets_url}/{urllib.parse.quote(asset_id, safe='')}"
        request = urllib.request.Request(
            url,
            headers=self._headers(stream=False),
            method="DELETE",
        )
        with self._open(request) as response:
            response.read()
        return True
