"""Shared Kemo model-capability cache and reasoning selection policy."""

from __future__ import annotations
from run.retry import HOST_CONTROL_EXCEPTIONS

from dataclasses import dataclass
import hashlib
import logging
import threading
import time
from typing import Any

from provider.factory import provider_request_slot
from provider.protocol.models import ModelCapabilities, normalize_reasoning_effort


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


def declared_input_token_limit(
    capabilities: ModelCapabilities | None,
) -> int | None:
    """Prefer a declared input limit and fall back to the model context window.

    Older Kemo gateways put the limit in ``extensions.limits`` while newer
    capability responses expose it in the typed ``limits`` object.  Runtime
    context protection must understand both forms, but a malformed optional
    extension must never make an otherwise usable capability response fail.

    ``context_window`` is only used when neither location declares an explicit
    ``max_input_tokens``.
    """

    if capabilities is None:
        return None

    candidates: list[int] = []

    def add_candidate(value: Any) -> None:
        if isinstance(value, bool):
            return
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return
        if parsed > 0:
            candidates.append(parsed)

    limits = getattr(capabilities, "limits", None)
    extensions = getattr(capabilities, "extensions", {})
    extension_limits = (
        extensions.get("limits")
        if isinstance(extensions, dict)
        else None
    )
    for name in ("max_input_tokens", "context_window"):
        add_candidate(getattr(limits, name, None))
        if isinstance(extension_limits, dict):
            add_candidate(extension_limits.get(name))
        if candidates:
            return min(candidates)
    return None


def model_output_token_limit(
    capabilities: ModelCapabilities | None, *, configured: int | None = None,
) -> int | None:
    """Use an explicit request limit or the model maximum; unknown stays unset."""
    if configured is not None and (type(configured) is not int or configured < 1):
        raise ValueError("Configured output capacity must be a positive integer")
    limits = getattr(capabilities, "limits", None)
    extension_limits = getattr(capabilities, "extensions", {}).get("limits", {})
    if not isinstance(extension_limits, dict):
        extension_limits = {}
    candidates = [value for value in (
        configured, getattr(limits, "max_output_tokens", None),
        extension_limits.get("max_output_tokens"),
    ) if type(value) is int and value > 0]
    return min(candidates) if candidates else None


def context_input_budget(
    configured_limit: int, capabilities: ModelCapabilities | None,
    *, output_reserve: int = 0,
) -> dict[str, Any]:
    """Prefer an input declaration, then a model window, then configuration."""
    if type(configured_limit) is not int or configured_limit < 1:
        raise ValueError("Configured context capacity must be a positive integer")
    if type(output_reserve) is not int or output_reserve < 0:
        raise ValueError("Output reserve must be a nonnegative integer")
    limits = getattr(capabilities, "limits", None)
    extension_limits = getattr(capabilities, "extensions", {}).get("limits", {})
    if not isinstance(extension_limits, dict):
        extension_limits = {}
    if output_reserve:
        output_reserve = model_output_token_limit(capabilities, configured=output_reserve) or output_reserve
    input_limits = []
    for value in (getattr(limits, "max_input_tokens", None),
                  extension_limits.get("max_input_tokens")):
        if type(value) is int and value > 0:
            input_limits.append(value)  # Already input-only; do not subtract again.
    windows = []
    for value in (getattr(limits, "context_window", None),
                  extension_limits.get("context_window")):
        if type(value) is int and value > 0:
            windows.append(value)
    window = min(windows) if windows else configured_limit
    declared = (min(input_limits) if input_limits
                else max(0, window - output_reserve) if windows else None)
    effective = declared if declared is not None else max(0, configured_limit - output_reserve)
    if effective < 1:
        raise ValueError("输出预留已占满上下文容量")
    return {
        "configured_token_limit": configured_limit,
        "context_window_tokens": window,
        "context_window_source": "model" if windows else "configuration",
        "input_limit_source": ("max_input_tokens" if input_limits
                               else "context_window" if windows else "configuration"),
        "provider_declared_input_limit": declared,
        "effective_token_limit": effective,
        "output_reserve": output_reserve,
        "capability_unknown": declared is None,
    }


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


def cached_model_capabilities(
    runtime_provider: dict[str, Any], *,
    capabilities_url: str | None = None,
) -> ModelCapabilities | None:
    """Read current evidence without probing the network from a status page."""
    key = _cache_key(runtime_provider, str(runtime_provider.get("model") or ""), capabilities_url)
    with _capability_lock:
        now = time.monotonic()
        # Catalogs may provide a custom capability URL. Status readers know the
        # selected route and model, and can reuse its declaration without I/O.
        candidates = [
            cached for cache_key, cached in _capability_cache.items()
            if (cache_key == key if capabilities_url else cache_key[:4] == key[:4])
            and cached.fresh_until > now
        ]
        cached = max(candidates, key=lambda value: value.fresh_until, default=None)
        return cached.capabilities if cached is not None else None


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
    if str(runtime_provider.get("type") or "").strip().casefold() == "chat":
        # Chat declarations are local profile data, not a network capability
        # probe and not a request-derived default. Never cache across profiles.
        method = getattr(provider, "capabilities", None)
        if not callable(method):
            return CapabilityLookup(None)
        try:
            declared = ModelCapabilities.model_validate(method(selected_model))
            if declared.model != selected_model:
                raise ValueError("Chat 能力声明与所选模型不一致")
            return CapabilityLookup(declared)
        except Exception as exc:
            return CapabilityLookup(None, error=exc)
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
        if isinstance(exc, HOST_CONTROL_EXCEPTIONS):
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
    available: list[str] = []
    for item in efforts:
        effort = str(item or "").strip().casefold()
        if not effort or effort == "none" or effort in available:
            continue
        available.append(effort)
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
    if provider_type not in {"chat", "kemo"}:
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
