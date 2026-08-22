"""Safe discovery, invocation and runtime diagnostics for Expand modules."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from run.extensions.attachments import validate_media_file
from run.config import load_config
from run.extensions.module_runtime import module_execution_lock, run_protocol_process
from run.config import ExpandMeta, read_expand_meta
from run.config import MainAgentSourcePolicy


EXPAND_CALL_RESULT_PREFIX = "__KEMO_EXPAND_CALL_RESULT__="
_SCOPES = frozenset({"global", "shared", "user"})
_MODULE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_COMMAND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_MAX_REQUEST_CHARS = 256_000
_MAX_RESULT_CHARS = 256_000
_MAX_ARTIFACTS = 32
_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_MAX_ARTIFACT_TOTAL_BYTES = 1024 * 1024 * 1024
_MAX_RESOURCES = 64
_STATE_FILE = "_runtime.json"
_LOCK_FILE = ".runtime.lock"
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


EXPAND_CALL_RUNNER = r'''
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path

PREFIX = "__KEMO_EXPAND_CALL_RESULT__="

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="strict")


def emit(payload: dict, *, stream) -> None:
    print(
        PREFIX + json.dumps(payload, ensure_ascii=False, default=str),
        file=stream,
        flush=True,
    )


try:
    entry_path = Path(sys.argv[1]).resolve()
    module_root = Path(sys.argv[2]).resolve()
    request = json.loads(sys.stdin.read())
    if not isinstance(request, dict):
        raise TypeError("拓展调用请求必须是 JSON 对象")
    command = request.get("command")
    params = request.get("params", {})
    context = request.get("context", {})
    if not isinstance(command, str) or not command:
        raise ValueError("拓展调用缺少 command")
    if not isinstance(params, dict):
        raise TypeError("拓展调用 params 必须是 JSON 对象")
    if not isinstance(context, dict):
        raise TypeError("拓展调用 context 必须是 JSON 对象")

    sys.path.insert(0, str(module_root))
    if entry_path.parent != module_root:
        sys.path.insert(0, str(entry_path.parent))
    spec = importlib.util.spec_from_file_location("__kemo_expand_call__", str(entry_path))
    if spec is None or spec.loader is None:
        raise ImportError("无法创建拓展操作入口加载器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    execute = getattr(module, "execute", None)
    if not callable(execute):
        raise AttributeError("start_expand.py 必须提供可调用的 execute()")

    signature = inspect.signature(execute)
    try:
        signature.bind(command, params, context=context)
    except TypeError:
        try:
            signature.bind(command, params)
        except TypeError:
            legacy = dict(params)
            legacy.setdefault("action", command)
            try:
                signature.bind(legacy, context=context)
            except TypeError:
                try:
                    signature.bind(legacy)
                except TypeError as exc:
                    raise TypeError(
                        "execute() 必须兼容 execute(command, params) 或 execute(command_dict)"
                    ) from exc
                result = execute(legacy)
            else:
                result = execute(legacy, context=context)
        else:
            result = execute(command, params)
    else:
        result = execute(command, params, context=context)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            pass
    emit({"ok": True, "result": result}, stream=sys.stdout)
except BaseException as exc:
    emit(
        {
            "ok": False,
            "reason": str(exc) or type(exc).__name__,
            "exception_type": type(exc).__name__,
        },
        stream=sys.stderr,
    )
    raise SystemExit(1)
'''


class ExpandRuntimeError(RuntimeError):
    pass


class ExpandOperationError(ExpandRuntimeError):
    category = "expand_operation_error"
    retryable = False


def _failure_value_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value).strip()


def _operation_failure_reason(result: dict[str, Any]) -> str:
    """Keep structured child failures visible instead of replacing them with a generic error."""

    direct = result.get("error") or result.get("message") or result.get("reason")
    direct_text = _failure_value_text(direct)
    if direct_text:
        return direct_text[:4000]

    details: list[str] = []
    for collection_name in ("domains", "failures", "results", "items"):
        collection = result.get(collection_name)
        if not isinstance(collection, list):
            continue
        for index, item in enumerate(collection):
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().casefold()
            failed = item.get("ok") is False or status in {"error", "failed", "failure", "source_missing"}
            if not failed:
                continue
            identity = next(
                (
                    str(item.get(key)).strip()
                    for key in ("domain_id", "source_uri", "name", "id")
                    if item.get(key) not in (None, "")
                ),
                f"{collection_name}[{index}]",
            )
            detail = (
                item.get("error")
                or item.get("message")
                or item.get("reason")
                or item.get("status")
                or "failed"
            )
            details.append(f"{identity}: {_failure_value_text(detail)}")
    return ("；".join(details) or "拓展操作返回失败状态")[:4000]


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(
            getattr(path, "is_junction", lambda: False)()
        )
    except OSError:
        return True


def _scope_root(root: Path, user: str, scope: str) -> Path:
    if scope == "global":
        return root / "global_expand"
    if scope == "shared":
        return root / "shared_expand"
    return root / "users" / user / "expand"


def _reject_link_components(base: Path, target: Path) -> None:
    resolved_base = base.resolve()
    try:
        relative = target.relative_to(resolved_base)
    except ValueError as exc:
        raise ExpandRuntimeError("拓展路径越出对应作用域") from exc
    if ".." in relative.parts:
        raise ExpandRuntimeError("拓展路径越出对应作用域")
    current = resolved_base
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_link(current):
            raise ExpandRuntimeError("拓展路径不允许经过符号链接或目录联接")


def resolve_expand(
    root: Path,
    user: str,
    scope: str,
    module: str,
    *,
    require_control: bool = True,
) -> tuple[Path, ExpandMeta]:
    """Resolve one module using the current user's Expand source policy."""

    root = root.resolve()
    normalized_scope = str(scope or "").strip()
    normalized_module = str(module or "").strip()
    if normalized_scope not in _SCOPES:
        raise ExpandRuntimeError("scope 只允许 global、shared 或 user")
    if not _MODULE_RE.fullmatch(normalized_module):
        raise ExpandRuntimeError("module 必须是合法的拓展目录名")

    config = load_config(user, root)
    policy = MainAgentSourcePolicy.from_config(config)
    if normalized_scope == "global" and not policy.global_expand.allows(normalized_module):
        raise ExpandRuntimeError(f"全局拓展未进入当前用户白名单：{normalized_module}")
    if normalized_scope == "shared" and not policy.shared_expand.allows(normalized_module):
        raise ExpandRuntimeError(f"共享拓展未进入当前用户白名单：{normalized_module}")

    base = _scope_root(root, user, normalized_scope).resolve()
    module_dir = base / normalized_module
    _reject_link_components(base, module_dir)
    if not module_dir.is_dir() or _is_link(module_dir):
        raise ExpandRuntimeError(
            f"拓展模块不存在或不是安全目录：{normalized_scope}:{normalized_module}"
        )
    try:
        module_dir.resolve().relative_to(base)
    except ValueError as exc:
        raise ExpandRuntimeError("拓展模块解析后越出对应作用域") from exc

    meta = read_expand_meta(module_dir)
    if not meta.valid:
        raise ExpandRuntimeError(f"拓展模块配置无效：{meta.error}")
    if require_control and not meta.open_control:
        raise ExpandRuntimeError(f"拓展模块没有开放操控能力：{normalized_module}")
    return module_dir, meta


def _thread_lock(module_root: Path) -> threading.RLock:
    key = str(module_root.resolve()).casefold()
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _state_lock(module_root: Path) -> Iterator[None]:
    with _thread_lock(module_root):
        lock_path = module_root / _LOCK_FILE
        if _is_link(lock_path):
            raise ExpandRuntimeError("拓展状态锁文件不能是符号链接或目录联接")
        handle = lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _atomic_json(path: Path, payload: dict[str, Any]) -> bool:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        if path.read_text("utf-8") == rendered:
            return False
    except (OSError, UnicodeError):
        pass
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def read_expand_runtime(module_root: Path) -> dict[str, Any]:
    path = module_root / _STATE_FILE
    if not path.is_file() or _is_link(path):
        return {"schema_version": 1}
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"schema_version": 1}
    return value if isinstance(value, dict) else {"schema_version": 1}


def _resource_descriptors(module_root: Path, result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or not isinstance(result.get("resources"), list):
        return []
    resources: list[dict[str, Any]] = []
    for item in result["resources"][:_MAX_RESOURCES]:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        candidate = Path(raw_path.strip())
        target = candidate if candidate.is_absolute() else module_root / candidate
        try:
            resolved = target.resolve(strict=True)
            relative = resolved.relative_to(module_root.resolve())
        except (OSError, ValueError):
            continue
        _reject_link_components(module_root, target)
        if not resolved.is_file() or _is_link(resolved):
            continue
        stat = resolved.stat()
        resources.append(
            {
                "path": relative.as_posix(),
                "kind": str(item.get("kind") or "file")[:64],
                "label": str(item.get("label") or item.get("name") or resolved.name)[:256],
                "mime_type": mimetypes.guess_type(resolved.name)[0]
                or "application/octet-stream",
                "size": stat.st_size,
                "updated_at": stat.st_mtime,
            }
        )
    return resources


def record_expand_runtime(
    module_root: Path,
    channel: str,
    *,
    ok: bool,
    duration_ms: int,
    result: Any = None,
    command: str = "",
    error: BaseException | str | None = None,
) -> dict[str, Any]:
    """Atomically record small framework-owned update/control diagnostics."""

    if channel not in {"update", "control"}:
        raise ValueError("Expand runtime channel 必须是 update 或 control")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with _state_lock(module_root):
        state = read_expand_runtime(module_root)
        previous = state.get(channel) if isinstance(state.get(channel), dict) else {}
        section: dict[str, Any] = {
            "status": "completed" if ok else "failed",
            "last_attempt": now,
            "last_success": now if ok else previous.get("last_success"),
            "duration_ms": max(0, int(duration_ms)),
            "error": None,
        }
        if channel == "update":
            resources = _resource_descriptors(module_root, result) if ok else []
            section["resource_count"] = len(resources)
            section["resources"] = resources
        else:
            section["last_command"] = command
            if isinstance(result, dict):
                artifacts = result.get("artifacts")
                if isinstance(artifacts, list):
                    section["artifact_count"] = len(artifacts)
                if isinstance(result.get("state_changed"), bool):
                    section["state_changed"] = result["state_changed"]
        if not ok:
            if isinstance(error, BaseException):
                error_type = type(error).__name__
                message = str(error)
            else:
                error_type = "ExpandRuntimeError"
                message = str(error or "拓展运行失败")
            section["error"] = {
                "type": error_type,
                "message": message[-1000:],
            }
        state["schema_version"] = 1
        state[channel] = section
        _atomic_json(module_root / _STATE_FILE, state)
        return state


def _safe_artifact_name(value: Any, fallback: str) -> str:
    name = Path(str(value or fallback).replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", name)
    name = name.rstrip(" .")
    if name in {"", ".", ".."}:
        name = fallback
    reserved = {
        "con", "prn", "aux", "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    if name.split(".", 1)[0].casefold() in reserved:
        name = f"_{name}"
    return name[:180]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_artifacts(
    root: Path,
    user: str,
    module_root: Path,
    result: Any,
) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or result.get("artifacts") is None:
        return []
    raw_artifacts = result.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ExpandOperationError("artifacts 必须是数组")
    if len(raw_artifacts) > _MAX_ARTIFACTS:
        raise ExpandOperationError(f"单次拓展操作最多返回 {_MAX_ARTIFACTS} 个产物")

    users_root = (root / "users").resolve()
    raw_output_dir = users_root / user / "download"
    try:
        output_dir = raw_output_dir.resolve()
        output_dir.relative_to(users_root)
    except ValueError as exc:
        raise ExpandOperationError("用户下载目录越出 users 目录") from exc
    _reject_link_components(users_root, raw_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    published: list[dict[str, Any]] = []
    created_targets: list[Path] = []
    total_size = 0
    try:
        for index, item in enumerate(raw_artifacts, 1):
            if not isinstance(item, dict):
                raise ExpandOperationError(f"第 {index} 个 artifact 必须是对象")
            raw_path = item.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ExpandOperationError(f"第 {index} 个 artifact 缺少 path")
            candidate = Path(raw_path.strip())
            source = candidate if candidate.is_absolute() else module_root / candidate
            try:
                resolved = source.resolve(strict=True)
                resolved.relative_to(module_root.resolve())
            except (OSError, ValueError) as exc:
                raise ExpandOperationError(
                    f"artifact 必须是拓展模块目录内的普通文件：{raw_path}"
                ) from exc
            _reject_link_components(module_root, source)
            if not resolved.is_file() or _is_link(resolved):
                raise ExpandOperationError(f"artifact 不是安全的普通文件：{raw_path}")
            size = resolved.stat().st_size
            if size > _MAX_ARTIFACT_BYTES:
                raise ExpandOperationError(
                    f"artifact 超过 {_MAX_ARTIFACT_BYTES // (1024 * 1024)} MB：{raw_path}"
                )
            total_size += size
            if total_size > _MAX_ARTIFACT_TOTAL_BYTES:
                raise ExpandOperationError(
                    "单次拓展操作的 artifact 总大小超过 1024 MB"
                )
            filename = _safe_artifact_name(item.get("name"), resolved.name)
            target = output_dir / filename
            if target.exists():
                target = output_dir / f"{Path(filename).stem}_{uuid.uuid4().hex[:8]}{Path(filename).suffix}"
            temporary = output_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
            try:
                with resolved.open("rb") as source_handle, temporary.open("wb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
                    target_handle.flush()
                    os.fsync(target_handle.fileno())
                os.replace(temporary, target)
                created_targets.append(target)
            finally:
                temporary.unlink(missing_ok=True)
            mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            media_type = str(item.get("kind") or "").casefold()
            if media_type not in {"image", "audio", "video", "file"}:
                media_type = (
                    "image" if mime_type.startswith("image/")
                    else "audio" if mime_type.startswith("audio/")
                    else "video" if mime_type.startswith("video/")
                    else "file"
                )
            if media_type in {"image", "audio", "video"} and not validate_media_file(
                target, media_type
            ):
                raise ExpandOperationError(
                    f"artifact 内容与声明的 {media_type} 类型不匹配：{raw_path}"
                )
            published.append(
                {
                    "asset_id": f"expand_{uuid.uuid4().hex}",
                    "type": media_type,
                    "name": target.name,
                    "scope": "download",
                    "path": target.name,
                    "project_path": target.relative_to(root).as_posix(),
                    "mime_type": mime_type,
                    "size": target.stat().st_size,
                    "checksum_sha256": _sha256(target),
                }
            )
    except Exception:
        for target in created_targets:
            target.unlink(missing_ok=True)
        raise
    return published


def invoke_expand(
    *,
    root: Path,
    user: str,
    scope: str,
    module: str,
    command: str,
    params: dict[str, Any] | None,
    timeout: float,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Invoke one Expand operation in a child process and publish its artifacts."""

    if not _COMMAND_RE.fullmatch(str(command or "").strip()):
        raise ExpandRuntimeError("command 必须是合法的非空命令名")
    if params is not None and not isinstance(params, dict):
        raise ExpandRuntimeError("params 必须是 JSON 对象")
    module_root, meta = resolve_expand(root, user, scope, module)
    entry = module_root / meta.start_expand
    _reject_link_components(module_root, entry)
    if not entry.is_file() or _is_link(entry):
        raise ExpandRuntimeError("start_expand 不是安全的普通文件")
    request_text = json.dumps(
        {
            "command": command,
            "params": params or {},
            "context": {
                "user": user,
                "scope": scope,
                "module": module,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(request_text) > _MAX_REQUEST_CHARS:
        raise ExpandRuntimeError(f"拓展调用参数超过 {_MAX_REQUEST_CHARS} 字符")

    started = time.monotonic()
    try:
        with module_execution_lock(
            module_root,
            timeout=timeout,
            cancel_event=cancel_event,
        ):
            remaining_timeout = max(
                0.1,
                float(timeout) - (time.monotonic() - started),
            )
            returncode, payload, stdout, stderr = run_protocol_process(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    EXPAND_CALL_RUNNER,
                    str(entry),
                    str(module_root),
                ],
                cwd=module_root,
                timeout=remaining_timeout,
                result_prefix=EXPAND_CALL_RESULT_PREFIX,
                stdin_payload=request_text,
                cancel_event=cancel_event,
            )
            if returncode != 0 or not payload or payload.get("ok") is not True:
                reason = (
                    str((payload or {}).get("reason") or "").strip()
                    or (stderr or stdout).strip()[-1000:]
                    or "拓展操作子进程未返回可识别结果"
                )
                raise ExpandOperationError(reason)
            result = payload.get("result")
            serialized = json.dumps(result, ensure_ascii=False, default=str)
            if len(serialized) > _MAX_RESULT_CHARS:
                raise ExpandOperationError(
                    "拓展操作结果过大；请将大型 DOM、日志或二进制内容保存为 artifact"
                )
            if isinstance(result, dict):
                status = str(result.get("status") or "").strip().casefold()
                if result.get("ok") is False or status in {"error", "failed", "failure"}:
                    raise ExpandOperationError(_operation_failure_reason(result))
            try:
                artifacts = _publish_artifacts(root.resolve(), user, module_root, result)
            except ExpandOperationError as exc:
                raise ExpandOperationError(
                    f"{exc}；外部操作可能已经执行，请先核对状态，不要直接重试"
                ) from exc
            public_result = result
            if isinstance(result, dict) and "artifacts" in result:
                public_result = {**result, "artifacts": artifacts}
    except Exception as exc:
        elapsed = round((time.monotonic() - started) * 1000)
        try:
            record_expand_runtime(
                module_root,
                "control",
                ok=False,
                duration_ms=elapsed,
                command=command,
                error=exc,
            )
        except Exception:
            pass
        raise
    elapsed = round((time.monotonic() - started) * 1000)
    record_expand_runtime(
        module_root,
        "control",
        ok=True,
        duration_ms=elapsed,
        command=command,
        result={"artifacts": artifacts},
    )
    return {
        "scope": scope,
        "module": module,
        "command": command,
        "duration_ms": elapsed,
        "result": public_result,
        "artifacts": artifacts,
    }
