"""Configuration loading for kemo-agent.

Global defaults are merged with one user's overrides.  Secret values are
resolved at runtime from environment variables and are never written back.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    """Configuration is missing or malformed."""


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


def load_config(user: str, root: Path | None = None) -> dict[str, Any]:
    base = root or project_root()
    from run.users import user_dir

    global_config = read_json_object(base / "config" / "global_config.json")
    user_config = read_json_object(
        user_dir(user, base) / "user_config.json", allow_empty=True
    )
    merged = deep_merge(global_config, user_config)
    merged["user"] = user
    return merged


def provider_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    provider = copy.deepcopy(config.get("provider") or {})
    provider_type = str(provider.get("type") or "").strip().lower()
    if provider_type not in {"openai", "kemo"}:
        raise ConfigError("provider.type 必须是 'openai' 或 'kemo'")

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

    base_url = str(provider.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        base_url = (
            "http://127.0.0.1:8741/v1"
            if provider_type == "kemo"
            else "https://api.openai.com/v1"
        )
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    provider.update(
        {
            "type": provider_type,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "timeout": float(provider.get("timeout", 120)),
            "stream": bool(provider.get("stream", False)),
        }
    )
    return provider
