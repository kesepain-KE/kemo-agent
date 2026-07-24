---
type: component
project: kemo-agent
domain: run
module: run-prompt
layer: L2
scope: project
status: active
summary: run/prompt.py — PromptBundle 编排（17 段 + 分拆子代理注册 + 六层图谱替换标记）
source: "run/run-prompt.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, run, System Prompt, PromptBundle, 编排, 子代理拆分, 图谱替换标记, 完整性警告]
---
# run/prompt.py — PromptBundle 编排

`E:\code\kemo-agent\run\prompt.py`

## 模块定位

围绕系统提示词拼接的专题模块。

## 职责

- 组装 PromptBundle
- 固定顺序注入多个来源（含空段填充）
- 产出诊断信息
- 保持顺序契约稳定

## 关键变更（2026-07-22）

### 1. 子代理注册分拆为全局 + 用户两段

旧版：单一 `subagent_registry` 段。
新版：拆分为 `global_subagent_registry`（内置子代理）+ `user_subagent_registry`（用户自建子代理）。

```python
PROMPT_SECTION_ORDER = (
    "user_soul", "global_soul", "agents_manual",
    "global_subagent_registry",    # 新增
    "user_subagent_registry",      # 新增
    "plugins", "skills", "knowledge_index", "kemo_graph",
    "permanent_memory", "important_memory",
    "temporary_memory:half_year", "temporary_memory:one_month", "temporary_memory:seven_days",
    "task_plan", "expand_data", "perception",
)
```

固定顺序从 14 段变为 **17 段**。

### 2. 知识索引替换标记

旧版：被 kemo-graph 替换的 scope 不出现在 knowledge_index 段。
新版：被替换的 scope 仍保留独立 PromptSection，但正文改为固定标记：

```python
pieces.append(f"# {base} 目录结构，已被知识图谱替代")
```

`_knowledge_index_prompt()` 函数统一处理：遍历所有 scopes，被替换的注入标记文本，未替换的注入完整索引。

### 3. 临时记忆替换标记

旧版：`kemo_graph_replaces_temporary_memory=true` 时排除三层临时记忆段。
新版：三层临时记忆仍保留独立 PromptSection，但正文改为固定标记：

```python
memory_replacement_labels = {
    "half_year": "# 用户的临时重要记忆，遗忘周期6个月，已被知识图谱替代",
    "one_month": "# 用户的临时重要记忆，遗忘周期一个月，已被知识图谱替代",
    "seven_days": "# 用户的临时重要记忆，遗忘周期七天，已被知识图谱替代",
}
```

所有 tier_specs 都参与循环，被替换的注入标记文本，未替换的走正常记忆选择。

### 4. kemo_graph 诊断增强

```python
original_items=sum(layer.enabled for layer in graph_context.layers),
injected_items=sum(
    layer.enabled and bool(layer.text.strip())
    for layer in graph_context.layers
),
```

### 5. diagnostics 新增 knowledge_replaced_scopes

```python
"memory_integrity_warnings": list(dict.fromkeys(memory_integrity_warnings)),
```

**2026-07-22 新增**：收集所有临时层 `select_tier_for_prompt` 产出的 `integrity_warnings`，去重后写入 diagnostics。`build_prompt_bundle` 的调用者（如 `context_status`）可以通过此字段监控记忆索引指向缺失正文文件的情况。

## 配置

- `kemo_graph.kemo_graph_user_knowledge` / `kemo_graph_shared_knowledge` / `kemo_graph_global_knowledge` — 知识替换开关
- `kemo_graph.kemo_graph_temporary_memory` — 临时记忆替换开关

## 代码证据

| 关系 | 目标 | 条件 |
|------|------|------|
| calls | [[run-prompt_sources]] | 读取提示词来源时 |
| calls | [[run-knowledge]] | 注入 knowledge_index 时 |
| calls | [[run-memory]] | 注入记忆分档时 |
| calls | [[run-task_plan_store]] | 注入任务计划时 |
| calls | [[run-kemo_graph]] | kemo_graph 替换时 |
| calls | [[agents-runtime]] | discover_agents (全局 + 用户) |

## 相关笔记

- [[原理-PromptBundle编排]]
- [[run-kemo_graph]]
- [[run-source_policy]]
- [[run-engine]]