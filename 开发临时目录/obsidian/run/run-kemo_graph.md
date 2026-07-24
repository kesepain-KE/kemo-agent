---
type: component
project: kemo-agent
domain: run
module: run-kemo_graph
layer: L2
scope: project
status: active
summary: run/kemo_graph.py — 六子层 kemo-graph 提示词替换边界
source: run/kemo_graph.py
updated: 2026-07-22
verified: true
tags: [kemo-agent, run, kemo_graph, 知识图谱, 替换, 六子层, 配置驱动]
---
# run/kemo_graph.py — 六子层 kemo-graph 提示词替换边界

`E:\code\kemo-agent\run\kemo_graph.py`

## 概览

声明外部 kemo-graph 项目作为知识库/记忆的替换器的接口边界。当前版本不实际连接外部项目（connected 恒为 False），只报告各子层的「启用但未连接」状态。

架构已从单一开关升级为**六个独立图谱子层**，每个子层由用户配置中的独立 bool 开关控制。

## 关键结构

### KemoGraphLayerStatus (frozen)

```python
@dataclass(frozen=True, slots=True)
class KemoGraphLayerStatus:
    name: str          # 子层显示名称
    switch: str        # 配置键名
    enabled: bool      # 是否在配置中启用
    connected: bool    # 是否成功连接（当前恒 False）
    status: str        # disabled / not_connected / connected
    text: str          # 注入文本
```

### KemoGraphPromptContext (frozen)

```python
@dataclass(frozen=True, slots=True)
class KemoGraphPromptContext:
    layers: tuple[KemoGraphLayerStatus, ...] = ()
    source_files: tuple[Path, ...] = ()

    @property
    def any_enabled(self) -> bool
    @property
    def requested(self) -> bool       # = any_enabled
    @property
    def connected(self) -> bool       # 所有启用层都已连接
    @property
    def status(self) -> str           # disabled / connected / partial / not_connected
    @property
    def text(self) -> str             # 拼接所有启用层的文本
```

### diagnostics 方法

返回诊断信息，新增 `layers` 数组，每层含 name/switch/enabled/connected/status/injected_chars。

## 六个子层

### _LAYER_SPECS

| 子层名称 | 配置键 |
|---------|--------|
| 外部知识图谱向量化检索，用户知识库层 | `kemo_graph_user_knowledge` |
| 外部知识图谱向量化检索，共享知识库层 | `kemo_graph_shared_knowledge` |
| 外部知识图谱向量化检索，全局知识库层级 | `kemo_graph_global_knowledge` |
| 用户的临时重要记忆，遗忘周期6个月 | `kemo_graph_temporary_memory` |
| 用户的临时重要记忆，遗忘周期一个月 | `kemo_graph_temporary_memory` |
| 用户的临时重要记忆，遗忘周期七天 | `kemo_graph_temporary_memory` |

注：记忆三个档共用同一个配置键 `kemo_graph_temporary_memory`，但生成三个独立的 LayerStatus（其 enabled 状态相同）。

## 加载逻辑

### load_kemo_graph_prompt_context

```python
def load_kemo_graph_prompt_context(
    root, user, config,
    *, replaces_knowledge, replaces_memory  # 已弃用，内部 del
) -> KemoGraphPromptContext
```

- 不再读 `config["kemo_graph"]["enabled"]`
- 不再读 `KEMO_GRAPH_ROOT` 环境变量
- 从 `config.get("kemo_graph")` 读取各子层开关
- 为每个 `_LAYER_SPECS` 构建 `KemoGraphLayerStatus`
- 未连接时注入 `_NOT_CONNECTED_TEXT`

## 变更记录

| 旧版 | 新版 |
|------|------|
| 单一 `KemoGraphPromptContext(requested, connected, status, text)` | `layers: tuple[KemoGraphLayerStatus, ...]` + 计算属性 |
| 从 `KEMO_GRAPH_ROOT` 环境变量定位项目 | 完全从配置驱动，移除环境变量依赖 |
| `requested = replaces_knowledge or replaces_memory` | `requested = any(layer.enabled)` |
| `diagnostics` 无 layers 数组 | diagnostics 含每子层的独立状态 |
| 一个 not_connected 文本 | 六个独立子层各自的 disabled/not_connected 文本 |