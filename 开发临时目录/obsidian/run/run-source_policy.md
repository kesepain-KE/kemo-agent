---
type: component
project: kemo-agent
domain: run
module: run-source_policy
layer: L2
scope: project
status: active
summary: run/source_policy.py — 主智能体资源准入策略（kemo_graph 粒度化 + 被替换 scope 标记）
source: run/source_policy.py
updated: 2026-07-22
verified: true
tags: [kemo-agent, run, source_policy, 策略, 白名单, kemo_graph, 替换标记]
---
# run/source_policy.py — 主智能体资源准入策略

`E:\code\kemo-agent\run\source_policy.py` → `MainAgentSourcePolicy`, `NameFilter`

## 概览

为主智能体提供配置驱动的资源准入控制。kemo_graph 替换控制已粒化为 4 个独立开关。

## 核心结构

### MainAgentSourcePolicy

```python
@dataclass(frozen=True, slots=True)
class MainAgentSourcePolicy:
    knowledge_scopes: tuple[str, ...]         # 所有已启用的 scope（含被替换的）
    plugins: NameFilter
    shared_skills: NameFilter
    user_skills: NameFilter
    global_expand: NameFilter
    shared_expand: NameFilter
    global_perception: NameFilter
    kemo_graph_requested: bool
    kemo_graph_global_knowledge: bool
    kemo_graph_shared_knowledge: bool
    kemo_graph_user_knowledge: bool
    kemo_graph_replaces_temporary_memory: bool
```

## 新增方法（2026-07-22）

### replaced_knowledge_scopes

```python
def replaced_knowledge_scopes(self) -> tuple[str, ...]:
    """返回被 kemo-graph 替换的知识 scope。"""
```

### direct_knowledge_scopes

```python
def direct_knowledge_scopes(self) -> tuple[str, ...]:
    """返回仍然允许暴露本地文件的 scope（未被替换的）。"""
```

## kemo_graph 粒度化规则

### scope 选择变更

旧版：被替换的 scope 从 `knowledge_scopes` 中移除。
新版：**所有 scope 始终包含在 `knowledge_scopes` 中**，不因 kemo-graph 启用而移除。

```python
# 旧版
if not graph_user: scopes.append("user")
if use_shared and not graph_shared: scopes.append("shared")

# 新版
scopes = ["user"]
if use_shared: scopes.append("shared")
if use_global: scopes.append("global")
```

区分由 `replaced_knowledge_scopes()` 和 `direct_knowledge_scopes()` 在使用方处理：
- `direct_knowledge_scopes()` — 传给工具和 Web 的有效 scope
- `replaced_knowledge_scopes()` — 知识索引段注入替换标记

### public_summary 变更

```python
"knowledge": {
    "enabled": True,
    "configured_scopes": list(self.knowledge_scopes),        # 新增
    "effective_scopes": list(self.direct_knowledge_scopes()), # 旧字段含义变更
    "graph_replaced_scopes": list(self.replaced_knowledge_scopes()),  # 新增
}
```

## 配置映射

| 配置路径 | 策略字段 |
|---------|---------|
| `knowledge.use_shared` | knowledge_scopes 含 shared |
| `knowledge.use_global` | knowledge_scopes 含 global |
| `kemo_graph.kemo_graph_global_knowledge` | 替换全局知识 |
| `kemo_graph.kemo_graph_shared_knowledge` | 替换共享知识 |
| `kemo_graph.kemo_graph_user_knowledge` | 替换用户知识 |
| `kemo_graph.kemo_graph_temporary_memory` | 替换三层临时记忆 |

## 变更记录

| 旧版 | 新版 |
|------|------|
| 被替换 scope 从 knowledge_scopes 移除 | 所有 scope 保留在 knowledge_scopes，通过 replaced/direct 区分 |
| 无 replaced_knowledge_scopes() | 新增方法返回被替换的 scope |
| 无 direct_knowledge_scopes() | 新增方法返回有效 scope |
| public_summary.effective_scopes = knowledge_scopes | effective_scopes = direct_knowledge_scopes(), 新增 configured_scopes 和 graph_replaced_scopes |