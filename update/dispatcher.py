"""Remote board loading and ordered module dispatch.

This module owns the board contract.  It deliberately knows nothing about
backups, CLI parsing or user migrations, which keeps the updater easy to test
and prevents the root entrypoint from becoming a second implementation.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path

from ._utils import UpdateError
from .constants import MODULES, REMOTE_UPDATE_PACKAGE


def _clear_remote_update_package() -> None:
    prefix = REMOTE_UPDATE_PACKAGE + "."
    for module_name in list(sys.modules):
        if module_name == REMOTE_UPDATE_PACKAGE or module_name.startswith(prefix):
            sys.modules.pop(module_name, None)


def load_remote_update_package(source_root: Path) -> str:
    """Load board code from the freshly cloned source under an isolated name."""

    update_dir = source_root / "update"
    init_path = update_dir / "__init__.py"
    if not init_path.is_file():
        raise UpdateError(f"远程源码缺少更新模块入口: {init_path}")
    _clear_remote_update_package()
    importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(
        REMOTE_UPDATE_PACKAGE,
        init_path,
        submodule_search_locations=[str(update_dir)],
    )
    if spec is None or spec.loader is None:
        raise UpdateError(f"无法加载远程更新模块: {init_path}")
    package = importlib.util.module_from_spec(spec)
    sys.modules[REMOTE_UPDATE_PACKAGE] = package
    try:
        spec.loader.exec_module(package)
    except Exception as exc:
        _clear_remote_update_package()
        raise UpdateError(f"远程更新模块入口加载失败: {exc}") from exc
    return REMOTE_UPDATE_PACKAGE


def _call_board_update(
    module,
    *,
    source_root: Path,
    target_root: Path,
    dry_run: bool,
    assume_yes: bool,
    replace_global_config: bool,
) -> dict:
    parameters = inspect.signature(module.update).parameters
    kwargs = {
        "dry_run": dry_run,
        "assume_yes": assume_yes,
    }
    if "legacy_core_compat" in parameters:
        # The old 0.1.x → 0.2.x bridge is only needed by the old dispatcher;
        # the current dispatcher updates provider/README_EN in core itself.
        kwargs["legacy_core_compat"] = False
    if "replace_global_config" in parameters:
        kwargs["replace_global_config"] = replace_global_config
    return module.update(source_root, target_root, **kwargs)


def run_modules(
    module_names: list[str],
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool,
    assume_yes: bool,
    replace_global_config: bool = False,
    stop_on_failure: bool = True,
) -> list[dict]:
    """Run boards in order and stop at the first failed/partial board."""

    results: list[dict] = []
    remote_package = load_remote_update_package(source_root)
    try:
        for name in module_names:
            label, configured_import_path = MODULES[name]
            board_name = configured_import_path.rsplit(".", 1)[-1]
            import_path = f"{remote_package}.{board_name}"
            print(f"\n{'=' * 50}")
            print(f"  板块: {label}")
            print(f"{'=' * 50}")
            try:
                importlib.invalidate_caches()
                module = importlib.import_module(import_path)
                result = _call_board_update(
                    module,
                    source_root=source_root,
                    target_root=target_root,
                    dry_run=dry_run,
                    assume_yes=assume_yes,
                    replace_global_config=replace_global_config,
                )
                if not isinstance(result, dict):
                    raise UpdateError(f"{import_path}.update() 未返回字典")
                status = str(result.get("status", ""))
                if status not in {"ok", "skipped", "partial", "failed"}:
                    raise UpdateError(f"{import_path}.update() 返回无效状态: {status!r}")
                result.setdefault("module", name)
                result.setdefault("details", [])
                result.setdefault("warnings", [])
            except Exception as exc:
                result = {
                    "module": name,
                    "status": "failed",
                    "details": [],
                    "warnings": [str(exc)],
                }
            results.append(result)
            if stop_on_failure and result.get("status") in {"failed", "partial"}:
                break
    finally:
        _clear_remote_update_package()
    return results
