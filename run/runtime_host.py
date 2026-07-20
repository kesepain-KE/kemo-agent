"""用于 CronScheduler 和外部消息传输的长时间运行主机。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Callable

from cron.scheduler import (
    CronScheduler,
    cleanup_old_system_tasks,
    ensure_memory_maintenance_tasks,
    ensure_memory_promotion_task,
    recover_all,
)
from message.identity import IdentityResolver
from message.plugin import (
    FileMessageTransport,
    MessagePluginIssue,
    discover_message_plugins,
)
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
from run.maintenance import MaintenanceScheduler
from run.tools import ToolRegistry, discover_tools
from run.users import list_users


_HOST_STATES = frozenset({"stopped", "starting", "running", "stopping", "failed"})
DEFAULT_SHUTDOWN_TIMEOUT = 10.0
DEFAULT_PROCESSED_MESSAGE_LIMIT = 2000


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
        message_config: dict[str, Any] | None = None,
        registry: TransportRegistry | None = None,
        provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
        tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
        cron_scheduler: CronScheduler | None = None,
        maintenance_scheduler: MaintenanceScheduler | None = None,
        router: MessageRouter | None = None,
        on_result: Callable[[RouteResult], None] | None = None,
        on_error: Callable[[str, BaseException], None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = config or read_json_object(
            self.root / "config" / "global_config.json"
        )
        self.message_config = (
            message_config
            if message_config is not None
            else read_json_object(
                self.root / "config" / "message_config.json",
                allow_empty=True,
            )
        )
        self.registry = registry or TransportRegistry()
        self.provider_factory = provider_factory
        self.tool_registry_factory = tool_registry_factory
        self.on_result = on_result
        self.on_error = on_error
        self._message_plugin_issues: list[MessagePluginIssue] = []
        plugin_transports, discovery_issues = discover_message_plugins(self.root)
        self._message_plugin_issues.extend(discovery_issues)
        for transport in plugin_transports:
            try:
                self.registry.register(transport, transport.policy)
            except Exception as exc:
                self._message_plugin_issues.append(
                    MessagePluginIssue(
                        transport.config.directory.name,
                        transport.config.directory,
                        str(exc),
                    )
                )

        host_config = self.config.get("runtime_host") or {}
        runtime_message_config = self.config.get("message") or {}
        cron_config = self.config.get("cron") or {}
        self.background_enabled = bool(
            host_config.get("enable_background_scheduler", True)
        )
        self.cron_enabled = self.background_enabled and bool(
            cron_config.get("enabled", True)
        )
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._state = "stopped"
        self._components: dict[str, ComponentStatus] = {}

        self.resolver = IdentityResolver.from_config(self.root, self.message_config)
        if router is None:
            self.router = MessageRouter(
                self.root,
                self.resolver,
                self.registry,
                max_workers=int(runtime_message_config.get("max_workers", 8)),
                processed_message_limit=DEFAULT_PROCESSED_MESSAGE_LIMIT,
                provider_factory=provider_factory,
                tool_registry_factory=tool_registry_factory,
                on_result=self._handle_result,
                on_error=self._handle_error,
            )
        else:
            if self.on_result is None:
                self.on_result = router.on_result
            if self.on_error is None:
                self.on_error = router.on_error
            router.on_result = self._handle_result
            router.on_error = self._handle_error
            self.router = router
        self.cron = cron_scheduler or CronScheduler(
            self.root,
            poll_interval=float(cron_config.get("poll_interval", 30)),
            provider_factory=provider_factory,
            tool_registry_factory=tool_registry_factory,
            on_error=self._handle_error,
        )
        self.maintenance = maintenance_scheduler or MaintenanceScheduler(
            self.root,
            poll_interval=float(cron_config.get("poll_interval", 30)),
            provider_factory=provider_factory,
            tool_registry_factory=tool_registry_factory,
            on_error=self._handle_error,
        )
        self._components["router"] = ComponentStatus("router", "router")
        self._components["background"] = ComponentStatus(
            "background", "scheduler_group"
        )
        self._components["cron"] = ComponentStatus("cron", "scheduler")
        self._components["maintenance"] = ComponentStatus(
            "maintenance", "scheduler"
        )
        for item in self.registry.items():
            self._components[f"transport:{item.transport.name}"] = ComponentStatus(
                item.transport.name,
                "message_plugin"
                if isinstance(item.transport, FileMessageTransport)
                else "transport",
            )
        for issue in self._message_plugin_issues:
            self._components[f"message_plugin:{issue.name}"] = ComponentStatus(
                issue.name,
                "message_plugin",
                state="failed",
                last_error={
                    "message": issue.error,
                    "exception_type": "MessagePluginError",
                },
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
            if self.cron_enabled:
                for user in list_users(self.root):
                    cleanup_old_system_tasks(self.root, user)
            recover_all(self.root)
            self._recover_message_state()

            if self.cron_enabled:
                ensure_memory_maintenance_tasks(self.root, self.config)
                ensure_memory_promotion_task(self.root)

            self._set_component("router", "starting")
            self.router.start()
            self._set_component("router", "running")

            for item in self.registry.items():
                self._start_transport(item)

            if self.background_enabled:
                self._set_component("background", "starting")
                self._set_component("maintenance", "starting")
                self.maintenance.start()
                self._set_component("maintenance", "running")
            else:
                self._set_component("background", "stopped")
                self._set_component("maintenance", "stopped")

            if self.cron_enabled:
                self._set_component("cron", "starting")
                self.cron.start()
                self._set_component("cron", "running")
            else:
                self._set_component("cron", "stopped")

            if self.background_enabled:
                self._set_component("background", "running")

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
            self.cron.stop(timeout=DEFAULT_SHUTDOWN_TIMEOUT)
            self._set_component("cron", "stopped")
        except Exception as exc:
            self._set_component("cron", "failed", exc)

        try:
            self.maintenance.stop(timeout=DEFAULT_SHUTDOWN_TIMEOUT)
            self._set_component("maintenance", "stopped")
        except Exception as exc:
            self._set_component("maintenance", "failed", exc)

        if self._components["cron"].state == "stopped" and self._components[
            "maintenance"
        ].state == "stopped":
            self._set_component("background", "stopped")

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

    def _accept_message(self, envelope):
        if self.state not in {"starting", "running"}:
            return None
        try:
            future = self.router.submit(envelope)
            future.add_done_callback(
                lambda item: self._future_done(envelope.platform, item)
            )
            return future
        except Exception as exc:
            self._handle_error(envelope.platform, exc)
            return None

    def _future_done(self, platform: str, future) -> None:
        try:
            future.result()
        except BaseException as exc:
            if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                self._handle_error(platform, exc)

    def _recover_message_state(self) -> None:
        for user in list_users(self.root):
            try:
                ProcessedMessageStore(
                    self.root,
                    user,
                    max_entries=DEFAULT_PROCESSED_MESSAGE_LIMIT,
                ).recover_interrupted()
            except Exception as exc:
                self._handle_error(f"message_state:{user}", exc)

    def _handle_result(self, result: RouteResult) -> None:
        try:
            registered = self.registry.get(result.envelope.platform)
            finalize = getattr(registered.transport, "finalize", None)
            if callable(finalize):
                finalize(result)
        except Exception as exc:
            self._handle_error(result.envelope.platform, exc)
        if self.on_result is not None:
            try:
                self.on_result(result)
            except Exception as exc:
                self._handle_error("result_callback", exc)

    def _handle_error(self, component: str, exc: BaseException) -> None:
                # 只有显式的生命周期故障才会更新组件的运行状况。  单个
                # 路由消息或 Cron 任务可能会失败，而不意味着传输或
                # 调度程序进程本身已停止。
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
    message_config = read_json_object(
        base / "config" / "message_config.json",
        allow_empty=True,
    )
    registry = TransportRegistry()
    if include_mock:
        registry.register(MockTransport(), mock_policy)
    return RuntimeHost(
        base,
        config=config,
        message_config=message_config,
        registry=registry,
    )
