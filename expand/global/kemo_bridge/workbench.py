"""Device-only settings and workbench projections."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import yaml
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from artifact_store import is_link
from bridge_auth import BranchRoutes, require_session
from payload_tools import credential_key, redact
from peer_store import BridgeError, PeerIdentity, PeerStore
from token_usage import summarize
from upstream import UpstreamClient, UpstreamError

# The core configuration PATCH merges objects; bridge clients cannot use it
# to change other capability domains or control which modules run.
PROTECTED_SECTIONS = {"expand", "expands", "perception", "sense", "skills", "plugins", "knowledge"}
PROVIDER_SECRETS = ("api_key", "api_key_env")


def local_config(user: Path) -> dict:
    path = user / "user_config.yaml"
    if is_link(path):
        raise BridgeError(409, "user_config_unavailable")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise BridgeError(409, "user_config_unavailable") from exc
    return value if isinstance(value, dict) else {}


def config_projection(payload: Any, actual: dict, changes: dict | None = None) -> dict:
    document = payload.get("config", {}) if isinstance(payload, dict) else {}

    def project(value: Any, stored: Any, new: Any) -> Any:
        if not isinstance(value, dict):
            return redact(value)
        stored = stored if isinstance(stored, dict) else {}
        new = new if isinstance(new, dict) else {}
        result = {}
        for key, item in value.items():
            if credential_key(key):
                # Core's redactor uses the same marker for blank and populated values.
                original = stored.get(key, item) if item == "***" else item
                result[key] = redact({key: original})[key]
            else:
                result[key] = project(item, stored.get(key), new.get(key))
        for key, item in new.items():
            if item is None and credential_key(key):
                result.setdefault(key, None)
        return result

    masked = project(document, actual, changes or {}) if isinstance(document, dict) else {}
    provider = masked.setdefault("provider", {})
    if isinstance(provider, dict):
        for key in PROVIDER_SECRETS:
            provider.setdefault(key, None)
    return {"config": masked, **{key: payload[key] for key in ("user", "updated")
                                 if isinstance(payload, dict) and key in payload}}


def validate_changes(changes: Any) -> dict:
    if not isinstance(changes, dict) or not changes:
        raise BridgeError(400, "invalid_config_changes")
    if PROTECTED_SECTIONS.intersection(key.casefold() for key in changes):
        raise BridgeError(403, "capability_not_granted")

    def walk(value: Any) -> None:
        if value == "***":
            raise BridgeError(400, "secret_placeholder_not_writable")
        if isinstance(value, dict):
            for key, item in value.items():
                # Disallow dotted / bracketed paths into secrets. Use a single
                # replacement string (or null to clear), not an object patch.
                if not key or any(ch in key for ch in ".[]"):
                    raise BridgeError(400, "invalid_config_field")
                if credential_key(key) and item is not None and not isinstance(item, str):
                    raise BridgeError(400, "secret_requires_full_replacement")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(changes)
    return changes


class ReasoningChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effort: str = Field(min_length=1, max_length=64)


def user_api(peer: PeerIdentity) -> str:
    return f"/api/users/{quote(peer.bound_user, safe='')}"


async def reasoning_state(upstream: UpstreamClient, base: str) -> dict:
    settings = await upstream.request_json("GET", base + "/settings")
    provider = settings.get("provider", {})
    effort = provider.get("reasoning_effort")
    available = []
    model = provider.get("model")
    if provider.get("type") == "kemo" and model:
        try:
            declared = await upstream.request_json("GET", base + "/provider/model-capabilities", params={"model": model})
        except UpstreamError as exc:
            if exc.status != 404:
                raise
        else:
            reasoning = declared.get("capabilities", {}).get("reasoning", {})
            if reasoning.get("supported") is True:
                available = [entry for entry in reasoning.get("efforts", [])
                             if isinstance(entry, str) and entry]
    return {"effort": effort, "available": available}


def register_workbench(routes: BranchRoutes, peers: PeerStore, upstream: UpstreamClient) -> None:
    @routes.route("GET", "/token_usage", "token_usage")
    def token_usage(since: date | None = None, until: date | None = None,
                    group_by: Literal["day", "model", "session"] = "day",
                    peer: PeerIdentity = Depends(require_session)):
        return summarize(peers.user_directory(peer.bound_user), since, until, group_by)

    @routes.route("GET", "/reasoning", "reasoning")
    async def reasoning(peer: PeerIdentity = Depends(require_session)):
        return await reasoning_state(upstream, user_api(peer))

    @routes.route("PATCH", "/reasoning", "reasoning")
    async def change_reasoning(body: ReasoningChange, peer: PeerIdentity = Depends(require_session)):
        base = user_api(peer)
        effort = body.effort.strip().casefold()
        available = (await reasoning_state(upstream, base))["available"]
        if effort not in available:
            raise BridgeError(400, "reasoning_effort_not_declared")
        await upstream.request_json("PATCH", base + "/config", json_body={
            "changes": {"provider": {"reasoning_effort": effort}},
        })
        return {"effort": effort}

    @routes.route("GET", "/skills", "skill_ref")
    async def skills(scope: Literal["user", "shared", "agent_create", "user_create"] | None = None,
                     enabled: bool | None = None, peer: PeerIdentity = Depends(require_session)):
        value = await upstream.request_json("GET", user_api(peer) + "/skills")
        items = []
        for item in value.get("prompt_skills", []):
            origin = item.get("scope")
            if origin not in {"user", "shared"}:
                continue
            category = ("shared" if origin == "shared" else
                        "agent_create" if item.get("category") == "agent_generated" else "user_create")
            state = item.get("active_for_main_agent") is True
            if (scope and scope != category and scope != origin) or (enabled is not None and state != enabled):
                continue
            items.append({
                "name": item.get("name", ""), "scope": category, "title": item.get("title", ""),
                "description": item.get("description", ""), "enabled": state, "origin": origin,
            })
        return {"items": items, "total": len(items)}

    async def projected(payload, peer, changes=None):
        actual = await run_in_threadpool(local_config, peers.user_directory(peer.bound_user))
        return config_projection(payload, actual, changes)

    @routes.route("GET", "/config", "config")
    async def config(peer: PeerIdentity = Depends(require_session)):
        data = await upstream.request_json("GET", user_api(peer) + "/config/full")
        return await projected(data, peer)

    @routes.route("PATCH", "/config", "config")
    async def patch_config(body: dict[str, Any], peer: PeerIdentity = Depends(require_session)):
        try:
            changes = validate_changes(body.get("changes", body))
        except BridgeError as exc:
            if exc.status == 403:
                await run_in_threadpool(peers.audit, exc.code, peer, endpoint="/v1/config")
            raise
        data = await upstream.request_json("PATCH", user_api(peer) + "/config", json_body={"changes": changes})
        return await projected(data, peer, changes)
