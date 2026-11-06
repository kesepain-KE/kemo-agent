"""首页概览与会话摘要状态聚合。"""

from __future__ import annotations

import threading
from typing import Callable, Iterator
from events import RunEvent
from run.infra import BoundedReadCache
from web.services.artifact_resolver import DownloadArtifactResolver
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from web.service import ActiveRun

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web.service import WebRunService

from pathlib import Path
from typing import Any

from run.cards import CardStore
from web.services.overview_aggregate import build_overview as _build_overview_impl


class OverviewServiceMixin:
    if TYPE_CHECKING:
        _overview_cache: BoundedReadCache
        require_session_id = WebRunService.require_session_id
        require_source = WebRunService.require_source
        require_user = WebRunService.require_user
        root: Path
        runtime_status_provider: Callable[[], dict[str, Any]] | None

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

    def _summary_cache_status(self, user, session_id, *, source="web"):
        store = CardStore(self.root, user)
        state = store.session(source, session_id) if session_id and store.path.is_file() else None
        snapshot = store.snapshot(source, session_id) if state else None
        card = next((card for card in snapshot.cards if card.view_kind == "summary"), None) if snapshot else None
        return {"exists": card is not None, "engine": "cardstream", "card_id": card.card_id if card else None,
                "host_seq": card.seq if card else None,
                "compression_stage": card.compression_stage if card else 0,
                "view_epoch": snapshot.view_epoch if snapshot else 0}

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
