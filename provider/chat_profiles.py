"""Local Chat compatibility policy, shared by capability and wire mapping.

No network probes and no configuration writes. Unknown endpoints receive an
explicitly *assumed* standard Chat policy; advanced features stay closed.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from provider.chat_deepseek import is_deepseek_chat, is_official_deepseek_url
from provider.protocol.models import ModelCapabilities, PROTOCOL_VERSION
from provider.schema import ProviderError


# Exact public aliases documented at api-docs.deepseek.com/quick_start/pricing
# and /guides/thinking_mode, reviewed 2026-10-26. No r1*/v3* prefix claims.
_DEEPSEEK_MODELS = {
    "deepseek-flash": True,
    "deepseek-v4-flash": True,
    "deepseek-v4-flash-vision-exp": True,
    "deepseek-v4-pro": False,
}
_ADAPTER_FIELDS = {
    "transport", "degradation_policy", "provider_option_map",
    "provider_options", "provider_options_allowlist",
}


@dataclass(frozen=True)
class EffectiveChatProfile:
    profile: dict[str, Any]
    capabilities: ModelCapabilities


@lru_cache(maxsize=128)
def _warn_deprecated_claims(keys: tuple[str, ...]) -> None:
    logging.getLogger(__name__).warning(
        "Chat capabilities 能力声明已 deprecated 并忽略；能力由内置档案与自动推定决定"
    )


def warn_deprecated_chat_capabilities(config: dict[str, Any]) -> None:
    """Warn during configuration loading without probing or printing values."""
    raw = config.get("capabilities")
    if not isinstance(raw, dict):
        return
    declared = raw.get("capabilities", raw)
    if isinstance(declared, dict):
        keys = tuple(sorted(key for key in declared if key not in _ADAPTER_FIELDS))
        if keys:
            _warn_deprecated_claims(keys)


def _user_profile(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("capabilities")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ProviderError("Chat capabilities 必须是对象", category="invalid_config")
    nested = raw.get("capabilities")
    if nested is not None and not isinstance(nested, dict):
        raise ProviderError("Chat capabilities.capabilities 必须是对象", category="invalid_config")
    declared = nested if isinstance(nested, dict) else raw
    warn_deprecated_chat_capabilities(config)
    profile = {key: copy.deepcopy(value) for key, value in declared.items() if key in _ADAPTER_FIELDS}
    if isinstance(nested, dict):
        for key in _ADAPTER_FIELDS:
            if key in raw:
                profile[key] = copy.deepcopy(raw[key])
    return profile


def resolve_chat_profile(config: dict[str, Any], model: str) -> EffectiveChatProfile:
    """Resolve once for the actual model, not the main agent's model alias.

    Legacy capability claims are ignored with a deprecation warning.
    Adapter transport mappings and vendor allow-lists remain configurable.
    """
    profile: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION, "model": model,
        "provider_id": "chat-compat", "provider_model": model,
        "streaming": True,
        "reasoning": {"supported": True,
                      "efforts": ["minimal", "low", "medium", "high", "max", "xhigh", "ultra"],
                      "contexts": ["none", "current_turn", "auto"],
                      "returns": ["none", "content", "auto"]},
        "transport": {"reasoning": {"request_format": "effort"}},
        "tools": {"function_calling": True, "parallel_calls": True,
                  "multimodal_results": False,
                  "tool_choice_modes": ["auto", "none", "required", "named"]},
        "decoding": {"temperature": True, "top_p": True, "stop": True,
                     "presence_penalty": True, "frequency_penalty": True},
        "structured_output": {"supported": True, "strict": False,
                              "schema_subset": ["json_object", "json_schema"]},
        "extensions": {"capability_source": "compat-default",
                       "capability_verification": "assumed",
                       "compatibility_profile": "chat-standard-v2"},
    }
    if "vision" in model.casefold():
        profile["input_modalities"] = ["text", "image"]
    if model in _DEEPSEEK_MODELS:
        profile.update({
            "input_modalities": ["text", "image"] if _DEEPSEEK_MODELS[model] else ["text"],
            "tools": {"function_calling": True, "parallel_calls": True,
                      "multimodal_results": False, "tool_choice_modes": ["auto", "none"]},
            "reasoning": {"supported": True,
                          "efforts": ["minimal", "low", "medium", "high", "max", "xhigh", "ultra"],
                          "contexts": ["none", "current_turn", "auto"],
                          "returns": ["none", "content", "auto"]},
            "transport": {"reasoning": {"history_field": "reasoning_content"}},
            "structured_output": {"supported": True, "strict": False,
                                  "schema_subset": ["json_object"]},
            "extensions": {"capability_source": "builtin",
                           "capability_verification": "documented-endpoint" if is_official_deepseek_url(
                               str(config.get("base_url") or "")) else "assumed-relay",
                           "compatibility_profile": "deepseek-v4-chat-20261026"},
        })

    explicit = _user_profile(config)
    for key, value in explicit.items():
        if key == "transport" and isinstance(value, dict):
            transport = copy.deepcopy(profile.get("transport", {}))
            for name, setting in value.items():
                if name == "reasoning" and isinstance(setting, dict):
                    transport[name] = {**transport.get(name, {}), **setting}
                    if "request_format" not in setting and any(
                        field in setting for field in ("enabled_field", "effort_field")
                    ):
                        transport[name].pop("request_format", None)
                else:
                    transport[name] = copy.deepcopy(setting)
            profile[key] = transport
        elif key == "extensions" and isinstance(value, dict):
            profile[key] = {**profile[key], **copy.deepcopy(value)}
        else:
            profile[key] = copy.deepcopy(value)
    # These implementation boundaries cannot be changed by capability claims.
    tools = profile.get("tools")
    if isinstance(tools, dict):
        tools["multimodal_results"] = False
        if is_deepseek_chat({**config, "model": model}) and tools.get("function_calling"):
            tools["tool_choice_modes"] = [m for m in tools.get("tool_choice_modes", ["auto", "none"])
                                          if m in {"auto", "none"}]
    transport = profile.get("transport")
    reasoning_transport = transport.get("reasoning", {}) if isinstance(transport, dict) else {}
    if not isinstance(reasoning_transport, dict):
        raise ProviderError("Chat transport.reasoning 必须是对象", category="invalid_config")
    wire = {key: value for key, value in profile.items() if key not in _ADAPTER_FIELDS}
    capabilities = ModelCapabilities.model_validate(wire)
    return EffectiveChatProfile(profile=profile, capabilities=capabilities)
