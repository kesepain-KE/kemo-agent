"""Local-only registry and path validation for Kemo Graph libraries."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import os
import re
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from errors import GraphExpandError


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "graph_config.json"
MANIFEST_PATH = BASE_DIR / "expand.json"
INPUT_PATH = BASE_DIR / "input_data.md"
LAST_RUN_PATH = BASE_DIR / "_last_run.json"
DATA_DIR = BASE_DIR / "data"
STATUS_PATH = DATA_DIR / "library_status.json"
SYNC_STATE_PATH = DATA_DIR / "library_sync_state.json"
QUERY_ARTIFACT_DIR = BASE_DIR / "artifacts" / "queries"

LIBRARY_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
LIBRARY_KINDS = frozenset({"portable", "service_default"})
STORE_SCOPES = frozenset({
    "knowledge.global",
    "knowledge.shared",
    "knowledge.user",
    "memory.temporary",
    "memory.important",
    "memory.permanent",
    "memory.user",
})


@dataclass(frozen=True, slots=True)
class GraphLibrary:
    id: str
    kind: str
    display_name: str
    enabled: bool = True
    store_root: str | None = None
    source_roots: tuple[str, ...] = ()
    scope: str | None = None
    owner_id: str | None = None
    allowed_users: tuple[str, ...] = ()

    def allows(self, user: str | None) -> bool:
        if user is None:
            return True
        return "*" in self.allowed_users or user in self.allowed_users

    @property
    def public_for_all_users(self) -> bool:
        return "*" in self.allowed_users

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "store_root": self.store_root,
            "source_roots": list(self.source_roots),
            "scope": self.scope,
            "owner_id": self.owner_id,
            "allowed_users": list(self.allowed_users),
        }


@dataclass(frozen=True, slots=True)
class GraphConfig:
    base_url: str
    admin_users: tuple[str, ...]
    allow_remote: bool = False
    timeout_seconds: int = 15
    ingest_timeout_seconds: int = 1800
    libraries: tuple[GraphLibrary, ...] = ()

    def is_admin(self, user: str | None) -> bool:
        return user is None or user in self.admin_users


def atomic_text(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text("utf-8") == content:
            return False
    except (OSError, UnicodeError):
        pass
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value: Any) -> bool:
    return atomic_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
    )


def integer(
    value: Any,
    *,
    field: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise GraphExpandError(f"{field} 必须是整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GraphExpandError(f"{field} 必须是整数") from exc
    if result < minimum or result > maximum:
        raise GraphExpandError(f"{field} 必须在 {minimum}..{maximum} 之间")
    return result


def _user_list(
    value: Any,
    *,
    field: str,
    allow_wildcard: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise GraphExpandError(f"{field} 必须是非空用户数组")
    users: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise GraphExpandError(f"{field}[{index}] 必须是字符串")
        user = item.strip()
        if (
            not user
            or (user == "*" and not allow_wildcard)
            or len(user) > 128
            or any(character in user for character in ("/", "\\"))
            or any(ord(character) < 32 or ord(character) == 127 for character in user)
        ):
            raise GraphExpandError(f"{field}[{index}] 不是合法用户标识")
        if user not in users:
            users.append(user)
    return tuple(users)


def _is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def normalized_base_url(value: Any, *, allow_remote: bool) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        raise GraphExpandError("缺少 kemo-graph base_url")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in raw
    ):
        raise GraphExpandError("base_url 不允许包含空白符或控制字符")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise GraphExpandError("base_url 必须是有效的 http 或 https 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GraphExpandError("base_url 不允许包含凭据、查询参数或片段")
    if not allow_remote and not _is_loopback(parsed.hostname):
        raise GraphExpandError("非回环地址必须显式设置 allow_remote=true")
    if allow_remote and not _is_loopback(parsed.hostname) and parsed.scheme != "https":
        raise GraphExpandError("远程 kemo-graph 地址必须使用 https")
    path = parsed.path.rstrip("/")
    if not path:
        path = "/api/v1"
    elif not path.endswith("/api/v1"):
        path = f"{path}/api/v1"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        return bool(isjunction and isjunction(path))
    except OSError:
        return True


def _has_link_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if _is_link(current):
            return True
    return False


def _absolute_directory(value: Any, *, field: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise GraphExpandError(f"{field} 不能为空")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise GraphExpandError(f"{field} 必须是绝对路径")
    if _has_link_component(candidate):
        raise GraphExpandError(f"{field} 及其父路径不能包含符号链接或目录联接")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise GraphExpandError(f"{field} 不存在或无法访问：{raw}") from exc
    if not resolved.is_dir():
        raise GraphExpandError(f"{field} 必须是已存在目录")
    return resolved


def _is_nested(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        return False


def _library_from_mapping(
    value: Any,
    *,
    index: int,
    default_users: tuple[str, ...],
) -> GraphLibrary:
    if not isinstance(value, dict):
        raise GraphExpandError(f"libraries[{index}] 必须是对象")
    unknown = sorted(
        set(value)
        - {
            "id",
            "kind",
            "display_name",
            "enabled",
            "store_root",
            "source_roots",
            "scope",
            "owner_id",
            "allowed_users",
        }
    )
    if unknown:
        raise GraphExpandError(
            f"libraries[{index}] 包含未知字段：{', '.join(unknown)}"
        )
    library_id = str(value.get("id") or "").strip().casefold()
    if not LIBRARY_ID_RE.fullmatch(library_id):
        raise GraphExpandError(
            f"libraries[{index}].id 必须匹配 {LIBRARY_ID_RE.pattern}"
        )
    kind = str(value.get("kind") or "portable").strip().casefold()
    if kind not in LIBRARY_KINDS:
        raise GraphExpandError(f"libraries[{index}].kind 只允许 portable 或 service_default")
    display_name = str(value.get("display_name") or "").strip()
    if not display_name or len(display_name) > 255:
        raise GraphExpandError(f"libraries[{index}].display_name 必须是 1..255 字符")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise GraphExpandError(f"libraries[{index}].enabled 必须是布尔值")
    allowed_users = (
        _user_list(
            value.get("allowed_users"),
            field=f"libraries[{index}].allowed_users",
        )
        if "allowed_users" in value
        else default_users
    )

    if kind == "service_default":
        forbidden = [
            key
            for key in ("store_root", "source_roots", "scope", "owner_id")
            if value.get(key) not in (None, "", [])
        ]
        if forbidden:
            raise GraphExpandError(
                f"service_default 库不能声明：{', '.join(forbidden)}"
            )
        return GraphLibrary(
            id=library_id,
            kind=kind,
            display_name=display_name,
            enabled=enabled,
            allowed_users=allowed_users,
        )

    store_root = _absolute_directory(
        value.get("store_root"), field=f"libraries[{index}].store_root"
    )
    raw_sources = value.get("source_roots", [])
    if not isinstance(raw_sources, list):
        raise GraphExpandError(f"libraries[{index}].source_roots 必须是绝对路径数组")
    source_roots: list[Path] = []
    for source_index, raw_source in enumerate(raw_sources):
        source = _absolute_directory(
            raw_source,
            field=f"libraries[{index}].source_roots[{source_index}]",
        )
        if source == store_root or _is_nested(source, store_root) or _is_nested(store_root, source):
            raise GraphExpandError("store_root 与 source_roots 不能相同或互相嵌套")
        if source not in source_roots:
            source_roots.append(source)
    scope = str(value.get("scope") or "knowledge.user").strip()
    if scope not in STORE_SCOPES:
        raise GraphExpandError(f"libraries[{index}].scope 不受 kemo-graph 支持")
    owner_raw = value.get("owner_id")
    owner_id = str(owner_raw).strip() if owner_raw not in (None, "") else None
    if owner_id is not None and len(owner_id) > 255:
        raise GraphExpandError(f"libraries[{index}].owner_id 不能超过 255 字符")
    return GraphLibrary(
        id=library_id,
        kind=kind,
        display_name=display_name,
        enabled=enabled,
        store_root=str(store_root),
        source_roots=tuple(str(path) for path in source_roots),
        scope=scope,
        owner_id=owner_id,
        allowed_users=allowed_users,
    )


def config_from_mapping(value: dict[str, Any]) -> GraphConfig:
    if not isinstance(value, dict):
        raise GraphExpandError("知识图谱拓展配置必须是 JSON 对象")
    unknown = sorted(
        set(value)
        - {
            "schema_version",
            "base_url",
            "admin_users",
            "allow_remote",
            "timeout_seconds",
            "ingest_timeout_seconds",
            "libraries",
        }
    )
    if unknown:
        raise GraphExpandError("graph_config.json 包含未知字段：" + ", ".join(unknown))
    if value.get("schema_version", 2) != 2:
        raise GraphExpandError("graph_config.json 必须使用 schema_version=2 注册表结构")
    allow_remote = value.get("allow_remote", False)
    if not isinstance(allow_remote, bool):
        raise GraphExpandError("allow_remote 必须是布尔值")
    admin_users = _user_list(
        value.get("admin_users"),
        field="admin_users",
        allow_wildcard=False,
    )
    raw_libraries = value.get("libraries", [])
    if not isinstance(raw_libraries, list):
        raise GraphExpandError("libraries 必须是数组")
    if len(raw_libraries) > 100:
        raise GraphExpandError("libraries 最多允许 100 项")
    libraries = tuple(
        _library_from_mapping(item, index=index, default_users=admin_users)
        for index, item in enumerate(raw_libraries)
    )
    ids = [library.id for library in libraries]
    if len(ids) != len(set(ids)):
        raise GraphExpandError("libraries.id 不能重复")
    default_count = sum(library.kind == "service_default" for library in libraries)
    if default_count > 1:
        raise GraphExpandError("service_default 库最多只能注册一个")
    return GraphConfig(
        base_url=normalized_base_url(value.get("base_url"), allow_remote=allow_remote),
        admin_users=admin_users,
        allow_remote=allow_remote,
        timeout_seconds=integer(
            value.get("timeout_seconds"),
            field="timeout_seconds",
            default=15,
            minimum=2,
            maximum=120,
        ),
        ingest_timeout_seconds=integer(
            value.get("ingest_timeout_seconds"),
            field="ingest_timeout_seconds",
            default=1800,
            minimum=60,
            maximum=7200,
        ),
        libraries=libraries,
    )


def config_payload(config: GraphConfig) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "base_url": config.base_url,
        "admin_users": list(config.admin_users),
        "allow_remote": config.allow_remote,
        "timeout_seconds": config.timeout_seconds,
        "ingest_timeout_seconds": config.ingest_timeout_seconds,
        "libraries": [library.public_dict() for library in config.libraries],
    }


def library_signature(library: GraphLibrary) -> str:
    payload = {
        "id": library.id,
        "kind": library.kind,
        "store_root": library.store_root,
        "source_roots": list(library.source_roots),
        "scope": library.scope,
        "owner_id": library.owner_id,
        "allowed_users": list(library.allowed_users),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_config() -> GraphConfig | None:
    if not CONFIG_PATH.is_file():
        return None
    try:
        value = json.loads(CONFIG_PATH.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GraphExpandError("graph_config.json 无法读取或不是有效 JSON") from exc
    return config_from_mapping(value)


def save_config(config: GraphConfig) -> None:
    atomic_json(CONFIG_PATH, config_payload(config))


def resolve_libraries(
    config: GraphConfig,
    requested: Any = None,
    *,
    include_disabled: bool = False,
    caller_user: str | None = None,
) -> list[GraphLibrary]:
    available = [
        library
        for library in config.libraries
        if (include_disabled or library.enabled) and library.allows(caller_user)
    ]
    if requested in (None, []):
        return available
    if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
        raise GraphExpandError("library_ids 必须是库 ID 字符串数组")
    wanted = list(dict.fromkeys(item.strip().casefold() for item in requested if item.strip()))
    by_id = {library.id: library for library in available}
    missing = [item for item in wanted if item not in by_id]
    if missing:
        raise GraphExpandError(f"未知、禁用或未注册的图谱库：{', '.join(missing)}")
    return [by_id[item] for item in wanted]


def configuration_status(caller_user: str | None = None) -> dict[str, Any]:
    config = load_config()
    if config is None:
        return {"ok": True, "active": False, "libraries": []}
    return {
        "ok": True,
        "active": True,
        "base_url": config.base_url,
        "caller_is_admin": config.is_admin(caller_user),
        "allow_remote": config.allow_remote,
        "timeout_seconds": config.timeout_seconds,
        "ingest_timeout_seconds": config.ingest_timeout_seconds,
        "libraries": [
            library.public_dict()
            for library in config.libraries
            if library.allows(caller_user)
        ],
    }
