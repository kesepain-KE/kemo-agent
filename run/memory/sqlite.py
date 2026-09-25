"""SQLite-backed implementation of the tiered memory engine.

The public store remains a single entry point while cohesive fragment,
promotion, candidate/weight, and important-view behaviors live in mixins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from run.memory.store import database_path
from run.memory.sqlite_support import memory_api as _memory_api
from run.memory.sqlite_fragments import FragmentStoreMixin
from run.memory.sqlite_promotion import PromotionStoreMixin
from run.memory.sqlite_candidates import CandidateStoreMixin
from run.memory.sqlite_important import ImportantStoreMixin


class SqliteMemoryStore(
    FragmentStoreMixin,
    PromotionStoreMixin,
    CandidateStoreMixin,
    ImportantStoreMixin,
):
    def __init__(self, root: Path, user: str, config: dict[str, Any]) -> None:
        api = _memory_api()
        self.root = root.resolve()
        self.user = user
        self.config = config
        self.rules = api.tier_rules(config)
        self._lock = api._store_lock(self.root, user)

    def database_path(self) -> Path:
        return database_path(self.root, self.user)
