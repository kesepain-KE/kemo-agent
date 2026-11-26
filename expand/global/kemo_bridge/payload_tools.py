"""Pure helpers for selecting, searching, and redacting upstream payloads."""

from __future__ import annotations

import re
from typing import Any


def select(value: Any, *keys: str, default: Any) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                return value[key]
        for nested in value.values():
            selected = select(nested, *keys, default=None)
            if selected is not None:
                return selected
    return default


def find_named(value: Any, name: str, scope: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if str(value.get("name", "")) == name and str(value.get("scope", scope)) == scope:
            return value
        for nested in value.values():
            found = find_named(nested, name, scope)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = find_named(nested, name, scope)
            if found:
                return found
    return None


def search_json(
    value: Any,
    query: str,
    hits: list[dict[str, Any]],
    path: str = "",
) -> None:
    if isinstance(value, dict):
        text = " ".join(
            str(item)
            for item in value.values()
            if isinstance(item, (str, int, float))
        )
        if query in text.casefold():
            hits.append({"path": path, "item": value})
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                search_json(nested, query, hits, f"{path}/{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            search_json(nested, query, hits, f"{path}/{index}")


def credential_key(key: str) -> bool:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key)).casefold()
    parts = re.split(r"[^a-z0-9]+", name)
    if {"auth", "key", "keys", "password", "secret", "cookie", "authorization", "credential", "credentials",
        "apikey", "apikeys", "sessionsecret", "accesstoken"}.intersection(parts):
        return True
    return "token" in parts and name not in {"token_limit", "token_compression_ratio"}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (None if item is None or item == "" or item == {} or item == [] else "***")
            if credential_key(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
