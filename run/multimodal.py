"""Capability-aware multimodal routing shared by Chat and Kemo providers."""

from __future__ import annotations

import threading
import time
from typing import Any, Literal

from run.config import ConfigError
from provider.factory import provider_request_slot


VisionRoute = Literal["main", "dedicated"]
VISION_ROUTING_MODES = frozenset({"auto", "main", "dedicated"})
_CAPABILITY_CACHE_TTL = 300.0
_capability_cache: dict[tuple[str, str, str], tuple[float, Any]] = {}
_capability_lock = threading.RLock()


def configured_input_modalities(config: dict[str, Any]) -> tuple[str, ...]:
    provider = config.get("provider") or {}
    provider_type = str(provider.get("type") or "").strip().casefold()
    allowed = {"text", "image"} if provider_type == "chat" else {
        "text", "image", "audio", "video", "file"
    }
    raw = provider.get("input_modalities", ["text"])
    if not isinstance(raw, list) or not raw:
        raise ConfigError("provider.input_modalities 必须是非空数组")
    modalities: list[str] = []
    for value in raw:
        if not isinstance(value, str) or value not in allowed:
            allowed_text = "、".join(sorted(allowed))
            raise ConfigError(
                f"provider.type={provider_type or 'unknown'} 时 input_modalities 只允许 {allowed_text}"
            )
        if value not in modalities:
            modalities.append(value)
    if "text" not in modalities:
        raise ConfigError("provider.input_modalities 必须包含 text")
    return tuple(modalities)


def configured_vision_mode(config: dict[str, Any]) -> str:
    routing = config.get("multimodal_routing") or {}
    if not isinstance(routing, dict):
        raise ConfigError("multimodal_routing 必须是对象")
    unknown = sorted(set(routing) - {"vision"})
    if unknown:
        raise ConfigError("multimodal_routing 包含未知项：" + ", ".join(unknown))
    mode = str(routing.get("vision") or "auto").strip().casefold()
    if mode not in VISION_ROUTING_MODES:
        raise ConfigError("multimodal_routing.vision 只允许 auto、main 或 dedicated")
    return mode


def validate_multimodal_config(config: dict[str, Any]) -> None:
    configured_input_modalities(config)
    configured_vision_mode(config)


def _kemo_capability_key(runtime_provider: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(runtime_provider.get("type") or ""),
        str(runtime_provider.get("base_url") or ""),
        str(runtime_provider.get("model") or ""),
    )


def _gateway_capabilities(
    config: dict[str, Any],
    runtime_provider: dict[str, Any],
    provider: Any,
    *,
    cancel_event: threading.Event | None = None,
) -> Any | None:
    key = _kemo_capability_key(runtime_provider)
    now = time.monotonic()
    with _capability_lock:
        cached = _capability_cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
    declared = None
    capabilities = getattr(provider, "capabilities", None)
    if callable(capabilities):
        try:
            with provider_request_slot(config, cancel_event=cancel_event):
                declared = capabilities(str(runtime_provider["model"]))
        except Exception:
            declared = None
    with _capability_lock:
        _capability_cache[key] = (now + _CAPABILITY_CACHE_TTL, declared)
    return declared


def main_model_supports_input(
    config: dict[str, Any],
    runtime_provider: dict[str, Any],
    provider: Any,
    modality: str,
    *,
    operation: str = "conversation",
    cancel_event: threading.Event | None = None,
) -> bool:
    explicit = configured_input_modalities(config)
    provider_config = config.get("provider") or {}
    provider_type = str(runtime_provider.get("type") or "").strip().casefold()
    if provider_type == "chat":
        return modality == "image" and modality in explicit
    if provider_type != "kemo":
        return False
    if "input_modalities" in provider_config and modality not in explicit:
        return False
    declared = _gateway_capabilities(
        config,
        runtime_provider,
        provider,
        cancel_event=cancel_event,
    )
    if declared is None:
        return modality in explicit if "input_modalities" in provider_config else False
    if modality not in set(getattr(declared, "input_modalities", []) or []):
        return False
    operations = (getattr(declared, "extensions", {}) or {}).get("operations")
    if operation == "conversation" or not isinstance(operations, dict):
        return True
    operation_value = operations.get(operation)
    if isinstance(operation_value, dict):
        return operation_value.get("supported") is True
    return operation_value is True


def main_model_supports_images(
    config: dict[str, Any],
    runtime_provider: dict[str, Any],
    provider: Any,
    *,
    cancel_event: threading.Event | None = None,
) -> bool:
    return main_model_supports_input(
        config,
        runtime_provider,
        provider,
        "image",
        operation="vision",
        cancel_event=cancel_event,
    )


def select_vision_route(
    config: dict[str, Any],
    runtime_provider: dict[str, Any],
    provider: Any,
    *,
    cancel_event: threading.Event | None = None,
) -> VisionRoute:
    mode = configured_vision_mode(config)
    if mode == "dedicated":
        return "dedicated"
    supported = main_model_supports_images(
        config, runtime_provider, provider, cancel_event=cancel_event
    )
    if mode == "main":
        if not supported:
            raise ConfigError("视觉路由设为 main，但主模型没有声明 image 输入能力")
        return "main"
    return "main" if supported else "dedicated"
