# kemo-graph 粒度化替换控制 — 编程规划

## 问题

当前 `kemo_graph.enabled` 一个 bool 控制全部：启用时一刀切替换所有知识库索引 + 全部记忆。实际需要的控制粒度是：**每个知识层级 + 三层临时记忆各自独立开关**。

## 方案

将 `kemo_graph.enabled` 拆分为 4 个独立 bool 字段，每个控制一个注入来源的替换：

| 新字段 | 默认值 | 替换目标 |
|--------|--------|----------|
| `kemo_graph_global_knowledge` | `false` | 全局知识库索引 |
| `kemo_graph_shared_knowledge` | `false` | 共享知识库索引 |
| `kemo_graph_user_knowledge` | `false` | 用户知识库索引 |
| `kemo_graph_temporary_memory` | `false` | 三层临时记忆（half_year/one_month/seven_days） |

永久记忆和临时重要记忆**始终不受影响**（上一轮改动已确保）。

## 详细规划

### 步骤 1：更新 `config/global_config.json`

将：
```json
"kemo_graph": {
  "enabled": false
}
```
替换为：
```json
"kemo_graph": {
  "kemo_graph_global_knowledge": false,
  "kemo_graph_shared_knowledge": false,
  "kemo_graph_user_knowledge": false,
  "kemo_graph_temporary_memory": false
}
```

**注意：不要修改 `users/kesepain/user_config.json`**，用户配置目前仍保留旧字段，后续由用户自己决定。

### 步骤 2：重构 `run/source_policy.py`

#### 2.1 更新 `_reject_unknown` 的允许字段
从 `{"enabled"}` → `{"kemo_graph_global_knowledge", "kemo_graph_shared_knowledge", "kemo_graph_user_knowledge", "kemo_graph_temporary_memory"}`

#### 2.2 替换 dataclass 字段
删除：
- `kemo_graph_replaces_knowledge: bool`

新增：
- `kemo_graph_global_knowledge: bool`
- `kemo_graph_shared_knowledge: bool`
- `kemo_graph_user_knowledge: bool`

保留不变：
- `kemo_graph_requested: bool`（改为 computed：四个 flag 任一为 true）
- `kemo_graph_replaces_temporary_memory: bool`

#### 2.3 更新 `from_config`
```python
graph_global = _boolean(graph, "kemo_graph_global_knowledge", ...)
graph_shared = _boolean(graph, "kemo_graph_shared_knowledge", ...)
graph_user = _boolean(graph, "kemo_graph_user_knowledge", ...)
graph_memory = _boolean(graph, "kemo_graph_temporary_memory", ...)

# knowledge_scopes 过滤
scopes = ["user"]
if use_shared and not graph_shared:
    scopes.append("shared")
if use_global and not graph_global:
    scopes.append("global")
# 注意：user scope 是否也受 graph_user 控制
# 当前逻辑 user scope 始终在 scopes 中，但如果 graph_user=true，也应排除
```

#### 2.4 更新 `public_summary`
反映新的字段结构。

### 步骤 3：更新 `run/prompt.py`

当前逻辑已经正确：
- `knowledge_scopes` 来自 `source_policy`，步骤 2 会自动过滤
- `kemo_graph_replaces_temporary_memory` 字段名不变

**需要改**：`graph_context_loader` 调用处，`replaces_knowledge` 参数改为传入具体哪些 scope 被替换（或传入是否 any knowledge replaced）。

具体改动：将 `replaces_knowledge` 参数从单个 bool 改为表示"是否有任何知识 scope 被替换"的 bool（即 `graph_global or graph_shared or graph_user`），让 kemo_graph 能生成准确的提示信息。

### 步骤 4：更新 `run/kemo_graph.py`

#### 4.1 `load_kemo_graph_prompt_context`
- 参数 `replaces_knowledge: bool` 保留（表示是否有任何知识 scope 被替换）
- 参数 `replaces_memory: bool` 保留
- 内部判断 `requested`：从读 `graph.get("enabled")` 改为 `requested = replaces_knowledge or replaces_memory`
- 文本消息细化：当 `replaces_knowledge` 时，文本中写"知识库索引"（不再区分哪一层，因为以后由 kemo-graph 连接器自动感知）

#### 4.2 `diagnostics` 方法
`replaces_knowledge` 为 bool（是否有知识 scope 被替换），方法签名不变。

## 应达到的效果

1. `global_config.json` 中 kemo_graph section 包含 4 个独立开关，默认全部 false
2. `source_policy.py` 的 `MainAgentSourcePolicy` 携带 4 个独立 flag
3. `knowledge_scopes` 根据各 flag 动态排除对应 scope：
   - `kemo_graph_user_knowledge=true` → scopes 不含 `"user"`
   - `kemo_graph_shared_knowledge=true` → scopes 不含 `"shared"`
   - `kemo_graph_global_knowledge=true` → scopes 不含 `"global"`
4. `prompt.py` 无需改动拼接逻辑，因为 `knowledge_scopes` 和 `tier_specs` 已在源头被 source_policy 过滤
5. `kemo_graph.py` 的 `load_kemo_graph_prompt_context` 不再读 `graph.get("enabled")`，而是根据 `replaces_knowledge` / `replaces_memory` 参数判断
6. 语法检查通过（py_compile），原有行为不受影响（4 个 flag 默认 false = 当前 enabled=false 的效果）
