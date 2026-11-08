
"""kemo-agent 的配置加载。

全局默认值与一个用户的覆盖值合并。  秘密值是
在运行时从环境变量解析并且永远不会写回。"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
import yaml
from run.infra import cached_read_text, record_runtime_event

from provider.protocol.models import (
    normalize_kemo_reasoning_effort,
    normalize_reasoning_effort,
)


USER_ONLY_SECTIONS = frozenset(
    {
        "provider",
        "agent_models",
        "multimodal_models",
        "multimodal_routing",
        "knowledge",
        "skills",
        "expand",
        "perception",
        "plugins",
        "web_ui",
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
        "video_understanding",
        "video_generation",
    }
)
AGENT_MODEL_PROFILES = frozenset({"default", "cheap", "reasoning"})
SYSTEM_UPDATE_RATE_KEYS = frozenset({"sense_update_rate", "expand_update_rate"})
PROVIDER_API_KEY_ENVS = frozenset({
    "KEMO_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
})
PROVIDER_BUILTIN_HOSTS = frozenset({
    "localhost", "127.0.0.1", "api.openai.com", "api.deepseek.com", "api.anthropic.com",
})
_PROVIDER_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1"})


class ConfigError(RuntimeError):
    """Configuration is missing or malformed."""


class _ConfigYamlLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ConfigError("YAML 配置键必须是字符串且不能重复")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _parse_yaml_mapping(text: str, path: Path, *, allow_empty: bool = False) -> dict[str, Any]:
    try:
        value = yaml.load(text, Loader=_ConfigYamlLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f"第 {mark.line + 1} 行、第 {mark.column + 1} 列" if mark else "语法错误"
        raise ConfigError(f"配置文件 YAML 无效：{path}（{location}）") from None
    if value is None and allow_empty:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"配置文件根节点必须是映射：{path}")
    return value


def validate_provider_security(config: dict[str, Any]) -> None:
    """Validate only routing/credential selectors, even before keys/models exist.

    ``config`` must be instance-merged: merge_user_config drops user host policy.
    This boundary does not resolve DNS or replace transport/network isolation.
    """
    policy = config.get("provider_security", {})
    if not isinstance(policy, dict) or not isinstance(policy.get("allowed_hosts", []), list):
        raise ConfigError("provider_security.allowed_hosts 必须是实例级精确主机名数组")
    hosts = set(PROVIDER_BUILTIN_HOSTS)
    for host in policy.get("allowed_hosts", []):
        if (
            not isinstance(host, str) or len(host) > 253
            or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in host.casefold().split("."))
        ):
            raise ConfigError(
                "provider_security.allowed_hosts 只接受精确主机名；不含协议、端口、路径或通配符"
            )
        hosts.add(host.casefold())
    provider = config.get("provider") or {}
    if not isinstance(provider, dict):
        raise ConfigError("provider 必须是对象")
    env_name = str(provider.get("api_key_env") or "").strip()
    if env_name and env_name not in PROVIDER_API_KEY_ENVS:
        raise ConfigError(
            "provider.api_key_env 不在允许列表：" + ", ".join(sorted(PROVIDER_API_KEY_ENVS))
            + "；请将 Provider 密钥迁移到允许的环境变量并更新别名，不要复制其他服务凭据"
        )
    url = str(provider.get("base_url") or "")
    if not url:
        return
    host = ""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
        valid = (
            not any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url)
            and "\\" not in url and parts.username is None and parts.password is None
            and bool(host) and (port is None or 1 <= port <= 65535)
            and (parts.scheme == "https" or (parts.scheme == "http" and host in _PROVIDER_HTTP_HOSTS))
        )
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError("provider.base_url 必须是有效 https 地址（仅 localhost/127.0.0.1 允许 http），不得含用户凭据")
    if host not in hosts:
        raise ConfigError(
            "provider.base_url 的 host 未获实例授权；请由管理员在 config/global_config.yaml 的 "
            "provider_security.allowed_hosts 追加精确主机名，不可在用户配置中放行"
        )


def cron_history_retention_days(config: dict[str, Any]) -> int:
    """Global-only cleanup policy. Invalid values fail closed, never delete data."""
    cron = config.get("cron", {})
    if not isinstance(cron, dict):
        raise ConfigError("cron 必须是对象")
    value = cron.get("history_retention_days", 7)
    if type(value) is not int or not 0 <= value <= 3650:
        raise ConfigError("cron.history_retention_days 必须是 0 到 3650 的整数；0 表示永久保留")
    return value


def cron_session_idle_close_seconds(config: dict[str, Any]) -> int:
    """Global-only idle-session fallback threshold."""
    cron = config.get("cron", {})
    if not isinstance(cron, dict):
        raise ConfigError("cron 必须是对象")
    value = cron.get("session_idle_close_seconds", 90 * 60)
    if type(value) is not int or value < 3600:
        raise ConfigError("cron.session_idle_close_seconds 必须是至少 3600 秒的整数")
    return value


def dotenv_values(
    path: Path,
    *,
    max_bytes: int | None = None,
    max_entries: int | None = None,
    max_value_chars: int | None = None,
    duplicate_policy: str = "last",
    case_insensitive_names: bool = False,
) -> dict[str, str]:
    """Read simple ``KEY=VALUE`` entries without mutating the process.

    Keeping parsing separate from installation lets isolated module processes
    consume their own private ``.env`` without changing the Web process or a
    concurrently running module's environment.
    """

    if duplicate_policy not in {"first", "last", "error"}:
        raise ValueError("duplicate_policy 必须是 first、last 或 error")

    try:
        if max_bytes is None:
            text = path.read_text("utf-8-sig")
        else:
            if max_bytes <= 0:
                raise ValueError("max_bytes 必须大于 0")
            with path.open("rb") as handle:
                raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ConfigError(f"环境变量文件超过 {max_bytes} 字节上限：{path}")
            text = raw.decode("utf-8-sig")
    except FileNotFoundError:
        return {}
    except ConfigError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"环境变量文件不可读：{path}（{exc}）") from exc

    values: dict[str, str] = {}
    stored_names: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
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
        if "\x00" in value:
            raise ConfigError(f".env 第 {line_number} 行包含空字符")
        if max_value_chars is not None and len(value) > max_value_chars:
            raise ConfigError(
                f".env 第 {line_number} 行的值超过 {max_value_chars} 字符上限"
            )
        identity = name.casefold() if case_insensitive_names else name
        previous_name = stored_names.get(identity)
        if previous_name is not None:
            if duplicate_policy == "error":
                raise ConfigError(f".env 第 {line_number} 行重复定义变量 {name}")
            if duplicate_policy == "first":
                continue
            if previous_name != name:
                values.pop(previous_name, None)
        elif max_entries is not None and len(values) >= max_entries:
            raise ConfigError(f".env 变量数量超过 {max_entries} 项上限")
        stored_names[identity] = name
        values[name] = value
    return values


def load_kemo_yaml(path: Path, *, override: bool = False) -> None:
    """Load root environment settings from a YAML mapping."""
    try:
        text = path.read_text("utf-8-sig")
    except FileNotFoundError:
        return
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"kemo.yaml 无法读取：{path}") from exc
    values = _parse_yaml_mapping(text, path, allow_empty=True)

    normalized_names: set[str] = set()
    environment: dict[str, str] = {}
    for name, value in values.items():
        if (
            not isinstance(name, str)
            or not name
            or not name.replace("_", "a").isalnum()
            or name[0].isdigit()
        ):
            raise ConfigError(f"kemo.yaml 包含无效环境变量名：{name!r}")
        identity = name.casefold() if os.name == "nt" else name
        if identity in normalized_names:
            raise ConfigError(f"kemo.yaml 包含重复环境变量：{name}")
        normalized_names.add(identity)
        if value is None:
            text = ""
        elif isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            text = str(value)
        else:
            raise ConfigError(f"kemo.yaml 环境变量值必须是标量：{name}")
        if "\x00" in text:
            raise ConfigError(f"kemo.yaml 环境变量包含空字符：{name}")
        environment[name] = text
    for name, text in environment.items():
        if override or name not in os.environ:
            os.environ[name] = text


def project_root() -> Path:
    # This module lives at ``run/config/__init__.py``.  The project root is
    # therefore two directory levels above ``run``; using ``parents[1]``
    # would incorrectly return ``run/`` and hide version/config/user data.
    return Path(__file__).resolve().parents[2]


def read_json_object(path: Path, *, allow_empty: bool = False) -> dict[str, Any]:
    try:
        text = cached_read_text(path, "utf-8")
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


def read_yaml_object(path: Path, *, allow_empty: bool = False) -> dict[str, Any]:
    try:
        text = cached_read_text(path, "utf-8")
    except FileNotFoundError:
        if allow_empty:
            return {}
        raise ConfigError(f"配置文件不存在：{path}") from None
    if not text.strip():
        if allow_empty:
            return {}
        raise ConfigError(f"配置文件为空：{path}")
    return _parse_yaml_mapping(text, path, allow_empty=allow_empty)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def memory_prompt_injection(config: dict[str, Any]) -> bool:
    memory = config.get("memory") or {}
    if not isinstance(memory, dict):
        raise ConfigError("memory 必须是对象")
    value = memory.get("prompt_injection", True)
    if not isinstance(value, bool):
        raise ConfigError("memory.prompt_injection 必须是布尔值")
    return value


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
    # The user owns provider routing, never the instance destination allowlist.
    merged.pop("provider_security", None)
    if "provider_security" in global_config:
        merged["provider_security"] = copy.deepcopy(global_config["provider_security"])
    memory = merged.get("memory")
    if memory is None:
        memory = merged["memory"] = {}
    if not isinstance(memory, dict):
        raise ConfigError("memory 必须是对象")
    memory["prompt_injection"] = memory_prompt_injection(global_config)
    retention_days = cron_history_retention_days(global_config)
    idle_close_seconds = cron_session_idle_close_seconds(global_config)
    if isinstance(merged.get("cron", {}), dict):
        merged.setdefault("cron", {})["history_retention_days"] = retention_days
        merged.setdefault("cron", {})["session_idle_close_seconds"] = idle_close_seconds
    for section in USER_ONLY_SECTIONS:
        if section in user_config:
            merged[section] = copy.deepcopy(user_config[section])
        else:
            merged.pop(section, None)
    return merged


def validate_cardstream_settings(config: dict[str, Any]) -> None:
    import math
    agents = config.get("agents") or {}
    if not isinstance(agents, dict):
        raise ValueError("agents 必须是对象")
    for key, default in (("token_limit", 1000000), ("output_reserve", 10000)):
        if type(agents.get(key, default)) is not int or agents.get(key, default) < 1:
            raise ValueError(f"agents.{key} 必须是正整数")
    ratio = agents.get("token_compression_ratio", .3)
    if isinstance(ratio, bool) or not isinstance(ratio, (float, int)) or not 0 < ratio < 1:
        raise ValueError("agents.token_compression_ratio 必须在 0 和 1 之间")
    timeout = agents.get("compression_timeout_seconds", 180)
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("agents.compression_timeout_seconds 必须是有限正数")
    runtime = config.get("provider_runtime") or {}
    retries = runtime.get("max_retries", 5) if isinstance(runtime, dict) else None
    if type(retries) is not int or not 0 <= retries <= 5:
        raise ValueError("provider_runtime.max_retries 必须是 0..5 的整数")


def load_config(user: str, root: Path | None = None) -> dict[str, Any]:
    base = root or project_root()
    load_kemo_yaml(base / "kemo.yaml")
    from run.config import user_dir

    global_config = read_yaml_object(base / "config" / "global_config.yaml")
    user_config = read_yaml_object(
        user_dir(user, base) / "user_config.yaml", allow_empty=True
    )
    merged = merge_user_config(global_config, user_config)
    # Retired options have no runtime branch. Existing user files stay intact.
    for section, keys in {
        "agents": ("context_engine", "max_rounds", "conserved_rounds", "rounds_after_compression",
                   "enable_mid_context_compression", "compression_max_attempts"),
        "history": ("schema_version", "recent_full_rounds"),
        "memory": ("recovery_max_rounds_per_scan", "extraction_batch_rounds"),
        "expand": ("realtime_injection",),
        "perception": ("realtime_injection",),
    }.items():
        if isinstance(merged.get(section), dict):
            for key in keys:
                merged[section].pop(key, None)
    merged["user"] = user
    from run.memory import memory_domain_rules

    memory_domain_rules(merged)
    configured_expand_update_interval(merged)
    try:
        validate_cardstream_settings(merged)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    try:
        validate_provider_security(merged)
    except ConfigError:
        if "provider" in user_config:
            record_runtime_event(base, user, category="backend", name="provider_config_override", status="failed")
        raise
    if "provider" in user_config:
        record_runtime_event(base, user, category="backend", name="provider_config_override", status="success")
    from run.extensions import validate_multimodal_config

    validate_multimodal_config(merged)
    from run.config.web_ui import web_background, web_language

    try:
        language = web_language(merged, strict=True, allow_retired=True)
        web_background(merged, strict=True)
        if merged.get("web_ui", {}).get("language") == "ar":
            merged["web_ui"]["language"] = language
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if (merged.get("provider") or {}).get("type") == "chat":
        from provider.chat_profiles import warn_deprecated_chat_capabilities

        warn_deprecated_chat_capabilities(merged["provider"])
    return merged


def system_update_rate(config: dict[str, Any], key: str) -> int:
    """Return a validated framework-owned module refresh rate in seconds."""

    if key not in SYSTEM_UPDATE_RATE_KEYS:
        raise ConfigError(f"未知系统模块刷新配置：{key}")
    if key == "expand_update_rate":
        return configured_expand_update_interval(config)
    task_config = config.get("task_cron_system") or {}
    if not isinstance(task_config, dict):
        return 5
    value = task_config.get(key, 5)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 5
    return value


def configured_expand_update_interval(config: dict[str, Any]) -> int:
    """Read the canonical collector interval, accepting the old key as an alias."""
    section = config.get("cron", {})
    for key in ("task_cron_system", "expand_update"):
        if not isinstance(section, dict):
            raise ConfigError("cron.task_cron_system.expand_update.interval_seconds 必须是正整数")
        section = section.get(key, {})
    legacy = config.get("task_cron_system") or {}
    fallback = legacy.get("expand_update_rate", 30) if isinstance(legacy, dict) else 30
    if type(fallback) is not int or fallback < 1:
        fallback = 30
    value = section.get("interval_seconds", fallback) if isinstance(section, dict) else None
    if type(value) is not int or value < 1:
        raise ConfigError("cron.task_cron_system.expand_update.interval_seconds 必须是正整数")
    return value


def provider_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    validate_provider_security(config)
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
    validate_provider_security({**config, "provider": {**provider, "base_url": base_url}})
    # An explicit Chat URL may use /proxy/api, a complete /chat/completions
    # route or query parameters. Preserve those routes; a bare host retains
    # the established /v1 default used by Chat model discovery as well.
    if provider_type == "chat":
        parts = urlsplit(base_url)
        if not parts.path:
            base_url = urlunsplit((parts.scheme, parts.netloc, "/v1", parts.query, parts.fragment))

    provider.update(
        {
            "type": provider_type,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "timeout": float(provider.get("timeout", 120.0)),
            "stream": bool(provider.get("stream", True)),
            "reasoning_effort": (
                normalize_kemo_reasoning_effort(provider.get("reasoning_effort"))
                if provider_type == "kemo"
                else normalize_reasoning_effort(provider.get("reasoning_effort"))
            ),
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


_DOMAIN_MODULES = (
    "web_ui",
    "user_skeleton",
    "source_policy",
    "users",
    "prompt_source_helpers",
    "prompt_sources",
    "markdown",
    "prompt",
)


def __getattr__(name: str) -> Any:
    from importlib import import_module

    for module_name in _DOMAIN_MODULES:
        module = import_module(f"run.config.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
