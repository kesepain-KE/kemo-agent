"""Thin public facade for the Kemo Graph sidecar extension."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from client import api_request, verify_service
from errors import GraphAPIError, GraphExpandError
from library_sync import scan_libraries, sync_libraries
from operations import (
    document_operation,
    ingest_library,
    initialize_libraries,
    jobs_operation,
    query_libraries,
    status_libraries,
    upload_markdown,
)
from registry import (
    BASE_DIR,
    CONFIG_PATH,
    INPUT_PATH,
    LAST_RUN_PATH,
    MANIFEST_PATH,
    QUERY_ARTIFACT_DIR,
    STATUS_PATH,
    SYNC_STATE_PATH,
    GraphConfig,
    GraphLibrary,
    atomic_json as _atomic_json,
    atomic_text as _atomic_text,
    config_from_mapping,
    config_payload as _config_payload,
    configuration_status,
    load_config,
    resolve_libraries,
    save_config,
)
from render import refresh_catalog


def activate(arguments: dict[str, Any]) -> dict[str, Any]:
    config = config_from_mapping(arguments)
    save_config(config)
    return {**configuration_status(), "catalog": refresh_catalog()}


def deactivate() -> dict[str, Any]:
    CONFIG_PATH.unlink(missing_ok=True)
    return {
        **refresh_catalog(),
        "deactivated": True,
        "external_stores_deleted": False,
        "local_sync_state_preserved": True,
    }


def update_snapshot() -> dict[str, Any]:
    """Compatibility name: only regenerate the local registry catalog."""

    return refresh_catalog()


def library_descriptors(config: GraphConfig) -> list[dict[str, Any]]:
    return [library.public_dict() for library in config.libraries]


def resolve_domains(
    config: GraphConfig,
    requested: Any = None,
    *,
    agent_root: Path | None = None,
    caller_user: str | None = None,
) -> list[GraphLibrary]:
    """Deprecated name retained for callers migrating to library_ids."""

    del agent_root
    return resolve_libraries(config, requested, caller_user=caller_user)


__all__ = [
    "BASE_DIR",
    "CONFIG_PATH",
    "INPUT_PATH",
    "LAST_RUN_PATH",
    "MANIFEST_PATH",
    "QUERY_ARTIFACT_DIR",
    "STATUS_PATH",
    "SYNC_STATE_PATH",
    "GraphAPIError",
    "GraphConfig",
    "GraphExpandError",
    "GraphLibrary",
    "_atomic_json",
    "_atomic_text",
    "_config_payload",
    "activate",
    "api_request",
    "config_from_mapping",
    "configuration_status",
    "deactivate",
    "document_operation",
    "ingest_library",
    "initialize_libraries",
    "jobs_operation",
    "library_descriptors",
    "load_config",
    "query_libraries",
    "refresh_catalog",
    "resolve_domains",
    "resolve_libraries",
    "scan_libraries",
    "status_libraries",
    "sync_libraries",
    "update_snapshot",
    "upload_markdown",
    "verify_service",
]
