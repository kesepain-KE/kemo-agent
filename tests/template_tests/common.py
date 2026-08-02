"""Sandbox and Python-entry probes used by template conformance checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class ContractValidationError(RuntimeError):
    """A candidate does not satisfy one of the public framework contracts."""


class ContractDependencyMissing(ContractValidationError):
    def __init__(self, dependency: str, message: str = "") -> None:
        self.dependency = dependency
        super().__init__(message or f"缺少运行依赖：{dependency}")


def read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8-sig"))
    except FileNotFoundError:
        raise ContractValidationError(f"{label}不存在：{path.name}") from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{label}不可读或 JSON 无效：{exc}") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label}顶层必须是 JSON 对象")
    return value


def ensure_plain_directory(path: Path) -> Path:
    target = path.resolve()
    if not target.is_dir():
        raise ContractValidationError(f"目标不是目录：{target}")
    if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
        raise ContractValidationError("目标目录不能是符号链接或目录联接")
    return target


def copy_candidate(source: Path, destination: Path) -> Path:
    source = ensure_plain_directory(source)
    if destination.exists():
        raise ContractValidationError(f"沙箱目标已经存在：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True)
    return destination


def _copy_if_file(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def prepare_root_layout(root: Path, *, repository_root: Path = PROJECT_ROOT) -> None:
    for relative in (
        "agents",
        "plugins",
        "shared_skills",
        "shared_knowledge",
        "global_knowledge",
        "global_expand",
        "shared_expand",
        "global_sense",
        "message/out",
        "config",
        "users",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    _copy_if_file(
        repository_root / "config" / "global_config.json",
        root / "config" / "global_config.json",
    )
    if not (root / "config" / "global_config.json").is_file():
        (root / "config" / "global_config.json").write_text("{}\n", "utf-8")
    _copy_if_file(
        repository_root / "config" / "global_soul.md",
        root / "config" / "global_soul.md",
    )
    _copy_if_file(repository_root / "agents.md", root / "agents.md")
    for relative in (
        "global_expand/register.py",
        "shared_expand/register.py",
        "shared_skills/register.py",
        "global_sense/register.py",
    ):
        _copy_if_file(repository_root / relative, root / relative)


def prepare_user(
    root: Path, user: str, *, config: dict[str, Any] | None = None
) -> Path:
    directory = root / "users" / user
    for relative in (
        "agents",
        "expand",
        "user_skills/agent_create",
        "user_skills/user_create",
        "knowledge",
        "improve",
        "download",
        "file_upload",
        "history",
        "task_cron",
        "task_plan",
    ):
        (directory / relative).mkdir(parents=True, exist_ok=True)
    config_path = directory / "user_config.json"
    if not config_path.exists():
        config_path.write_text(
            json.dumps(config or {}, ensure_ascii=False, indent=2) + "\n",
            "utf-8",
        )
    return directory


@contextmanager
def sandbox(*, repository_root: Path = PROJECT_ROOT) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="kemo-template-contract-") as temporary:
        root = Path(temporary).resolve()
        prepare_root_layout(root, repository_root=repository_root)
        yield root


_IMPORT_PROBE = r"""
import importlib
import importlib.util
import inspect
import json
import sys
from pathlib import Path

MARKER = "KEMO_TEMPLATE_TEST_RESULT="
path = Path(sys.argv[1]).resolve()
specification = json.loads(sys.argv[2])
project_root = Path(sys.argv[3]).resolve()
sandbox_root = Path(sys.argv[4]).resolve()
sys.path.insert(0, str(path.parent))
sys.path.insert(0, str(sandbox_root))
sys.path.insert(0, str(project_root))
try:
    message_root = sandbox_root / "message"
    if message_root.is_dir():
        package = importlib.import_module("message")
        package_path = getattr(package, "__path__", None)
        if package_path is not None and str(message_root) not in package_path:
            package_path.insert(0, str(message_root))
    module_spec = importlib.util.spec_from_file_location(
        "_kemo_template_contract_probe", path
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError("无法创建 Python 模块加载器")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    checked = []
    for name, arity in specification.get("required", {}).items():
        function = getattr(module, name, None)
        if not callable(function):
            raise TypeError(f"缺少可调用的 {name}()")
        inspect.signature(function).bind(*([None] * int(arity)))
        if inspect.iscoroutinefunction(function):
            raise TypeError(f"{name}() 必须是同步函数")
        checked.append(name)
    alternatives = specification.get("one_of", [])
    if alternatives:
        selected = None
        errors = []
        for name, arity in alternatives:
            function = getattr(module, name, None)
            if not callable(function):
                errors.append(f"{name} 不可调用")
                continue
            try:
                inspect.signature(function).bind(*([None] * int(arity)))
            except TypeError as exc:
                errors.append(f"{name}: {exc}")
                continue
            if inspect.iscoroutinefunction(function):
                errors.append(f"{name} 必须是同步函数")
                continue
            selected = name
            checked.append(name)
            break
        if selected is None:
            raise TypeError("入口候选均不符合合同：" + "；".join(errors))
    result = {"ok": True, "functions": checked}
except ModuleNotFoundError as exc:
    result = {
        "ok": False,
        "category": "dependency_missing",
        "dependency": exc.name or "unknown",
        "error": str(exc),
    }
except BaseException as exc:
    result = {
        "ok": False,
        "category": "contract_error",
        "exception_type": type(exc).__name__,
        "error": str(exc),
    }
print(MARKER + json.dumps(result, ensure_ascii=False, default=str))
"""


def hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def probe_python_entry(
    path: Path,
    *,
    required: dict[str, int] | None = None,
    one_of: tuple[tuple[str, int], ...] = (),
    sandbox_root: Path,
    repository_root: Path = PROJECT_ROOT,
    timeout: float = 10.0,
) -> dict[str, Any]:
    specification = {
        "required": required or {},
        "one_of": [list(item) for item in one_of],
    }
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _IMPORT_PROBE,
                str(path),
                json.dumps(specification, ensure_ascii=False),
                str(repository_root),
                str(sandbox_root),
            ],
            cwd=path.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.1, float(timeout)),
            startupinfo=hidden_startupinfo(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ContractValidationError(
            f"导入入口超过 {float(timeout):g} 秒，疑似在导入阶段阻塞"
        ) from exc
    marker = "KEMO_TEMPLATE_TEST_RESULT="
    payload_line = next(
        (
            line[len(marker) :]
            for line in reversed(completed.stdout.splitlines())
            if line.startswith(marker)
        ),
        "",
    )
    if not payload_line:
        detail = (completed.stderr or completed.stdout).strip()[-800:]
        raise ContractValidationError(
            "入口探测进程没有返回合同结果" + (f"：{detail}" if detail else "")
        )
    try:
        payload = json.loads(payload_line)
    except json.JSONDecodeError as exc:
        raise ContractValidationError("入口探测结果不是有效 JSON") from exc
    if payload.get("ok") is True:
        return payload
    if payload.get("category") == "dependency_missing":
        raise ContractDependencyMissing(
            str(payload.get("dependency") or "unknown"),
            str(payload.get("error") or ""),
        )
    raise ContractValidationError(str(payload.get("error") or "入口合同不符合要求"))
