"""Shared Kemo model-capability cache and reasoning selection policy."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import threading
import time
from typing import Any

from provider.factory import provider_request_slot
from provider.protocol.models import ModelCapabilities


LOGGER = logging.getLogger(__name__)
_CAPABILITY_CACHE_TTL = 300.0
_CAPABILITY_STALE_TTL = 1800.0


@dataclass(frozen=True, slots=True)
class CapabilityLookup:
    capabilities: ModelCapabilities | None
    stale: bool = False
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class ReasoningSelection:
    enabled: bool
    effort: str | None
    status: str
    stale: bool = False
    capabilities: ModelCapabilities | None = None
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class _CachedCapability:
    fresh_until: float
    stale_until: float
    capabilities: ModelCapabilities


_capability_cache: dict[tuple[str, str, str, str, str], _CachedCapability] = {}
_capability_lock = threading.RLock()


def _secret_fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def capability_cache_identity(runtime_provider: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(runtime_provider.get("type") or "").strip().casefold(),
        str(runtime_provider.get("base_url") or "").rstrip("/"),
        _secret_fingerprint(runtime_provider.get("api_key")),
    )


def _cache_key(
    runtime_provider: dict[str, Any],
    model: str,
    capabilities_url: str | None,
) -> tuple[str, str, str, str, str]:
    return (
        *capability_cache_identity(runtime_provider),
        str(model),
        str(capabilities_url or ""),
    )


def clear_model_capability_cache(
    runtime_provider: dict[str, Any] | None = None,
) -> None:
    with _capability_lock:
        if runtime_provider is None:
            _capability_cache.clear()
            return
        identity = capability_cache_identity(runtime_provider)
        for key in list(_capability_cache):
            if key[:3] == identity:
                _capability_cache.pop(key, None)


def retain_model_capability_cache(
    runtime_provider: dict[str, Any],
    models: set[str],
) -> None:
    """Discard cached declarations for models removed from a refreshed catalog."""

    identity = capability_cache_identity(runtime_provider)
    with _capability_lock:
        for key in list(_capability_cache):
            if key[:3] == identity and key[3] not in models:
                _capability_cache.pop(key, None)


def lookup_model_capabilities(
    config: dict[str, Any],
    runtime_provider: dict[str, Any],
    provider: Any,
    *,
    model: str | None = None,
    capabilities_url: str | None = None,
    cancel_event: threading.Event | None = None,
    force_refresh: bool = False,
) -> CapabilityLookup:
    selected_model = str(model or runtime_provider.get("model") or "").strip()
    if str(runtime_provider.get("type") or "").strip().casefold() != "kemo":
        return CapabilityLookup(None)
    key = _cache_key(runtime_provider, selected_model, capabilities_url)
    now = time.monotonic()
    with _capability_lock:
        cached = _capability_cache.get(key)
        if cached is not None and cached.stale_until <= now:
            _capability_cache.pop(key, None)
            cached = None
        if not force_refresh and cached is not None and cached.fresh_until > now:
            return CapabilityLookup(cached.capabilities)

    capability_method = getattr(provider, "capabilities", None)
    if not callable(capability_method):
        error = RuntimeError("Kemo Provider 未实现模型能力查询")
        return CapabilityLookup(
            cached.capabilities if cached is not None else None,
            stale=cached is not None,
            error=error,
        )
    try:
        with provider_request_slot(config, cancel_event=cancel_event):
            if capabilities_url:
                declared = capability_method(
                    selected_model,
                    capabilities_url=capabilities_url,
                )
            else:
                declared = capability_method(selected_model)
        capabilities = ModelCapabilities.model_validate(declared)
        if capabilities.model != selected_model:
            raise ValueError(
                f"能力声明模型不匹配：{capabilities.model!r} != {selected_model!r}"
            )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        LOGGER.warning("Kemo 模型能力查询失败：model=%s error=%s", selected_model, exc)
        return CapabilityLookup(
            cached.capabilities if cached is not None else None,
            stale=cached is not None,
            error=exc,
        )

    with _capability_lock:
        _capability_cache[key] = _CachedCapability(
            fresh_until=now + _CAPABILITY_CACHE_TTL,
            stale_until=now + _CAPABILITY_STALE_TTL,
            capabilities=capabilities,
        )
    return CapabilityLookup(capabilities)


def select_declared_reasoning_effort(
    configured: Any,
    efforts: list[str] | tuple[str, ...],
) -> str | None:
    available = [str(item).strip().casefold() for item in efforts if str(item).strip()]
    selected = str(configured or "").strip().casefold()
    if selected in available:
        return selected
    if "medium" in available:
        return "medium"
    return available[0] if available else None


def resolve_reasoning_selection(
    config: dict[str, Any],
    runtime_provider: dict[str, Any],
    provider: Any,
    *,
    model: str | None = None,
    capabilities_url: str | None = None,
    cancel_event: threading.Event | None = None,
) -> ReasoningSelection:
    provider_type = str(runtime_provider.get("type") or "").strip().casefold()
    configured = runtime_provider.get("reasoning_effort")
    if provider_type == "chat":
        return ReasoningSelection(True, str(configured or "medium"), "chat_legacy")
    if provider_type != "kemo":
        return ReasoningSelection(False, None, "unsupported_provider")
    lookup = lookup_model_capabilities(
        config,
        runtime_provider,
        provider,
        model=model,
        capabilities_url=capabilities_url,
        cancel_event=cancel_event,
    )
    if lookup.capabilities is None:
        return ReasoningSelection(
            False,
            None,
            "capabilities_unavailable",
            error=lookup.error,
        )
    reasoning = lookup.capabilities.reasoning
    if not reasoning.supported or not reasoning.efforts:
        return ReasoningSelection(
            False,
            None,
            "reasoning_unsupported",
            stale=lookup.stale,
            capabilities=lookup.capabilities,
            error=lookup.error,
        )
    effort = select_declared_reasoning_effort(configured, reasoning.efforts)
    return ReasoningSelection(
        effort is not None,
        effort,
        "enabled" if effort is not None else "reasoning_unsupported",
        stale=lookup.stale,
        capabilities=lookup.capabilities,
        error=lookup.error,
    )
