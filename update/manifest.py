"""Version manifest validation, comparison and final commit helpers."""

from __future__ import annotations

import copy
from pathlib import Path

from ._utils import (
    UpdateError,
    compare_versions,
    fetch_json,
    green,
    parse_version,
    read_json,
    write_json_atomic,
)
from .constants import MODULES, ROOT


def load_local_version_document(*, root: Path = ROOT) -> dict:
    path = root / "version.json"
    if not path.is_file():
        raise UpdateError(f"未找到本地版本文件: {path}")
    document = read_json(path)
    validate_version_document(document, "本地")
    return document


def load_remote_version_document(remote_url: str) -> dict:
    document = fetch_json(remote_url)
    validate_version_document(document, "远程")
    return document


def load_source_version_document(source_root: Path) -> dict:
    path = source_root / "version.json"
    if not path.is_file():
        raise UpdateError(f"克隆源码缺少版本文件: {path}")
    document = read_json(path)
    validate_version_document(document, "克隆源码")
    return document


def load_version_documents(remote_url: str, *, root: Path = ROOT) -> tuple[dict, dict]:
    return load_local_version_document(root=root), load_remote_version_document(remote_url)


def validate_version_document(document: dict, label: str) -> None:
    try:
        version_for_module(document, "all")
        for module_name in MODULES:
            version_for_module(document, module_name)
    except UpdateError as exc:
        raise UpdateError(f"{label}版本文件无效: {exc}") from exc


def version_for_module(document: dict, module: str) -> str:
    if module == "all":
        value = document.get("version")
    else:
        components = document.get("components")
        if not isinstance(components, dict) or not isinstance(
            components.get(module), dict
        ):
            raise UpdateError(f"version.json 缺少 components.{module}")
        value = components[module].get("version")
    version = str(value or "").strip()
    if not version:
        field = "version" if module == "all" else f"components.{module}.version"
        raise UpdateError(f"version.json 缺少 {field}")
    parse_version(version)
    return version


def load_versions(
    remote_url: str,
    module: str = "all",
    *,
    root: Path = ROOT,
) -> tuple[str, str]:
    local, remote = load_version_documents(remote_url, root=root)
    return version_for_module(local, module), version_for_module(remote, module)


def verify_source_manifest(remote_document: dict, source_document: dict) -> None:
    """Reject a branch race or a version URL that describes another source."""

    if remote_document != source_document:
        raise UpdateError(
            "远程 version.json 与实际克隆源码不一致；远程分支可能在更新检查后发生变化，"
            "或 --repo-url 与 --remote-version-url 指向了不同仓库。为避免安装错版源码，"
            "本轮更新已停止，请重新执行。"
        )


def ensure_no_downgrade(
    local_document: dict,
    remote_document: dict,
    requested_module: str,
) -> None:
    """Reject a full update that would downgrade any selected component.

    A partial component update can legitimately leave component versions
    different from the root version.  Comparing only the root manifest during
    a later ``--module all`` update could therefore silently replace a newer
    local component with an older remote one.  The check is read-only and is
    performed before any backup or board writes.
    """

    names = (
        ["all", *MODULES]
        if requested_module == "all"
        else [requested_module]
    )
    downgraded: list[str] = []
    for name in names:
        local_version = version_for_module(local_document, name)
        remote_version = version_for_module(remote_document, name)
        if compare_versions(local_version, remote_version) > 0:
            label = "全部" if name == "all" else MODULES[name][0]
            downgraded.append(f"{label} {local_version} -> {remote_version}")
    if downgraded:
        raise UpdateError(
            "拒绝降级：本地版本高于远程版本（"
            + "; ".join(downgraded)
            + "）。当前更新器不提供自动降级；如需恢复旧版本，请使用经过验证的备份。"
        )


def version_document_after_update(
    local_document: dict,
    remote_document: dict,
    requested_module: str,
) -> dict:
    """Build the manifest committed after a successful update."""

    if requested_module == "all":
        return copy.deepcopy(remote_document)
    local_components = local_document.get("components")
    remote_components = remote_document.get("components")
    if not isinstance(local_components, dict):
        raise UpdateError("本地 version.json 缺少 components")
    if not isinstance(remote_components, dict) or not isinstance(
        remote_components.get(requested_module), dict
    ):
        raise UpdateError(f"远程 version.json 缺少 components.{requested_module}")
    result = copy.deepcopy(local_document)
    result_components = result.setdefault("components", {})
    result_components[requested_module] = copy.deepcopy(
        remote_components[requested_module]
    )
    return result


def finalize_version_document(
    local_document: dict,
    remote_document: dict,
    requested_module: str,
    *,
    dry_run: bool,
    root: Path = ROOT,
) -> None:
    final_document = version_document_after_update(
        local_document,
        remote_document,
        requested_module,
    )
    target = root / "version.json"
    final_version = version_for_module(final_document, requested_module)
    if dry_run:
        print(
            f"[dry-run] 将在全部步骤成功后写入 {requested_module} 版本: {final_version}"
        )
        return
    try:
        write_json_atomic(target, final_document)
    except Exception as exc:
        raise UpdateError(f"版本文件写入失败: {exc}") from exc
    print(green(f"版本状态已提交: {requested_module} {final_version}"))


def print_version_report(local: dict, remote: dict, requested_module: str) -> None:
    names = ["all", *MODULES] if requested_module == "all" else [requested_module]
    for name in names:
        local_version = version_for_module(local, name)
        remote_version = version_for_module(remote, name)
        comparison = compare_versions(local_version, remote_version)
        label = "全部" if name == "all" else MODULES[name][0]
        state = (
            "最新" if comparison == 0 else "可更新" if comparison < 0 else "本地较新"
        )
        print(
            f"{label:<10} 本地 {local_version:<12} "
            f"远程 {remote_version:<12} {state}"
        )
