---
type: component
project: kemo-agent
domain: run
module: run-context
layer: L3
updated: 2026-07-21
status: active
summary: run/context.py — 上下文预算与整轮选取
source: "run/run-context.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, run, 上下文, 预算, 整轮选取]
---
# run/context.py — 上下文预算与整轮选取

`E:\code\kemo-agent\run\context.py`

## 概览

只构建 Provider 视角的消息列表，**绝不修改源历史窗口**。提供轮数和 Token 双预算整轮选取。

v2 新增：`build_round_groups` 优先从 **Items v2 结构**（`window.items`）构建轮次消息组。Items 中的 message/reasoning/tool_call/tool_result 条目被合并为 Provider 格式消息，`_kemo_message` / `_kemo_reasoning` 元数据透传至上层。无 Items 的旧版窗口走 text/think/tool 遗留路径。

## 类

### ContextPolicy (frozen dataclass)

```python
@dataclass(frozen=True)
class ContextPolicy:
    recent_tool_rounds: int = 3           # n1
    max_rounds: int = 30                   # n2
    rounds_after_compression: int = 10     # n3
    token_limit: int = 120000              # n4
    compression_ratio: float = 0.6         # n5
    older_tool_log_max_chars: int = 200
```

**计算属性**：
- `input_budget = token_limit × compression_ratio`
- `output_reserve = token_limit - input_budget`

**from_config(config)**：从 `global_config.json` 读取 n1-n5 参数。

### RoundGroup

```python
@dataclass
class RoundGroup:
    number: int                          # 轮号
    messages: list[dict]                 # Provider 格式
    raw_text_messages: list[dict]        # 原始文本
    think: dict | None
    tool: dict | None
```

**核心原则**：整轮不可拆分。text 文件中的 user→assistant 是一轮。

### ContextSelection

```python
@dataclass
class ContextSelection:
    messages: list[dict]          # 最终发给 Provider 的消息
    kept_rounds: list[RoundGroup]
    removed_rounds: list[RoundGroup]
    estimated_tokens_before: int
    estimated_tokens_after: int
    round_limit_triggered: bool
    token_limit_triggered: bool
    fixed_content_over_budget: bool
```

## 函数

### estimate_text_tokens

```python
def estimate_text_tokens(text: str) -> int
```

保守估算（无外部依赖）：中文 1 字 = 1 token，英文 4 字 = 1 token。

### estimate_messages_tokens

```python
def estimate_messages_tokens(messages) -> int
```

遍历消息 JSON 序列化后估算。

### estimate_tools_tokens

```python
def estimate_tools_tokens(tools) -> int
```

工具 schema JSON 序列化后估算。

### build_round_groups

```python
def build_round_groups(window, policy) -> list[RoundGroup]
```

将四文件历史窗口拆分为 RoundGroup 列表。旧轮工具结果自动压缩（compact）。

### select_context

```python
def select_context(
    *,
    window, policy, system_message, current_user_message,
    tools=None, summary_message=None, force_compress=False,
) -> ContextSelection
```

**核心算法**：
1. **轮数超限**（≥ max_rounds）→ 保留最后 rounds_after_compression 轮
2. **Token 超限**（> token_limit）→ 从旧到新移除整轮直到低于 input_budget
3. 固定内容（system + current + tools）单独核算
4. 不修改源 window

### _compact_result

```python
def _compact_result(value, limit) -> str
```

旧轮工具结果超过限制时压缩为 `{"compressed": true, "preview": "..."}`。
