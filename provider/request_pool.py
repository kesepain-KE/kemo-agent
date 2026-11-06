"""A non-preemptive fair request pool shared by all users in one process."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import threading
import time

from provider.fairness import SpaceTurns
from provider.request_scope import RequestScope, UNSCOPED


class RequestCancelled(RuntimeError):
    pass


@dataclass(eq=False)
class RequestTicket:
    scope: RequestScope
    priority: int = 2
    enqueued_at: float = 0.0


class FairRequestPool:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._condition = threading.Condition()
        self._active: Counter[RequestScope] = Counter()
        self._pending: list[RequestTicket] = []
        self._turns: SpaceTurns[RequestScope] = SpaceTurns()

    def status(self) -> dict[str, int]:
        with self._condition:
            active = self._active.total()
            return {
                "active_requests": active,
                "max_requests": self.limit,
                "available_requests": self.limit - active,
                "waiting_estimate": len(self._pending),
            }

    def register(self, scope: RequestScope, priority: int = 2) -> RequestTicket:
        """Register under the factory lock so idle reconfiguration cannot race."""
        with self._condition:
            if type(priority) is not int or priority not in {0, 1, 2}:
                raise ValueError("Invalid Provider priority")
            ticket = RequestTicket(scope, priority, time.monotonic())
            self._pending.append(ticket)
            # Record new contenders before a current borrower releases its slot.
            self._turns.choose((item.scope for item in self._pending), self._active)
            self._condition.notify_all()
            return ticket

    def wait(
        self, ticket: RequestTicket, timeout: float | None,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            try:
                first_check = True
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RequestCancelled()
                    if not first_check and deadline is not None and time.monotonic() >= deadline:
                        return False
                    # A zero timeout still allows an immediate nonblocking grant.
                    now = time.monotonic()
                    # Every 2s of waiting raises one effective class. Once all
                    # reach P0, age wins across classes, preserving bounded
                    # service even under a continuous stream of main requests.
                    def rank(item):
                        return max(0, item.priority - int((now - item.enqueued_at) / 2))
                    best = min((rank(item) for item in self._pending), default=2)
                    candidates = [item for item in self._pending if rank(item) == best]
                    aged = [item for item in candidates if now - item.enqueued_at >= 4]
                    if aged:
                        first = min(aged, key=lambda item: item.enqueued_at)
                    else:
                        chosen = self._turns.choose((item.scope for item in candidates), self._active)
                        first = next((item for item in candidates if item.scope == chosen), None)
                    if self._active.total() < self.limit and first is ticket:
                        self._pending.remove(ticket)
                        self._active[ticket.scope] += 1
                        self._turns.served(ticket.scope)
                        self._condition.notify_all()
                        return True
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        return False
                    first_check = False
                    self._condition.wait(.1 if remaining is None else min(.1, remaining))
            finally:
                if ticket in self._pending:
                    self._pending.remove(ticket)
                    self._condition.notify_all()

    def acquire(
        self, blocking: bool = True, timeout: float | None = None,
    ) -> bool:
        """Compatibility for trusted callers of get_provider_semaphore()."""
        return self.wait(self.register(UNSCOPED), timeout if blocking else 0)

    def release(self, scope: RequestScope = UNSCOPED) -> None:
        with self._condition:
            if self._active[scope] < 1:
                raise ValueError("Provider 槽位重复释放或归属不匹配")
            self._active[scope] -= 1
            if not self._active[scope]:
                del self._active[scope]
            self._condition.notify_all()
