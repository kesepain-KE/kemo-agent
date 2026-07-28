"""Recursive prompt-skill package contract checks."""

from __future__ import annotations

import re
from pathlib import Path

from run.prompt_sources import load_prompt_source_registry, parse_skill_descriptor

from tests.template_tests.base import begin_report
from tests.template_tests.common import (
    PROJECT_ROOT,
    copy_candidate,
    prepare_user,
    sandbox,
)
from tests.template_tests.contracts import ContractReport


_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_TEMPLATE_MARKER = re.compile(r"\{\{[^{}]+\}\}")


def validate(
    target: Path,
    *,
    repository_root: Path = PROJECT_ROOT,
    timeout: float = 10.0,
    template_mode: bool = False,
    runtime_probe: bool = True,
) -> ContractReport:
    del timeout, runtime_probe
    report, directory = begin_report("skills", target)
    if directory is None:
        return report
    try:
        _validate(
            report,
            directory,
            repository_root=repository_root.resolve(),
            template_mode=bool(template_mode),
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        report.failed("skills.validator", f"验收器未能完成：{exc}", exception=type(exc).__name__)
    return report


def _skill_paths(target: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path
                for path in target.rglob("*")
                if path.is_file() and path.name.casefold() == "skill.md"
            ),
            key=lambda path: path.relative_to(target).as_posix().casefold(),
        )
    )


def _validate(
    report: ContractReport,
    target: Path,
    *,
    repository_root: Path,
    template_mode: bool,
) -> None:
    paths = _skill_paths(target)
    if not paths:
        report.failed("skills.discovery", "目录内没有递归找到 SKILL.md")
        return
    descriptors = []
    for path in paths:
        try:
            descriptor = parse_skill_descriptor(path, scope="candidate", root=target)
        except BaseException as exc:
            report.failed("skills.descriptor", str(exc), file=str(path.relative_to(target)))
            continue
        text = path.read_text("utf-8-sig")
        if _TEMPLATE_MARKER.search(descriptor.title + descriptor.description):
            if template_mode:
                report.warning(
                    "skills.placeholder",
                    "参考模板仍保留标题/描述占位符，复制后必须替换",
                    file=str(path.relative_to(target)),
                )
            else:
                report.failed(
                    "skills.placeholder",
                    "技能标题或描述仍包含模板占位符",
                    file=str(path.relative_to(target)),
                )
                continue
        if not descriptor.description.strip():
            report.failed(
                "skills.description",
                "SKILL.md 一级标题后的发现描述不能为空",
                file=str(path.relative_to(target)),
            )
            continue
        descriptors.append(descriptor)
        missing_links: list[str] = []
        for raw_link in _MARKDOWN_LINK.findall(text):
            link = raw_link.strip().split(maxsplit=1)[0].strip("<>")
            if not link or link.startswith(("#", "http://", "https://", "data:")):
                continue
            path_part = link.split("#", 1)[0]
            if path_part and not (path.parent / path_part).exists():
                missing_links.append(path_part)
        if missing_links:
            report.warning(
                "skills.relative_resources",
                "技能包含当前目录无法解析的相对 Markdown 资源",
                file=str(path.relative_to(target)),
                missing=sorted(set(missing_links)),
            )
    if len(descriptors) != len(paths):
        return
    report.passed(
        "skills.descriptor",
        "所有递归 SKILL.md 均有可发现的标题与描述",
        count=len(descriptors),
    )
    user = "contract_user"
    with sandbox(repository_root=repository_root) as root:
        prepare_user(root, user)
        copy_candidate(target, root / "users" / user / "user_skills" / "candidate")
        registry = load_prompt_source_registry(root, user)
        selected = registry.select_skills()
        candidate_selected = [
            item
            for item in selected
            if "/user_skills/candidate/" in f"/{item.relative_path}"
        ]
        if len(candidate_selected) == len(descriptors):
            report.passed(
                "skills.prompt_injection",
                "真实 Prompt 来源注册器支持嵌套发现全部技能",
                count=len(candidate_selected),
            )
        else:
            report.failed(
                "skills.prompt_injection",
                f"Prompt 注册器只发现 {len(candidate_selected)}/{len(descriptors)} 个候选技能",
            )

