"""Non-starting kemo-graph prompt replacement boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class KemoGraphPromptContext:
    requested: bool
    connected: bool
    status: str
    text: str = ""
    source_files: tuple[Path, ...] = ()

    def diagnostics(self, *, replaces_knowledge: bool, replaces_memory: bool) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "connected": self.connected,
            "effective": self.connected and bool(self.text.strip()),
            "status": self.status,
            "replacement_active": replaces_knowledge or replaces_memory,
            "replaces_knowledge": replaces_knowledge,
            "replaces_memory": replaces_memory,
            "injected_chars": len(self.text),
            "source_files": [str(path) for path in self.source_files],
        }


def load_kemo_graph_prompt_context(
    root: Path,
    user: str,
    config: dict[str, Any],
    *,
    replaces_knowledge: bool,
    replaces_memory: bool,
) -> KemoGraphPromptContext:
    """Return graph context without launching or invoking the external project.

    The current kemo-graph checkout exposes no callable API/CLI contract.  This
    boundary deliberately reports that state instead of fabricating retrieval
    results.  A future connector can replace this loader while preserving the
    prompt pipeline contract and tests.
    """

    del user
    graph = config.get("kemo_graph") or {}
    requested = bool(graph.get("enabled", False)) if isinstance(graph, dict) else False
    if not requested:
        return KemoGraphPromptContext(False, False, "disabled")
    configured_root = os.getenv("KEMO_GRAPH_ROOT", "").strip()
    graph_root = (
        Path(configured_root).expanduser()
        if configured_root
        else root.resolve().parent / "kemo-graph"
    )
    exists = graph_root.is_dir()
    replaced = []
    if replaces_knowledge:
        replaced.append("知识库索引")
    if replaces_memory:
        replaced.append("记忆碎片")
    target = "、".join(replaced) or "上下文来源"
    detail = "项目目录存在但尚无连接接口" if exists else "项目目录不存在"
    return KemoGraphPromptContext(
        True,
        False,
        "not_connected",
        f"kemo-graph 已启用为{target}替换器，但{detail}；本轮未注入被替换的原始内容。",
    )
