"""首页概览与会话摘要状态聚合。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from run.agents import discover_agents
from run.config import load_config
from run.context import read_summary_cache
from run.history import find_window, list_sessions, load_window, runtime_window_path
from web.services.runtime_status import _nonnegative_int
from web.services.overview_aggregate import build_overview as _build_overview_impl


class OverviewServiceMixin:
    def overview(
        self,
        user: Any,
        *,
        session_id: Any = "",
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id) if session_id else ""
        key = (name, normalized_source, normalized_session)
        return self._overview_cache.get_or_load(
            key, lambda: self._build_overview(name, session_id=normalized_session, source=normalized_source),
            owner=name, ttl=0.5,
        )[0]

    def _summary_cache_status(
        self,
        user: str,
        session_id: str,
        *,
        source: str = "web",
    ) -> dict[str, Any]:
        empty = {
            "exists": False,
            "covered_rounds": [],
            "created_at": "",
            "window": "",
        }
        if not session_id:
            return empty
        directory = find_window(self.root, user, source, session_id)
        if directory is None:
            return empty
        runtime_path = runtime_window_path(directory)
        try:
            value = read_summary_cache(runtime_path)
        except Exception:
            return {
                **empty,
                "exists": False,
                "window": directory.name,
                "invalid": True,
            }
        if value is None:
            return {**empty, "window": directory.name}
        return {
            "exists": True,
            "covered_rounds": [
                int(item) for item in value.get("covered_rounds", []) if isinstance(item, int)
            ] if isinstance(value, dict) else [],
            "created_at": str(value.get("created_at") or "") if isinstance(value, dict) else "",
            "window": directory.name,
        }

    def _runtime_status(self) -> dict[str, Any]:
        if self.runtime_status_provider is None:
            return {"state": "unmanaged", "components": {}}
        try:
            value = self.runtime_status_provider()
        except Exception:
            return {"state": "unavailable", "components": {}}
        if not isinstance(value, dict):
            return {"state": "unavailable", "components": {}}
        components = value.get("components")
        return {
            "state": str(value.get("state") or "unknown"),
            "components": dict(components) if isinstance(components, dict) else {},
        }

    def _build_overview(
        self,
        user: Any,
        *,
        session_id: Any = "",
        source: Any = "web",
    ) -> dict[str, Any]:
        """Compatibility entry point delegated to overview aggregation."""

        return _build_overview_impl(
            self,
            user,
            session_id=session_id,
            source=source,
        )
