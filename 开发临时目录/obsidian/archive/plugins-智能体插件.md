---
type: component
project: kemo-agent
domain: archive
module: plugins-智能体插件
layer: L2
scope: project
status: archived
summary: plugins — 智能体插件（工具系统）
source: "archive/plugins-智能体插件.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, plugins, 工具, tool, get_current_time, history_search]
created: 2026-07-15
---
# plugins — 智能体插件（工具系统）

**状态**：✅ 第三轮落地工具系统 + 首批两个工具

## 工具发现与加载

`run/tools.py` — 完整的工具生命周期管理。

### 四级覆盖

```
优先级从低到高：
1. plugins/              # 内置插件
2. shared_skills/        # 共享技能
3. user_skills/agent_create/   # 智能体自创
4. user_skills/user_create/    # 用户自创（最高优先级）
```

同名工具：高层覆盖低层，记录 `overrides` 链。

### 工具清单格式 (tool.json)

```json
{
  "name": "get_current_time",
  "description": "...",
  "input_schema": {
    "type": "object",
    "properties": {},
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```

`entrypoint` 格式：`文件名:函数名`。

### 核心类

| 类 | 说明 |
|----|------|
| `ToolDefinition` | 工具定义：name/description/input_schema/version/enabled/entrypoint/source/overrides |
| `ToolRegistry` | 工具注册表：`enabled_tools()` / `schemas()` / `get(name)` |

### 工具执行

```python
execute_tool(tool, arguments, context, timeout, cancel_event) → Any
```

- 参数校验（类型、必填、范围）
- 同步/异步工具统一调用
- `ThreadPoolExecutor` 隔离执行
- 超时抛出 `ToolTimeoutError`
- `cancel_event` 取消支持
- `context` 注入：`root` / `user` / `source` / `session_id` / `window`

---

## 首批内置工具

### get_current_time

**目录**：`plugins/get_current_time/`

获取 UTC + 本地时间，含时区偏移。

```python
def run() → dict:
    return {"utc": "ISO8601", "local": "ISO8601", "timezone": "CST", "utc_offset": "+0800"}
```

- 无参数
- 同步函数

### history_search

**目录**：`plugins/history_search/`

搜索当前用户已提交对话窗口中的用户与助手文本。不读取思考记录或工具日志。

```python
def run(query: str, limit: int = 10, *, context: dict) → dict:
    # 遍历 history/ 目录 → 匹配 query → 返回 matches 列表
```

- 支持 `context` 注入（root/user）
- 只读 `text.json`，跳过不完整窗口
- 参数：`query`（必填，string）、`limit`（可选，1-100，默认 10）

---

## 工具循环

引擎 (`engine.py`) 中实现的完整模型—工具闭环：

```
用户输入 → 模型回复（含 tool_calls）
  → 逐个执行工具
    → 结果注入 messages（role=tool）
      → 模型再次回复
        → 直到无 tool_calls 或达到 max_iterations
```

- 同一轮中相同 `(name, arguments)` 的工具调用**去重复用**，避免重复执行
- `max_iterations` 默认 8（可配置 `tools.max_iterations`）
- 工具超时默认 60s（可配置 `tools.timeout`）
