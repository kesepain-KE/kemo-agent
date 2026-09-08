"""基于现有运行、历史记录和用户 API 的面向 Web 的服务适配器。"""

from __future__ import annotations

import math
import copy
from dataclasses import dataclass, field
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable, Iterator
import uuid

from pydantic import ValidationError

from events import RunEvent
from provider.protocol.models import (
    AudioContent,
    FileContent,
    ImageContent,
    VideoContent,
)
from run.agents import AgentRunner
from run.extensions import history_attachment_descriptors
from run.config import (
    ConfigError,
    load_config,
)
from run.engine import compress_context, iter_request_events
from run.history import (
    find_window,
    load_window,
    queue_memory_extraction,
)
from run.history import (
    close_session as close_index_session,
    queue_summary as queue_history_summary,
    find_record as find_index_record,
)
from run.long_task import (
    LONG_TASK_ACTIVE_STATUSES,
    activate_long_task,
    finish_long_task,
    get_long_task_state,
    reconcile_orphaned_long_task,
    record_long_task_run,
    request_long_task_cancel,
    set_long_task_current_run,
    set_long_task_enabled as update_long_task_enabled,
)
from run.long_task import (
    MAX_LONG_TASK_RUNS,
    continuation_request,
    is_continuable_terminal,
    long_task_event_metadata,
    terminal_run_stats,
)
from run.tasks import (
    PlanError,
    PlanNotFoundError,
    PlanStore,
)
from run.memory import extract_memory_backlog
from run.conversation import session_lock
from run.conversation import GuidanceInput, GuidanceMailbox
from run.config import list_users
from web.constants import (
    AUDIO_PREVIEW_MAX_BYTES,
    AVATAR_MAX_BYTES,
    COMPLETION_SOUND_MAX_BYTES,
    FAILURE_SOUND_MAX_BYTES,
    FILE_UPLOAD_MAX_BYTES,
    IMAGE_PREVIEW_MAX_BYTES,
    IMPORTANT_MEMORY_MAX_HARD_CHARS,
    SESSION_LEASE_TTL_SECONDS,
    SKILL_ARCHIVE_MAX_BYTES,
    TEXT_DOCUMENT_MAX_CHARS,
    VIDEO_PREVIEW_MAX_BYTES,
    _CLIENT_ID_RE,
    _CONTENT_LIST_ADAPTER,
    _RUN_ID_RE,
    _SESSION_RE,
    _SESSION_TITLE_RE,
    _WORKER_DONE,
)
from web.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    ProviderDiscoveryError,
    TooManyChatsError,
    WebServiceError,
)
from web.services.artifact_resolver import DownloadArtifactResolver
from web.services.chat_stream import stream_chat as _stream_chat
from web.services.files import FileServiceMixin
from web.services.identity import IdentityServiceMixin
from web.services.knowledge import KnowledgeServiceMixin
from web.services.memory import MemoryServiceMixin
from web.services.modules import MessageExpandServiceMixin
from web.services.runtime import RuntimeServiceMixin, _usage_cache_tokens
from web.services.run_control import RunControlServiceMixin
from web.services.sense import SenseServiceMixin
from web.services.sessions import SessionServiceMixin
from web.services.settings import SettingsServiceMixin, _fetch_remote_version_manifest
from web.services.skills import SkillServiceMixin
from web.services.tasks import TaskServiceMixin


__all__ = [
    "AUDIO_PREVIEW_MAX_BYTES",
    "AVATAR_MAX_BYTES",
    "COMPLETION_SOUND_MAX_BYTES",
    "FAILURE_SOUND_MAX_BYTES",
    "ActiveRun",
    "ConflictError",
    "FILE_UPLOAD_MAX_BYTES",
    "IMAGE_PREVIEW_MAX_BYTES",
    "IMPORTANT_MEMORY_MAX_HARD_CHARS",
    "InvalidRequestError",
    "NotFoundError",
    "ProviderDiscoveryError",
    "SKILL_ARCHIVE_MAX_BYTES",
    "TEXT_DOCUMENT_MAX_CHARS",
    "TooManyChatsError",
    "VIDEO_PREVIEW_MAX_BYTES",
    "WebRunService",
    "WebServiceError",
    "_usage_cache_tokens",
]


@dataclass(slots=True)
class ActiveRun:
    run_id: str
    user: str
    session_id: str
    source: str = "web"
    cancel_event: threading.Event = field(default_factory=threading.Event)
    guidance: GuidanceMailbox = field(default_factory=lambda: GuidanceMailbox(maxsize=8))
    started_at: float = field(default_factory=time.monotonic)


class _UserChatGate:
    """单用户聊天并发槽与有界等待区。"""

    def __init__(
        self,
        max_concurrent: int,
        max_pending: int,
        pending_timeout: float,
    ) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_pending = max(0, int(max_pending))
        self.pending_timeout = max(1.0, float(pending_timeout))
        self._semaphore = threading.BoundedSemaphore(self.max_concurrent)
        self._pending_count = 0
        self._active_count = 0
        self._lock = threading.Lock()

    def acquire(self, *, cancel_event: threading.Event | None = None) -> bool:
        if cancel_event is not None and cancel_event.is_set():
            return False
        if self._semaphore.acquire(blocking=False):
            if cancel_event is not None and cancel_event.is_set():
                self._semaphore.release()
                return False
            with self._lock:
                self._active_count += 1
            return True
        with self._lock:
            if self._pending_count >= self.max_pending:
                return False
            self._pending_count += 1
        acquired = False
        deadline = time.monotonic() + self.pending_timeout
        try:
            while not acquired:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                acquired = self._semaphore.acquire(timeout=min(0.1, remaining))
            if cancel_event is not None and cancel_event.is_set():
                self._semaphore.release()
                acquired = False
                return False
            return acquired
        finally:
            with self._lock:
                self._pending_count = max(0, self._pending_count - 1)
                if acquired:
                    self._active_count += 1

    def release(self) -> None:
        with self._lock:
            if self._active_count < 1:
                raise RuntimeError("Web 聊天并发槽位重复释放")
            self._active_count -= 1
            self._semaphore.release()

    def status(self) -> dict[str, int]:
        with self._lock:
            return {
                "active_chats": self._active_count,
                "max_chats": self.max_concurrent,
                "pending_chats": self._pending_count,
                "max_pending": self.max_pending,
            }

    def matches(self, max_concurrent: int, max_pending: int, pending_timeout: float) -> bool:
        return (
            self.max_concurrent == max(1, int(max_concurrent))
            and self.max_pending == max(0, int(max_pending))
            and self.pending_timeout == max(1.0, float(pending_timeout))
        )


class WebRunService(
    RunControlServiceMixin,
    FileServiceMixin,
    IdentityServiceMixin,
    KnowledgeServiceMixin,
    MemoryServiceMixin,
    MessageExpandServiceMixin,
    RuntimeServiceMixin,
    SenseServiceMixin,
    SessionServiceMixin,
    SettingsServiceMixin,
    SkillServiceMixin,
    TaskServiceMixin,
):
    """A thin, injectable boundary between HTTP routes and the Run core."""

    def __init__(
        self,
        root: Path,
        *,
        event_source: Callable[..., Iterator[RunEvent]] = iter_request_events,
        context_compressor: Callable[..., dict[str, Any]] = compress_context,
        runtime_status_provider: Callable[[], dict[str, Any]] | None = None,
        message_health_checker: Callable[[str, str], dict[str, Any]] | None = None,
        message_transport_remover: Callable[[str, str], None] | None = None,
        plan_waker: Callable[[], None] | None = None,
        summary_waker: Callable[[], None] | None = None,
        router_ref: Any | None = None,
        version_manifest_fetcher: Callable[[str, float], dict[str, Any]] = _fetch_remote_version_manifest,
    ) -> None:
        self.root = root.resolve()
        self.event_source = event_source
        self.context_compressor = context_compressor
        self.runtime_status_provider = runtime_status_provider
        self.message_health_checker = message_health_checker
        self.message_transport_remover = message_transport_remover
        self.plan_waker = plan_waker
        self.summary_waker = summary_waker
        self._router_ref = router_ref
        self.version_manifest_fetcher = version_manifest_fetcher
        self._active_runs: dict[str, ActiveRun] = {}
        self._active_runs_lock = threading.RLock()
        self._session_leases: dict[tuple[str, str, str], dict[str, float]] = {}
        self._chat_gates: dict[str, _UserChatGate] = {}
        self._chat_gates_lock = threading.Lock()
        self._file_upload_lock = threading.RLock()
        self._download_artifact_resolver = DownloadArtifactResolver()
        self._skill_upload_lock = threading.RLock()
        self._version_check_lock = threading.Lock()
        self._version_check_cache: tuple[float, dict[str, Any]] | None = None
        self._overview_cache_lock = threading.RLock()
        self._overview_cache: dict[
            tuple[str, str, str], tuple[float, dict[str, Any]]
        ] = {}
        self._kemo_catalog_lock = threading.RLock()
        self._kemo_catalog_cache: dict[
            tuple[str, str, str], tuple[float, Any]
        ] = {}

    def _get_chat_gate(self, user: str) -> _UserChatGate:
        try:
            config = load_config(user, self.root)
        except ConfigError:
            # WebRunService 也用于最小化嵌入/测试环境；缺少完整配置时
            # 维持既有行为并使用安全默认值，不能让并发层反向破坏启动。
            config = {}
        web_config = config.get("web") or {}
        if not isinstance(web_config, dict):
            web_config = {}
        try:
            max_concurrent = int(web_config.get("max_concurrent_chats", 3))
        except (TypeError, ValueError):
            max_concurrent = 3
        try:
            max_pending = int(web_config.get("max_pending_chats", 5))
        except (TypeError, ValueError):
            max_pending = 5
        try:
            pending_timeout = float(web_config.get("pending_chat_timeout", 30.0))
        except (TypeError, ValueError):
            pending_timeout = 30.0
        if not math.isfinite(pending_timeout):
            pending_timeout = 30.0
        max_concurrent = max(1, max_concurrent)
        max_pending = max(0, max_pending)
        pending_timeout = max(1.0, pending_timeout)

        with self._chat_gates_lock:
            gate = self._chat_gates.get(user)
            if gate is not None:
                if gate.matches(max_concurrent, max_pending, pending_timeout):
                    return gate
                status = gate.status()
                if status["active_chats"] or status["pending_chats"]:
                    # 在途请求继续服从创建时的闸门，等完全空闲后再原子替换。
                    return gate
            gate = _UserChatGate(
                max_concurrent=max_concurrent,
                max_pending=max_pending,
                pending_timeout=pending_timeout,
            )
            self._chat_gates[user] = gate
            return gate

    def chat_gate_status(self) -> dict[str, dict[str, int]]:
        with self._chat_gates_lock:
            return {user: gate.status() for user, gate in self._chat_gates.items()}

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "service": "kemo-agent-web", "version": 2}

    def users(self) -> list[dict[str, str]]:
        return [{"name": user} for user in list_users(self.root)]

    def require_user(self, user: Any) -> str:
        if not isinstance(user, str) or not user.strip():
            raise InvalidRequestError("user 必须是非空字符串")
        name = user.strip()
        if name not in set(list_users(self.root)):
            raise NotFoundError(f"用户不存在：{name}")
        return name

    def require_source(self, source: Any = "web") -> str:
        if source not in {"web", "app"}:
            raise InvalidRequestError("交互 API 当前仅允许 source=web 或 source=app")
        return str(source)

    def require_history_source(
        self,
        source: Any = "web",
        *,
        allow_all: bool = False,
    ) -> str | None:
        """Validate a read-only history source without granting mutation rights."""

        if not isinstance(source, str):
            raise InvalidRequestError("source 必须是字符串")
        value = source.strip()
        if allow_all and value in {"", "all"}:
            return None
        if value in {"web", "app", "cli", "interactive", "direct_api", "telegram", "onebot"}:
            return value
        if value.startswith("message:"):
            platform = value.split(":", 1)[1]
            if platform and len(platform) <= 64 and all(
                character.isalnum() or character in {"-", "_", "."}
                for character in platform
            ):
                return value
        raise InvalidRequestError("source 不是受支持的历史来源")

    def require_session_id(self, session_id: Any) -> str:
        if not isinstance(session_id, str):
            raise InvalidRequestError("session_id 必须是字符串")
        value = session_id.strip()
        if not _SESSION_RE.fullmatch(value):
            raise InvalidRequestError("session_id 必须是 1–128 字符且不能包含控制字符")
        return value

    def require_client_id(self, client_id: Any, *, optional: bool = True) -> str:
        if client_id in (None, "") and optional:
            return ""
        if not isinstance(client_id, str) or not _CLIENT_ID_RE.fullmatch(client_id.strip()):
            raise InvalidRequestError("client_id 必须是 8–128 位字母、数字、下划线或连字符")
        return client_id.strip()

    def _prune_session_leases_locked(self, now: float | None = None) -> None:
        cutoff = (time.monotonic() if now is None else now) - SESSION_LEASE_TTL_SECONDS
        for key, clients in list(self._session_leases.items()):
            active = {client: seen for client, seen in clients.items() if seen >= cutoff}
            if active:
                self._session_leases[key] = active
            else:
                self._session_leases.pop(key, None)

    def _touch_session_lease_locked(
        self,
        user: str,
        source: str,
        session_id: str,
        client_id: str,
    ) -> int:
        if not client_id:
            return 0
        self._prune_session_leases_locked()
        clients = self._session_leases.setdefault((user, source, session_id), {})
        clients[client_id] = time.monotonic()
        return len(clients)

    def _release_session_lease_locked(
        self,
        user: str,
        source: str,
        session_id: str,
        client_id: str,
    ) -> int:
        self._prune_session_leases_locked()
        key = (user, source, session_id)
        clients = self._session_leases.get(key, {})
        if client_id:
            clients.pop(client_id, None)
        if clients:
            self._session_leases[key] = clients
            return len(clients)
        self._session_leases.pop(key, None)
        return 0

    def session_lease(
        self,
        user: Any,
        session_id: Any,
        client_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_client = self.require_client_id(client_id, optional=False)
        with self._active_runs_lock:
            clients = self._touch_session_lease_locked(
                name, normalized_source, normalized_session, normalized_client
            )
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "client_id": normalized_client,
            "active_clients": clients,
            "leased": True,
        }

    def release_session_lease(
        self,
        user: Any,
        session_id: Any,
        client_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_client = self.require_client_id(client_id, optional=False)
        with self._active_runs_lock:
            remaining = self._release_session_lease_locked(
                name, normalized_source, normalized_session, normalized_client
            )
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "client_id": normalized_client,
            "active_clients": remaining,
            "released": True,
        }

    def require_session_title(self, title: Any) -> str:
        if not isinstance(title, str):
            raise InvalidRequestError("title 必须是字符串")
        value = title.strip()
        if not _SESSION_TITLE_RE.fullmatch(value):
            raise InvalidRequestError("title 必须是 1–80 字符且不能包含控制字符")
        return value

    def require_prompt(self, prompt: Any) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise InvalidRequestError("prompt 必须是非空字符串")
        return prompt.strip()

    def require_content(self, content: Any) -> list[dict[str, Any]]:
        if content in (None, []):
            return []
        if not isinstance(content, list):
            raise InvalidRequestError("content 必须是 Content Block 数组")
        try:
            blocks = _CONTENT_LIST_ADAPTER.validate_python(content)
        except ValidationError as exc:
            raise InvalidRequestError("content 包含无效的多模态内容块") from exc
        media_types = (ImageContent, AudioContent, VideoContent, FileContent)
        for block in blocks:
            if isinstance(block, media_types):
                if block.source is not None:
                    raise InvalidRequestError(
                        "Web 多模态入口只接受 asset_id，不接受 URL、Base64 或本地路径"
                    )
                if not block.asset_id:
                    raise InvalidRequestError("Web 媒体内容必须提供 asset_id")
        return [block.model_dump(mode="json", exclude_none=True) for block in blocks]

    def require_run_id(self, run_id: Any) -> str:
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id.strip()):
            raise InvalidRequestError("run_id 必须是 8–128 位字母、数字、下划线或连字符")
        return run_id.strip()

    def has_active_runs(self) -> bool:
        with self._active_runs_lock:
            return bool(self._active_runs)

    def _queue_memory_extraction(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Preserve the service-level injection seam used by tests and embedders."""

        return queue_memory_extraction(*args, **kwargs)

    def _new_agent_runner(self, user: str, config: dict[str, Any]) -> AgentRunner:
        return AgentRunner(self.root, user, config=config)

    def stream_chat(
        self,
        user: Any,
        session_id: Any,
        prompt: Any,
        *,
        cancel_event: threading.Event,
        run_id: Any = "",
        content: Any = None,
        uploaded_files: Any = None,
        task_plan_id: str = "",
        task_plan_mode: str = "",
        source: Any = "web",
        client_id: Any = "",
    ) -> Iterator[RunEvent]:
        """Compatibility entry point delegated to the chat stream service."""

        return _stream_chat(
            self,
            user,
            session_id,
            prompt,
            cancel_event=cancel_event,
            run_id=run_id,
            content=content,
            uploaded_files=uploaded_files,
            task_plan_id=task_plan_id,
            task_plan_mode=task_plan_mode,
            source=source,
            client_id=client_id,
        )
    def stream_plan(
        self,
        user: Any,
        session_id: Any,
        plan_id: Any,
        *,
        cancel_event: threading.Event,
        run_id: Any = "",
        source: Any = "web",
        client_id: Any = "",
    ) -> Iterator[RunEvent]:
        name = self.require_user(user)
        normalized_session = self.require_session_id(session_id)
        normalized_source = self.require_source(source)
        normalized_plan_id = str(plan_id or "").strip()
        store = PlanStore(self.root, name)

        def claim(current: dict[str, Any]) -> dict[str, Any]:
            if (
                str(current.get("source") or "") != normalized_source
                or str(current.get("session_id") or "") != normalized_session
            ):
                raise ConflictError(
                    f"计划 {normalized_plan_id} 不属于当前对话空间"
                )
            status = str(current.get("status") or "")
            if status not in {"pending", "approved", "paused"}:
                raise ConflictError(
                    f"计划 {normalized_plan_id} 当前状态为 {status!r}，无法开始执行"
                )
            return {**current, "status": "running"}

        try:
            plan = store.update(normalized_plan_id, claim)
        except PlanNotFoundError as exc:
            raise NotFoundError(str(exc)) from None
        except ConflictError:
            raise
        except (PlanError, RuntimeError, ValueError) as exc:
            raise ConflictError(str(exc)) from None

        steps = [step for step in (plan.get("steps") or []) if isinstance(step, dict)]
        by_id = {str(step.get("step_id") or ""): step for step in steps}
        next_step = next(
            (
                step
                for step in steps
                if step.get("status") == "pending"
                and all(
                    by_id.get(str(dependency), {}).get("status") == "completed"
                    for dependency in (step.get("depends_on") or [])
                )
            ),
            None,
        )
        next_description = (
            f"{next_step.get('step_id')} - {next_step.get('title', '')}："
            f"{next_step.get('description', '')}"
            if next_step is not None
            else "请读取计划并确认剩余可执行步骤"
        )
        control_prompt = (
            "【任务计划连续执行】\n"
            f"计划 ID：{normalized_plan_id}\n"
            f"起始步骤：{next_description}\n\n"
            "这是用户批准后的单轮连续执行，不是新的用户提问。完整活跃计划已注入系统提示词。\n"
            "请严格按依赖顺序逐步执行：每次只执行一个步骤；成功后立即调用 "
            "task_plan(action=\"step_done\") 并写入结果摘要，失败时调用 step_fail。\n"
            "step_done 返回 completed_step、progress、next_step 和 plan_status。"
            "只要 plan_status=running 且 next_step 非空，就直接继续下一步，不要等待用户再次发送消息。\n"
            "当返回 completed、paused、failed 或 cancelled，或 next_step 为空时停止执行并给出最终总结。"
            "不得创建或编辑新的任务计划。"
        )
        try:
            source_events = self.stream_chat(
                name,
                normalized_session,
                control_prompt,
                cancel_event=cancel_event,
                run_id=run_id,
                task_plan_id=normalized_plan_id,
                task_plan_mode="agent_managed",
                source=normalized_source,
                client_id=client_id,
            )
        except BaseException:
            current = store.read(normalized_plan_id)
            if current.get("status") == "running":
                store.update(normalized_plan_id, lambda value: {**value, "status": "paused"})
            raise

        def events() -> Iterator[RunEvent]:
            try:
                yield from source_events
            finally:
                close = getattr(source_events, "close", None)
                if callable(close):
                    try:
                        close()
                    except BaseException:
                        # 关闭底层生成器失败不能跳过计划状态收口。
                        pass
                try:
                    current = store.read(normalized_plan_id)
                    if current.get("status") == "running":
                        store.update(
                            normalized_plan_id,
                            lambda value: {**value, "status": "paused"},
                        )
                except PlanError:
                    pass

        return events()
