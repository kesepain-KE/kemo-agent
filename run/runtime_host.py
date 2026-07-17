"""Long-running host for CronScheduler and external message transports."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any, Callable

from cron.scheduler import CronScheduler, recover_all
from message.identity import IdentityResolver
from message.router import MessageRouter, RouteResult
from message.state import ProcessedMessageStore
from message.transport import (
    MockTransport,
    RegisteredTransport,
    Transport,
    TransportPolicy,
    TransportRegistry,
)
from provider.factory import create_provider
from run.config import read_json_object
from run.tools import ToolRegistry, discover_tools
from run.users import list_users


_HOST_STATES = frozenset({"stopped", "starting", "running", "stopping", "failed"})


@dataclass(slots=True)
class ComponentStatus:
    name: str
    kind: str
    state: str = "registered"
    last_error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "state": self.state,
            "last_error": dict(self.last_error) if self.last_error else None,
        }


class RuntimeHost:
    """Own the lifecycle of message routing, transports and cron scheduling."""

    def __init__(
        self,
        root: Path,
        *,
        config: dict[str, Any] | None = None,
        registry: TransportRegistry | None = None,
        provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
        tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
        cron_scheduler: CronScheduler | None = None,
        router: MessageRouter | None = None,
        on_result: Callable[[RouteResult], None] | None = None,
        on_error: Callable[[str, BaseException], None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = config or read_json_object(
            self.root / "config" / "global_config.json"
        )
        self.registry = registry or TransportRegistry()
        self.provider_factory = provider_factory
        self.tool_registry_factory = tool_registry_factory
        self.on_result = on_result
        self.on_error = on_error

        host_config = self.config.get("runtime_host") or {}
        message_config = self.config.get("message") or {}
        cron_config = self.config.get("cron") or {}
        self.cron_enabled = bool(cron_config.get("enabled", True)) and bool(
            host_config.get("start_cron", True)
        )
        self.shutdown_timeout = float(host_config.get("shutdown_timeout", 10))
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._state = "stopped"
        self._components: dict[str, ComponentStatus] = {}

        self.resolver = IdentityResolver.from_config(self.root, self.config)
        self.router = router or MessageRouter(
            self.root,
            self.resolver,
            self.registry,
            max_workers=int(message_config.get("max_workers", 4)),
            dedupe_max_entries=int(message_config.get("dedupe_max_entries", 2000)),
            provider_factory=provider_factory,
            tool_registry_factory=tool_registry_factory,
            on_result=self._handle_result,
            on_error=self._handle_error,
        )
        self.cron = cron_scheduler or CronScheduler(
            self.root,
            poll_interval=float(cron_config.get("poll_interval", 30)),
            provider_factory=provider_factory,
            tool_registry_factory=tool_registry_factory,
            on_error=self._handle_error,
        )
        self._components["router"] = ComponentStatus("router", "router")
        self._components["cron"] = ComponentStatus("cron", "scheduler")
        for item in self.registry.items():
            self._components[f"transport:{item.transport.name}"] = ComponentStatus(
                item.transport.name, "transport"
            )

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def running(self) -> bool:
        return self.state == "running"

    def register_transport(
        self, transport: Transport, policy: TransportPolicy | None = None
    ) -> RegisteredTransport:
        with self._lock:
            if self._state not in {"stopped", "failed"}:
                raise RuntimeError("宿主运行期间不能注册 Transport")
            item = self.registry.register(transport, policy)
            self._components[f"transport:{transport.name}"] = ComponentStatus(
                transport.name, "transport"
            )
            return item

    def start(self) -> None:
        with self._lock:
            if self._state in {"starting", "running"}:
                return
            if self._state == "stopping":
                raise RuntimeError("宿主正在停止")
            self._state = "starting"
            self._stop_event.clear()

        try:
            recover_all(self.root)
            self._recover_message_state()

            self._set_component("router", "starting")
            self.router.start()
            self._set_component("router", "running")

            for item in self.registry.items():
                self._start_transport(item)

            if self.cron_enabled:
                self._set_component("cron", "starting")
                self.cron.start()
                self._set_component("cron", "running")
            else:
                self._set_component("cron", "stopped")

            with self._lock:
                self._state = "running"
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._handle_error("host", exc)
            with self._lock:
                self._state = "failed"
            self.stop()
            with self._lock:
                self._state = "failed"
            raise

    def stop(self) -> None:
        with self._lock:
            if self._state == "stopped":
                return
            if self._state == "stopping":
                return
            previous = self._state
            self._state = "stopping"
            self._stop_event.set()

        for item in reversed(self.registry.items()):
            key = f"transport:{item.transport.name}"
            try:
                item.transport.stop()
                item.state = "stopped"
                self._set_component(key, "stopped")
            except Exception as exc:
                item.state = "failed"
                item.last_error = self._error_payload(exc)
                self._set_component(key, "failed", exc)

        try:
            self.router.stop(wait=True)
            self._set_component("router", "stopped")
        except Exception as exc:
            self._set_component("router", "failed", exc)

        try:
            self.cron.stop(timeout=self.shutdown_timeout)
            self._set_component("cron", "stopped")
        except Exception as exc:
            self._set_component("cron", "failed", exc)

        with self._lock:
            if previous != "failed":
                self._state = "stopped"

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until stop is requested. Returns True if stopped by signal."""
        return self._stop_event.wait(timeout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            components = {
                key: value.to_dict() for key, value in self._components.items()
            }
            return {"state": self._state, "components": components}

    def _start_transport(self, item: RegisteredTransport) -> None:
        name = item.transport.name
        key = f"transport:{name}"
        item.state = "starting"
        self._set_component(key, "starting")
        try:
            item.transport.start(
                lambda envelope: self._accept_message(envelope),
                self._handle_error,
            )
            item.state = "running"
            item.last_error = None
            self._set_component(key, "running")
        except Exception as exc:
            item.state = "failed"
            item.last_error = self._error_payload(exc)
            self._set_component(key, "failed", exc)
            self._handle_error(name, exc)

    def _accept_message(self, envelope) -> None:
        if not self.running:
            return
        try:
            future = self.router.submit(envelope)
            future.add_done_callback(
                lambda item: self._future_done(envelope.platform, item)
            )
        except Exception as exc:
            self._handle_error(envelope.platform, exc)

    def _future_done(self, platform: str, future) -> None:
        try:
            future.result()
        except BaseException as exc:
            if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                self._handle_error(platform, exc)

    def _recover_message_state(self) -> None:
        message_config = self.config.get("message") or {}
        max_entries = int(message_config.get("dedupe_max_entries", 2000))
        for user in list_users(self.root):
            try:
                ProcessedMessageStore(
                    self.root, user, max_entries=max_entries
                ).recover_interrupted()
            except Exception as exc:
                self._handle_error(f"message_state:{user}", exc)

    def _handle_result(self, result: RouteResult) -> None:
        if self.on_result is not None:
            try:
                self.on_result(result)
            except Exception as exc:
                self._handle_error("result_callback", exc)

    def _handle_error(self, component: str, exc: BaseException) -> None:
        # Only explicit lifecycle failures update component health.  A single
        # routed message or Cron task may fail without meaning the transport or
        # scheduler process itself has stopped.
        if component in self._components:
            self._set_component(component, "failed", exc)
        if self.on_error is not None:
            try:
                self.on_error(component, exc)
            except Exception:
                pass

    @staticmethod
    def _error_payload(exc: BaseException) -> dict[str, str]:
        return {
            "message": str(exc),
            "exception_type": type(exc).__name__,
        }

    def _set_component(
        self, key: str, state: str, exc: BaseException | None = None
    ) -> None:
        with self._lock:
            item = self._components.get(key)
            if item is None:
                item = ComponentStatus(key, "component")
                self._components[key] = item
            item.state = state
            item.last_error = self._error_payload(exc) if exc is not None else None


def build_host(
    root: Path,
    *,
    include_mock: bool = False,
    mock_policy: TransportPolicy | None = None,
) -> RuntimeHost:
    """Build a RuntimeHost from global config for CLI/background startup."""
    base = root.resolve()
    config = read_json_object(base / "config" / "global_config.json")
    registry = TransportRegistry()
    if include_mock:
        registry.register(MockTransport(), mock_policy)
    return RuntimeHost(base, config=config, registry=registry)
