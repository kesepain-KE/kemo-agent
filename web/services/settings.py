"""配置、版本、Prompt 诊断和界面偏好领域服务。"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import time
from typing import Any
import urllib.error
import urllib.request

from provider.adapters.gateway import KemoGatewayAdapter
from provider.protocol.models import (
    normalize_kemo_reasoning_effort,
    normalize_reasoning_effort,
)
from provider.schema import ProviderError
from run.config import (
    ConfigError,
    USER_ONLY_SECTIONS,
    load_config,
    provider_runtime_config,
    read_json_object,
)
from run.extensions import (
    clear_model_capability_cache,
    lookup_model_capabilities,
    retain_model_capability_cache,
)
from run.config import (
    DEFAULT_IMPORTANT_MEMORY_MAX_CHARS,
    PROMPT_SECTION_ORDER,
    build_prompt_bundle,
)
from run.config import MainAgentSourcePolicy
from update._utils import UpdateError, compare_versions, parse_version
from web.constants import (
    VERSION_CHECK_CACHE_SECONDS,
    VERSION_CHECK_TIMEOUT_SECONDS,
    VERSION_MANIFEST_URL,
    _BEIJING,
    _REDACTED,
    _SENSITIVE_CONFIG_KEYS,
    _VERSION_COMPONENT_IDS,
)
from web.errors import (
    InvalidRequestError,
    ProviderAccessError,
    ProviderCapabilityError,
    ProviderDiscoveryError,
    ProviderModelUnavailableError,
)
from web.services._io import atomic_write as _atomic_write


class _VersionCheckFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fetch_remote_version_manifest(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "kemo-agent-web-version-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _VersionCheckFailure(
                "remote_manifest_missing",
                "云端 version.json 不存在，请检查发布分支是否完整。",
            ) from exc
        raise _VersionCheckFailure(
            "remote_http_error",
            f"GitHub 返回 HTTP {exc.code}，请稍后重新检查。",
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise _VersionCheckFailure(
            "remote_timeout",
            "连接 GitHub 超时，请检查服务器网络后重试。",
        ) from exc
    except urllib.error.URLError as exc:
        raise _VersionCheckFailure(
            "remote_unreachable",
            "无法连接 GitHub，请检查服务器网络或代理设置。",
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise _VersionCheckFailure(
            "remote_unreachable",
            "读取云端版本信息失败，请检查服务器网络后重试。",
        ) from exc
    except json.JSONDecodeError as exc:
        raise _VersionCheckFailure(
            "invalid_remote_manifest",
            "云端 version.json 格式错误，暂时无法比较版本。",
        ) from exc
    if not isinstance(payload, dict):
        raise _VersionCheckFailure(
            "invalid_remote_manifest",
            "云端 version.json 不是有效对象，暂时无法比较版本。",
        )
    return payload


def _positive_float_or_default(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else default


_CONFIG_SOURCE_PATHS = (
    "provider.type",
    "provider.base_url",
    "provider.model",
    "provider.stream",
    "tools.enabled",
    "tools.max_iterations",
    "tools.consecutive_identical_call_limit",
    "tools.invalid_tool_arguments_retries",
    "tools.timeout",
    "memory.extraction_mode",
    "memory.history_read_enabled",
    "memory.temporary_injection_limits.half_year",
    "memory.temporary_injection_limits.one_month",
    "memory.temporary_injection_limits.seven_days",
    "memory.important_memory_max_chars",
    "task_plan.auto_accept",
    "task_plan.max_steps",
    "cron.enabled",
    "provider_runtime.max_concurrent_requests",
    "provider_runtime.request_semaphore_timeout",
    "web.max_concurrent_chats",
    "web.max_pending_chats",
    "web.pending_chat_timeout",
    "message.max_queued_messages",
    "agent_runtime.queue_maxsize",
    "cron.avoid_congestion",
    "cron.congestion_threshold_ratio",
    "runtime_host.enable_background_scheduler",
    "agents.max_rounds",
    "agents.token_limit",
    "agents.token_compression_ratio",
    "skills.shared_whitelist",
    "plugins.whitelist",
    "expand.global_whitelist",
    "expand.shared_whitelist",
    "expand.prompt_injection",
    "expand.realtime_injection",
    "perception.global_whitelist",
    "perception.prompt_injection",
    "perception.realtime_injection",
)


def _contains_redacted_placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_redacted_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_redacted_placeholder(item) for item in value)
    return value == _REDACTED


def _merge_patch(target: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    result = dict(target)
    for key, value in changes.items():
        if not isinstance(key, str) or not key:
            raise InvalidRequestError("配置字段名必须是非空字符串")
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_patch(result[key], value)
        else:
            result[key] = value
    return result


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in _SENSITIVE_CONFIG_KEYS or lowered.endswith("_secret")


def _redact_config(value: Any, path: tuple[str, ...] = ()) -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        redacted: list[str] = []
        for key, item in value.items():
            rendered = str(key)
            current = (*path, rendered)
            if _is_sensitive_key(rendered):
                result[rendered] = _REDACTED
                redacted.append(".".join(current))
                continue
            clean, nested = _redact_config(item, current)
            result[rendered] = clean
            redacted.extend(nested)
        return result, redacted
    if isinstance(value, list):
        result = []
        redacted: list[str] = []
        for index, item in enumerate(value):
            clean, nested = _redact_config(item, (*path, str(index)))
            result.append(clean)
            redacted.extend(nested)
        return result, redacted
    return value, []


class SettingsServiceMixin:
    def _config_path(self, user: str) -> Path:
        return self.root / "users" / user / "user_config.json"

    @staticmethod
    def _has_path(value: dict[str, Any], dotted: str) -> bool:
        current: Any = value
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return True

    def _config_provenance(self, user: str) -> dict[str, str]:
        global_config = read_json_object(self.root / "config" / "global_config.json")
        user_config = read_json_object(self._config_path(user), allow_empty=True)
        return {
            dotted: (
                "user"
                if self._has_path(user_config, dotted)
                else "global"
                if (
                    dotted.split(".", 1)[0] not in USER_ONLY_SECTIONS
                    and self._has_path(global_config, dotted)
                )
                else "default"
            )
            for dotted in _CONFIG_SOURCE_PATHS
        }

    def user_config(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        path = self._config_path(name)
        config = read_json_object(path, allow_empty=True)
        redacted, redacted_paths = _redact_config(config)
        return {
            "user": name,
            "config": redacted,
            "redacted_paths": redacted_paths,
        }

    def global_config(self) -> dict[str, Any]:
        path = self.root / "config" / "global_config.json"
        config = read_json_object(path)
        redacted, redacted_paths = _redact_config(config)
        return {
            "scope": "global",
            "config": redacted,
            "redacted_paths": redacted_paths,
        }

    def _patch_config_document(
        self,
        path: Path,
        changes: Any,
        *,
        user: str | None,
    ) -> dict[str, Any]:
        if not isinstance(changes, dict) or not changes:
            raise InvalidRequestError("changes 必须是非空对象")
        if _contains_redacted_placeholder(changes):
            raise InvalidRequestError("不能把脱敏占位符 *** 写回配置")
        current = read_json_object(path, allow_empty=user is not None)
        updated = _merge_patch(current, changes)
        encoded = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        previous = path.read_bytes() if path.is_file() else None
        _atomic_write(path, encoded)
        try:
            if user is not None:
                load_config(user, self.root)
            else:
                # Validate the global document through one concrete user when possible.
                available = self.users()
                if available:
                    load_config(available[0]["name"], self.root)
        except Exception as exc:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, previous)
            raise InvalidRequestError(f"配置校验失败：{exc}") from None
        redacted, redacted_paths = _redact_config(updated)
        response: dict[str, Any] = {
            "config": redacted,
            "redacted_paths": redacted_paths,
            "updated": True,
        }
        if user is None:
            response["scope"] = "global"
        else:
            response["user"] = user
        return response

    def patch_user_config(self, user: Any, changes: Any) -> dict[str, Any]:
        name = self.require_user(user)
        result = self._patch_config_document(self._config_path(name), changes, user=name)
        provider_changes = changes.get("provider") if isinstance(changes, dict) else None
        if isinstance(provider_changes, dict) and {
            "type",
            "base_url",
            "api_key",
            "api_key_env",
        }.intersection(provider_changes):
            self._clear_kemo_catalog_cache(name)
            clear_model_capability_cache()
        return result

    def patch_global_config(self, changes: Any) -> dict[str, Any]:
        return self._patch_config_document(
            self.root / "config" / "global_config.json",
            changes,
            user=None,
        )

    @staticmethod
    def _kemo_catalog_key(
        user: str,
        runtime_provider: dict[str, Any],
    ) -> tuple[str, str, str]:
        secret_fingerprint = hashlib.sha256(
            str(runtime_provider.get("api_key") or "").encode("utf-8")
        ).hexdigest()
        return (
            user,
            str(runtime_provider.get("base_url") or "").rstrip("/"),
            secret_fingerprint,
        )

    def _clear_kemo_catalog_cache(self, user: str) -> None:
        with self._kemo_catalog_lock:
            for key in list(self._kemo_catalog_cache):
                if key[0] == user:
                    self._kemo_catalog_cache.pop(key, None)

    def _kemo_catalog(
        self,
        user: str,
        *,
        refresh: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], KemoGatewayAdapter, Any]:
        config = load_config(user, self.root)
        configured_provider = config.get("provider") or {}
        if str(configured_provider.get("type") or "").strip().casefold() != "kemo":
            raise InvalidRequestError("只有已保存的 Kemo 私有协议配置允许查询模型能力")
        runtime_provider = provider_runtime_config(config)
        adapter = KemoGatewayAdapter(runtime_provider)
        cache_key = self._kemo_catalog_key(user, runtime_provider)
        now = time.monotonic()
        with self._kemo_catalog_lock:
            for key in list(self._kemo_catalog_cache):
                if key[0] == user and key != cache_key:
                    self._kemo_catalog_cache.pop(key, None)
            cached = self._kemo_catalog_cache.get(cache_key)
            if not refresh and cached is not None and cached[0] > now:
                return config, runtime_provider, adapter, cached[1]
        catalog = adapter.models(task="llm")
        with self._kemo_catalog_lock:
            self._kemo_catalog_cache[cache_key] = (now + 300.0, catalog)
        retain_model_capability_cache(
            runtime_provider,
            {item.id for item in catalog.data},
        )
        return config, runtime_provider, adapter, catalog

    def kemo_provider_models(
        self,
        user: Any,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Discover LLMs through persisted Kemo credentials without exposing them."""

        name = self.require_user(user)
        try:
            _, _, _, catalog = self._kemo_catalog(name, refresh=bool(refresh))
        except InvalidRequestError:
            raise
        except (ConfigError, ProviderError, ValueError) as exc:
            raise ProviderDiscoveryError("Kemo API 验证失败，未拉取模型") from exc
        return {
            "user": name,
            "protocol": "kemo",
            "api_valid": True,
            "count": catalog.count,
            "data": [item.model_dump(mode="json") for item in catalog.data],
        }

    @staticmethod
    def _raise_capability_lookup_error(error: BaseException | None) -> None:
        if isinstance(error, ProviderError):
            if error.status_code in {401, 403}:
                raise ProviderAccessError(
                    "Kemo API 密钥无效或没有读取该模型能力的权限",
                    status=int(error.status_code),
                ) from error
            if error.status_code == 404:
                raise ProviderModelUnavailableError(
                    "模型不存在、已禁用，或不在当前密钥的白名单和任务权限内"
                ) from error
            if error.status_code == 502 or error.category == "gateway_protocol_error":
                raise ProviderCapabilityError(
                    "Kemo Provider 的模型能力声明无效"
                ) from error
        raise ProviderCapabilityError(
            "Kemo 模型能力暂时无法读取，请检查网关连接后重试"
        ) from error

    def kemo_provider_model_capabilities(
        self,
        user: Any,
        model: Any,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        name = self.require_user(user)
        selected_model = str(model or "").strip()
        if (
            not selected_model
            or len(selected_model) > 256
            or any(ord(character) < 32 for character in selected_model)
        ):
            raise InvalidRequestError("model 必须是 1–256 字符的非空模型标识")
        try:
            config, runtime_provider, adapter, catalog = self._kemo_catalog(
                name,
                refresh=False,
            )
        except InvalidRequestError:
            raise
        except (ConfigError, ProviderError, ValueError) as exc:
            self._raise_capability_lookup_error(exc)
            raise AssertionError("unreachable")
        entry = next(
            (
                item
                for item in catalog.data
                if item.id == selected_model and item.task == "llm"
            ),
            None,
        )
        if entry is None:
            raise ProviderModelUnavailableError(
                "模型不存在、已禁用，或不在当前密钥的 LLM 白名单内"
            )
        if not entry.capabilities_available:
            raise ProviderModelUnavailableError("网关未为该模型提供能力声明")
        lookup = lookup_model_capabilities(
            config,
            runtime_provider,
            adapter,
            model=selected_model,
            capabilities_url=entry.capabilities_url,
            force_refresh=bool(refresh),
        )
        if lookup.capabilities is None:
            self._raise_capability_lookup_error(lookup.error)
            raise AssertionError("unreachable")
        return {
            "user": name,
            "protocol": "kemo",
            "api_valid": True,
            "model": selected_model,
            "stale": lookup.stale,
            "warning": (
                "能力信息暂时无法刷新，当前显示上一次成功结果"
                if lookup.stale
                else ""
            ),
            "capabilities": lookup.capabilities.model_dump(mode="json"),
        }

    def preferences(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        path = self.root / "users" / name / "web_preferences.json"
        value = read_json_object(path, allow_empty=True)
        appearance = value.get("appearance") if isinstance(value.get("appearance"), dict) else {}
        return {
            "user": name,
            "appearance": {
                "theme": appearance.get("theme", "light"),
                "font_size": appearance.get("font_size", "medium"),
            },
        }

    def patch_preferences(self, user: Any, changes: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(changes, dict):
            raise InvalidRequestError("appearance 必须是对象")
        theme = changes.get("theme", self.preferences(name)["appearance"]["theme"])
        font_size = changes.get(
            "font_size", self.preferences(name)["appearance"]["font_size"]
        )
        if theme not in {"light", "dark"}:
            raise InvalidRequestError("theme 只允许 light 或 dark")
        if font_size not in {"small", "medium", "large"}:
            raise InvalidRequestError("font_size 只允许 small、medium 或 large")
        value = {"schema_version": 1, "appearance": {"theme": theme, "font_size": font_size}}
        path = self.root / "users" / name / "web_preferences.json"
        _atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
        return {"user": name, "appearance": value["appearance"], "updated": True}

    def settings(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        global_config = read_json_object(
            self.root / "config" / "global_config.json"
        )
        global_history = global_config.get("history") or {}
        global_memory = global_config.get("memory") or {}
        source_policy = MainAgentSourcePolicy.from_config(config)
        provider = config.get("provider") or {}
        env_name = str(provider.get("api_key_env") or "")
        inline_key = bool(str(provider.get("api_key") or "").strip())
        environment_key = bool(env_name and os.getenv(env_name, "").strip())
        credential_source = "inline" if inline_key else "environment" if environment_key else "missing"
        tools = config.get("tools") or {}
        memory = config.get("memory") or {}
        temporary_memory_limits = memory.get("temporary_injection_limits") or {}
        task_plan = config.get("task_plan") or {}
        cron = config.get("cron") or {}
        provider_runtime = config.get("provider_runtime") or {}
        web_config = config.get("web") or {}
        message_config = config.get("message") or {}
        agent_runtime = config.get("agent_runtime") or {}
        runtime_host = config.get("runtime_host") or {}
        agents = config.get("agents") or {}
        provider_timeout = _positive_float_or_default(
            provider.get("timeout"),
            120.0,
        )
        return {
            "user": name,
            "schema_version": int(config.get("schema_version") or 1),
            "schema_versions": {
                "config_schema": int(global_config.get("schema_version") or 1),
                "history_schema": int(global_history.get("schema_version") or 1),
                "memory_storage_schema": int(
                    global_memory.get("storage_schema_version") or 1
                ),
            },
            "provider": {
                "type": str(provider.get("type") or ""),
                "base_url": str(provider.get("base_url") or ""),
                "model": str(provider.get("model") or ""),
                "reasoning_effort": (
                    normalize_kemo_reasoning_effort(provider.get("reasoning_effort"))
                    if str(provider.get("type") or "").strip().casefold() == "kemo"
                    else normalize_reasoning_effort(provider.get("reasoning_effort"))
                ),
                "timeout": provider_timeout,
                "stream": bool(provider.get("stream", True)),
                "credential_source": credential_source,
                "configured": bool(provider.get("type") and provider.get("model") and provider.get("base_url")),
            },
            "features": {
                "tools": bool(tools.get("enabled", True)),
                "knowledge": True,
                "history_read": bool(memory.get("history_read_enabled", True)),
                "memory_injection": True,
                "task_plan_auto_accept": bool(task_plan.get("auto_accept", False)),
                "cron": bool(cron.get("enabled", False)),
                "background_scheduler": bool(
                    runtime_host.get("enable_background_scheduler", True)
                ),
                "perception_realtime_injection": (
                    source_policy.perception_realtime_injection
                ),
                "perception_prompt_injection": (
                    source_policy.perception_prompt_injection
                ),
                "expand_realtime_injection": (
                    source_policy.expand_realtime_injection
                ),
                "expand_prompt_injection": (
                    source_policy.expand_prompt_injection
                ),
            },
            "limits": {
                "context_rounds": int(agents.get("max_rounds") or 30),
                "context_tokens": int(agents.get("token_limit") or 120000),
                "compression_ratio": float(
                    agents.get("token_compression_ratio") or 0.6
                ),
                "task_plan_steps": int(task_plan.get("max_steps") or 10),
                "tool_iterations": int(tools.get("max_iterations") or 80),
                "tool_timeout": float(tools.get("timeout") or 60),
                "tool_argument_retries": int(
                    tools.get("invalid_tool_arguments_retries", 2)
                ),
                "memory_items": sum(
                    int(temporary_memory_limits.get(tier, default))
                    for tier, default in (
                        ("half_year", 300),
                        ("one_month", 200),
                        ("seven_days", 100),
                    )
                ),
                "memory_chars": int(
                    memory.get(
                        "important_memory_max_chars",
                        DEFAULT_IMPORTANT_MEMORY_MAX_CHARS,
                    )
                ),
                "provider_max_concurrent": int(
                    provider_runtime.get("max_concurrent_requests", 10)
                ),
                "web_max_chats": int(web_config.get("max_concurrent_chats", 3)),
                "message_max_queued": int(
                    message_config.get("max_queued_messages", 20)
                ),
                "agent_queue_maxsize": int(agent_runtime.get("queue_maxsize", 50)),
            },
            "users": [item["name"] for item in self.users()],
            "source_policy": source_policy.public_summary(),
            "provenance": self._config_provenance(name),
        }

    def version_info(self) -> dict[str, Any]:
        """Return a presentation-safe, read-only view of version.json."""

        path = self.root / "version.json"
        try:
            raw = json.loads(path.read_text("utf-8-sig"))
        except FileNotFoundError:
            raw = {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw_components = raw.get("components")
        if not isinstance(raw_components, dict):
            raw_components = {}
        preferred = ("core", "agents", "plugins", "web")
        component_ids = [
            *[key for key in preferred if key in raw_components],
            *sorted(key for key in raw_components if key not in preferred),
        ]
        components: list[dict[str, str]] = []
        for component_id in component_ids:
            value = raw_components.get(component_id)
            if not isinstance(value, dict):
                continue
            components.append(
                {
                    "id": str(component_id),
                    "version": str(value.get("version") or "").strip(),
                    "description": str(value.get("description") or "").strip(),
                }
            )
        schema_version = raw.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            schema_version = 0
        return {
            "name": str(raw.get("name") or "kemo-agent").strip() or "kemo-agent",
            "version": str(raw.get("version") or "").strip(),
            "schema_version": schema_version,
            "components": components,
            "read_only": True,
        }

    def version_check(self, *, refresh: bool = False) -> dict[str, Any]:
        """Compare local and published manifests without changing the installation."""

        now = time.monotonic()
        with self._version_check_lock:
            if not refresh and self._version_check_cache is not None:
                cached_at, cached = self._version_check_cache
                if now - cached_at < VERSION_CHECK_CACHE_SECONDS:
                    return cached

            checked_at = datetime.now(_BEIJING).isoformat(timespec="seconds")
            local = self.version_info()
            try:
                local_version = str(local.get("version") or "").strip()
                parse_version(local_version)
                local_components = {
                    str(item.get("id") or ""): item
                    for item in local.get("components") or []
                    if isinstance(item, dict)
                }
                for component_id in _VERSION_COMPONENT_IDS:
                    item = local_components.get(component_id)
                    if not isinstance(item, dict):
                        raise UpdateError(f"version.json 缺少 components.{component_id}")
                    parse_version(str(item.get("version") or "").strip())

                remote = self.version_manifest_fetcher(
                    VERSION_MANIFEST_URL,
                    VERSION_CHECK_TIMEOUT_SECONDS,
                )
                remote_version = str(remote.get("version") or "").strip()
                parse_version(remote_version)
                remote_components = remote.get("components")
                if not isinstance(remote_components, dict):
                    raise UpdateError("version.json 缺少 components")

                components: list[dict[str, str]] = []
                has_component_update = False
                has_component_ahead = False
                for component_id in _VERSION_COMPONENT_IDS:
                    remote_item = remote_components.get(component_id)
                    if not isinstance(remote_item, dict):
                        raise UpdateError(f"version.json 缺少 components.{component_id}")
                    local_item = local_components[component_id]
                    local_component_version = str(local_item.get("version") or "").strip()
                    remote_component_version = str(remote_item.get("version") or "").strip()
                    parse_version(remote_component_version)
                    comparison = compare_versions(
                        local_component_version,
                        remote_component_version,
                    )
                    component_status = (
                        "update_available" if comparison < 0
                        else "local_newer" if comparison > 0
                        else "up_to_date"
                    )
                    has_component_update = has_component_update or comparison < 0
                    has_component_ahead = has_component_ahead or comparison > 0
                    components.append(
                        {
                            "id": component_id,
                            "description": str(local_item.get("description") or component_id),
                            "local_version": local_component_version,
                            "remote_version": remote_component_version,
                            "status": component_status,
                        }
                    )

                overall_comparison = compare_versions(local_version, remote_version)
                if overall_comparison < 0 or has_component_update:
                    status = "update_available"
                elif overall_comparison > 0 or has_component_ahead:
                    status = "local_newer"
                else:
                    status = "up_to_date"

                python_command = "python" if os.name == "nt" else "python3"
                module_commands = {
                    component_id: f"{python_command} update.py --module {component_id}"
                    for component_id in _VERSION_COMPONENT_IDS
                }
                result: dict[str, Any] = {
                    "status": status,
                    "checked_at": checked_at,
                    "local_version": local_version,
                    "remote_version": remote_version,
                    "components": components,
                    "commands": {
                        "check": f"{python_command} update.py --check",
                        "all": f"{python_command} update.py --module all",
                        "recommended": f"{python_command} update.py --module all",
                        "modules": module_commands,
                    },
                    "source": VERSION_MANIFEST_URL,
                    "read_only": True,
                }
            except _VersionCheckFailure as exc:
                result = {
                    "status": "check_failed",
                    "checked_at": checked_at,
                    "error": {"code": exc.code, "message": str(exc)},
                    "read_only": True,
                }
            except UpdateError as exc:
                result = {
                    "status": "check_failed",
                    "checked_at": checked_at,
                    "error": {
                        "code": "invalid_version_manifest",
                        "message": f"版本文件格式不完整：{exc}",
                    },
                    "read_only": True,
                }

            self._version_check_cache = (now, result)
            return result

    def prompt_sections(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        bundle = build_prompt_bundle(self.root, name, config)
        selected = bundle.diagnostics.get("sections") or {}
        sections = []
        for section_name in PROMPT_SECTION_ORDER:
            detail = selected.get(section_name)
            status = (
                "disabled"
                if isinstance(detail, dict) and detail.get("mode") == "disabled"
                else "injected"
                if isinstance(detail, dict)
                else "omitted"
            )
            sections.append(
                {
                    "name": section_name,
                    "status": status,
                    "original_items": int((detail or {}).get("original_items") or 0),
                    "injected_items": int((detail or {}).get("injected_items") or 0),
                    "original_chars": int((detail or {}).get("original_chars") or 0),
                    "injected_chars": int((detail or {}).get("injected_chars") or 0),
                    "truncated": bool((detail or {}).get("truncated", False)),
                    "source_files": list((detail or {}).get("source_files") or []),
                }
            )
        source_selection = bundle.diagnostics.get("source_selection") or {}
        return {
            "user": name,
            "total_chars": int(bundle.diagnostics.get("total_chars") or 0),
            "sections": sections,
            "source_policy": bundle.diagnostics.get("source_policy") or {},
            "source_selection": source_selection,
            "expand": source_selection.get("expand") or {},
        }
