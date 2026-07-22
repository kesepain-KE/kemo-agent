
"""kemo-agent 的配置加载。

全局默认值与一个用户的覆盖值合并。  秘密值是
在运行时从环境变量解析并且永远不会写回。"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any


USER_ONLY_SECTIONS = frozenset(
    {
        "provider",
        "agent_models",
        "multimodal_models",
        "knowledge",
        "skills",
        "expand",
        "perception",
        "plugins",
    }
)
MULTIMODAL_CAPABILITIES = frozenset(
    {
        "vision",
        "image_generation",
        "image_edit",
        "audio_transcription",
        "speech_generation",
        "speech_to_speech",
        "video_generation",
    }
)
AGENT_MODEL_PROFILES = frozenset({"default", "cheap", "reasoning"})


class ConfigError(RuntimeError):
    """Configuration is missing or malformed."""


def load_dotenv(path: Path, *, override: bool = False) -> None:
    """Load simple KEY=VALUE entries without adding a third-party dependency."""
    try:
        lines = path.read_text("utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ConfigError(f"环境变量文件不可读：{path}（{exc}）") from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not name or not name.replace("_", "a").isalnum() or name[0].isdigit():
            raise ConfigError(f".env 第 {line_number} 行格式无效")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
            value = value[1:-1]
        if override or name not in os.environ:
            os.environ[name] = value


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_json_object(path: Path, *, allow_empty: bool = False) -> dict[str, Any]:
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError:
        if allow_empty:
            return {}
        raise ConfigError(f"配置文件不存在：{path}") from None
    if not text.strip():
        if allow_empty:
            return {}
        raise ConfigError(f"配置文件为空：{path}")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件 JSON 无效：{path}（{exc}）") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"配置文件根节点必须是对象：{path}")
    return value


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def merge_user_config(
    global_config: dict[str, Any], user_config: dict[str, Any]
) -> dict[str, Any]:
    """Merge framework defaults with one user's private runtime choices.

    Provider routing and main-agent source selection are user-owned.  Keeping
    those sections out of the global merge prevents a stale global file from
    silently becoming a credential/model or data-scope fallback.
    """

    global_defaults = {
        key: value
        for key, value in global_config.items()
        if key not in USER_ONLY_SECTIONS
    }
    merged = deep_merge(global_defaults, user_config)
    for section in USER_ONLY_SECTIONS:
        if section in user_config:
            merged[section] = copy.deepcopy(user_config[section])
        else:
            merged.pop(section, None)
    return merged


def load_config(user: str, root: Path | None = None) -> dict[str, Any]:
    base = root or project_root()
    load_dotenv(base / ".env")
    from run.users import user_dir

    global_config = read_json_object(base / "config" / "global_config.json")
    user_config = read_json_object(
        user_dir(user, base) / "user_config.json", allow_empty=True
    )
    merged = merge_user_config(global_config, user_config)
    merged["user"] = user
    return merged


def provider_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    provider = copy.deepcopy(config.get("provider") or {})
    provider_type = str(provider.get("type") or "").strip().lower()
    if provider_type not in {"chat", "kemo"}:
        raise ConfigError("provider.type 必须是 'chat' 或 'kemo'")

    default_env = "KEMO_API_KEY" if provider_type == "kemo" else "OPENAI_API_KEY"
    env_name = str(provider.get("api_key_env") or default_env).strip()
    api_key = str(provider.get("api_key") or "").strip()
    if not api_key and env_name:
        api_key = os.getenv(env_name, "").strip()
    if not api_key:
        raise ConfigError(f"Provider API Key 未设置；请配置环境变量 {env_name}")

    model = str(provider.get("model") or "").strip()
    model_env = "KEMO_MODEL" if provider_type == "kemo" else "OPENAI_MODEL"
    model = model or os.getenv(model_env, "").strip()
    if not model:
        raise ConfigError(f"Provider 模型未设置；请配置 provider.model 或 {model_env}")

    base_url_env = "KEMO_BASE_URL" if provider_type == "kemo" else "OPENAI_BASE_URL"
    base_url = str(provider.get("base_url") or "").strip()
    if not base_url:
        base_url = os.getenv(base_url_env, "").strip()
    base_url = base_url.rstrip("/")
    if not base_url:
        base_url = (
            "http://127.0.0.1:8741"
            if provider_type == "kemo"
            else "https://api.openai.com/v1"
        )
    if provider_type == "chat" and not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    provider.update(
        {
            "type": provider_type,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "timeout": float(provider.get("timeout", 120.0)),
            "stream": bool(provider.get("stream", True)),
        }
    )
    provider.pop("headers", None)
    return provider


def resolve_agent_model(
    config: dict[str, Any],
    profile: str,
    *,
    model_override: str | None = None,
) -> str:
    """Resolve an optional per-profile subagent model, falling back to main."""

    if model_override is not None and str(model_override).strip():
        return str(model_override).strip()
    normalized_profile = str(profile or "default").strip().casefold()
    if normalized_profile not in AGENT_MODEL_PROFILES:
        raise ConfigError(f"未知子代理模型档位：{normalized_profile!r}")
    profiles = config.get("agent_models") or {}
    if not isinstance(profiles, dict):
        raise ConfigError("agent_models 必须是对象")
    unknown = sorted(set(profiles) - AGENT_MODEL_PROFILES)
    if unknown:
        raise ConfigError("agent_models 包含未知项：" + ", ".join(unknown))
    selected = str(profiles.get(normalized_profile) or "").strip()
    if selected:
        return selected
    provider = config.get("provider") or {}
    if not isinstance(provider, dict):
        raise ConfigError("provider 必须是对象")
    fallback = str(provider.get("model") or "").strip()
    if not fallback:
        raise ConfigError("子代理未配置专用模型，且 provider.model 为空")
    return fallback


def resolve_capability_model(config: dict[str, Any], capability: str) -> str:
    """Resolve a dedicated multimodal model or use the configured default model."""

    name = str(capability or "").strip()
    if name not in MULTIMODAL_CAPABILITIES:
        raise ConfigError(f"未知多模态能力：{name!r}")
    models = config.get("multimodal_models") or {}
    if not isinstance(models, dict):
        raise ConfigError("multimodal_models 必须是对象")
    unknown = sorted(set(models) - MULTIMODAL_CAPABILITIES)
    if unknown:
        raise ConfigError("multimodal_models 包含未知项：" + ", ".join(unknown))
    selected = str(models.get(name) or "").strip()
    if selected:
        return selected
    provider = config.get("provider") or {}
    if not isinstance(provider, dict):
        raise ConfigError("provider 必须是对象")
    default_model = str(provider.get("model") or "").strip()
    if not default_model:
        raise ConfigError(f"{name} 未配置专用模型，且 provider.model 为空")
    return default_model
