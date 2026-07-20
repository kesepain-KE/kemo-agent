"""Non-starting, layer-granular kemo-graph prompt replacement boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class KemoGraphLayerStatus:
    """Prompt state for one independently configured graph-backed layer."""

    name: str
    switch: str
    enabled: bool
    connected: bool
    status: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class KemoGraphPromptContext:
    layers: tuple[KemoGraphLayerStatus, ...] = ()
    source_files: tuple[Path, ...] = ()

    @property
    def any_enabled(self) -> bool:
        return any(layer.enabled for layer in self.layers)

    @property
    def requested(self) -> bool:
        return self.any_enabled

    @property
    def connected(self) -> bool:
        enabled = [layer for layer in self.layers if layer.enabled]
        return bool(enabled) and all(layer.connected for layer in enabled)

    @property
    def status(self) -> str:
        enabled = [layer for layer in self.layers if layer.enabled]
        if not enabled:
            return "disabled"
        connected = sum(layer.connected for layer in enabled)
        if connected == len(enabled):
            return "connected"
        if connected:
            return "partial"
        return "not_connected"

    @property
    def text(self) -> str:
        parts = [
            f"# {layer.name}\n{layer.text}"
            for layer in self.layers
            if layer.enabled and layer.text.strip()
        ]
        return "\n\n".join(parts)

    def diagnostics(self, *, replaces_knowledge: bool, replaces_memory: bool) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "connected": self.connected,
            "effective": any(
                layer.enabled and layer.connected and bool(layer.text.strip())
                for layer in self.layers
            ),
            "status": self.status,
            "replacement_active": replaces_knowledge or replaces_memory,
            "replaces_knowledge": replaces_knowledge,
            "replaces_memory": replaces_memory,
            "injected_chars": len(self.text),
            "source_files": [str(path) for path in self.source_files],
            "layers": [
                {
                    "name": layer.name,
                    "switch": layer.switch,
                    "enabled": layer.enabled,
                    "connected": layer.connected,
                    "status": layer.status,
                    "injected_chars": len(layer.text),
                }
                for layer in self.layers
            ],
        }


_LAYER_SPECS = (
    (
        "外部知识图谱向量化检索，用户知识库层",
        "kemo_graph_user_knowledge",
    ),
    (
        "外部知识图谱向量化检索，共享知识库层",
        "kemo_graph_shared_knowledge",
    ),
    (
        "外部知识图谱向量化检索，全局知识库层级",
        "kemo_graph_global_knowledge",
    ),
    (
        "用户的临时重要记忆，遗忘周期6个月",
        "kemo_graph_temporary_memory",
    ),
    (
        "用户的临时重要记忆，遗忘周期一个月",
        "kemo_graph_temporary_memory",
    ),
    (
        "用户的临时重要记忆，遗忘周期七天",
        "kemo_graph_temporary_memory",
    ),
)
_NOT_CONNECTED_TEXT = "kemo-graph 已启用但尚未连接；该层检索结果暂不可用。"


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

    del root, user, replaces_knowledge, replaces_memory
    graph = config.get("kemo_graph")
    if not isinstance(graph, dict):
        graph = {}
    layers = tuple(
        KemoGraphLayerStatus(
            name=name,
            switch=switch,
            enabled=graph.get(switch) is True,
            connected=False,
            status=("not_connected" if graph.get(switch) is True else "disabled"),
            text=(_NOT_CONNECTED_TEXT if graph.get(switch) is True else ""),
        )
        for name, switch in _LAYER_SPECS
    )
    return KemoGraphPromptContext(layers=layers)
