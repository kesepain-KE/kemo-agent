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

from provider.protocol.errors import StreamProtocolError
from provider.protocol.assets import AssetDescriptor
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


_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


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
                body=raw[:1000],
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
                body=raw[:1000],
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

    def embeddings(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Call the native Kemo vectorization endpoint without translation."""

        http_request = urllib.request.Request(
            self.embeddings_url,
            data=to_json_bytes(request),
            headers=self._headers(
                stream=False,
                idempotency_key=request.request_id,
            ),
            method="POST",
        )
        with self._open(http_request) as response:
            raw = response.read()
        try:
            return EmbeddingResponse.model_validate_json(raw)
        except Exception as exc:
            raise ProviderError(
                "Kemo gateway 返回了无效的 embedding 响应",
                category="gateway_protocol_error",
                body=raw[:1000],
            ) from exc

    # Provider packages use ``embed`` as the task-level method name.  Keep the
    # endpoint-named alias as well so callers can use either vocabulary.
    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return self.embeddings(request)

    def rerank(self, request: RerankRequest) -> RerankResponse:
        """Call the native Kemo reranking endpoint without translation."""

        http_request = urllib.request.Request(
            self.rerank_url,
            data=to_json_bytes(request),
            headers=self._headers(
                stream=False,
                idempotency_key=request.request_id,
            ),
            method="POST",
        )
        with self._open(http_request) as response:
            raw = response.read()
        try:
            return RerankResponse.model_validate_json(raw)
        except Exception as exc:
            raise ProviderError(
                "Kemo gateway 返回了无效的 rerank 响应",
                category="gateway_protocol_error",
                body=raw[:1000],
            ) from exc

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
            message = str((current.error or {}).get("message") or "Asset 不可用")
            raise ProviderError(
                f"Kemo Asset {current.id} 状态为 {current.status}：{message}",
                category="asset_error",
                retryable=False,
                body=current.model_dump(mode="json", exclude_none=True),
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
