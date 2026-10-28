"""Work-conserving, least-occupied round robin without retaining idle spaces."""
from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping
from typing import Generic, TypeVar

Scope = TypeVar("Scope", bound=Hashable)


class SpaceTurns(Generic[Scope]):
    """Caller owns the lock. Running work is never preempted."""

    def __init__(self) -> None:
        self._order: list[Scope] = []
        self._last: Scope | None = None

    def choose(self, pending: Iterable[Scope], active: Mapping[Scope, int]) -> Scope | None:
        candidates = list(dict.fromkeys(pending))
        needed = set(candidates) | {key for key, count in active.items() if count}
        self._order = [key for key in self._order if key in needed or key == self._last]
        for key in candidates:
            if key not in self._order:
                # New contenders get a turn before the last borrower can refill.
                index = len(self._order) - 1 if self._order and self._order[-1] == self._last else len(self._order)
                self._order.insert(index, key)
        if not candidates:
            return None
        lowest = min(active.get(key, 0) for key in candidates)
        eligible = {key for key in candidates if active.get(key, 0) == lowest}
        return next(key for key in self._order if key in eligible)

    def served(self, key: Scope) -> None:
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)
        self._last = key
