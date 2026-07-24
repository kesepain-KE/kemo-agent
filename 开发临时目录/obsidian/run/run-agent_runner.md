---
type: component
project: kemo-agent
domain: run
module: run-agent_runner
layer: L2
scope: project
status: active
summary: run/agent_runner.py — 独立 AgentRunner（使用 KemoRequest/KemoResponse 协议）
source: "run/run-agent_runner.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, run, 子代理, AgentRunner, 执行, Kemo协议]
---
# run/agent_runner.py — 独立 AgentRunner

`E:\code\kemo-agent\run\agent_runner.py`

## 概览

子代理只接收**专用职责指令 + 调用方显式输入**，不继承主对话历史。支持模型档位、输入输出 JSON Schema 校验、超时/取消、事件通知。

## 类

### 错误体系

```python
AgentRunError
├── AgentInputError       # 输入校验失败
├── AgentOutputError      # 输出解析/校验失败
├── AgentTimeoutError     # 超时
└── AgentCancelledError   # 取消
```

### AgentRunResult

```python
@dataclass
class AgentRunResult:
    agent: str
    data: dict           # 解析后的 JSON 输出
    raw_text: str        # 原始响应
    usage: dict
    model: str
    response_ids: list[str]
    metadata: dict
```

### AgentRunner

```python
class AgentRunner:
    def __init__(self, root, user, *, config, registry, provider_factory)
    def run(name, input_data, *, cancel_event, timeout, model_override,
            event_callback, task_id, max_tokens) -> AgentRunResult
```

**run() 流程**：
1. 获取 AgentDefinition → 校验 input_schema
2. 构建 system prompt：instruction + 无上下文声明 + output_schema
3. resolve_agent_provider_config：合并模型档位
4. **使用 KemoRequest/KemoResponse 协议调用 provider.create()**（非 ChatRequest）
5. 迭代工具循环：MessageItem/ToolCallItem/ToolResultItem 协议类型
6. 通过 event_callback 发送 started/completed/failed/cancelled

## 执行变更（2026-07-21）

| 旧版 | 新版 |
|------|------|
| `provider.chat(ChatRequest)` → `ChatResponse` | `provider.create(KemoRequest)` → `KemoResponse` |
| 消息用 `dict` 列表拼接（role/content/tool_calls） | 消息用 `MessageItem`/`ToolCallItem`/`ToolResultItem` 协议类型 |
| `response.tool_calls` → 构建 assistant 字典消息 | `response.output` 中 `isinstance(item, ToolCallItem)` 判断 |
| `response.text` | `text_from_content(item.content)` 提取文本 |
| `response.usage.to_dict()` | `_usage_dict(response.usage)` 转换 |

## 函数

### resolve_agent_provider_config

```python
def resolve_agent_provider_config(config, definition, *, model_override) -> dict
```

从 agent_models 读取档位配置，合并到主 config provider。

### _tool_definitions (新增)

```python
@staticmethod
def _tool_definitions(schemas: list[dict[str, Any]]) -> list[ToolDefinition]
```

将旧版 tool schema 转换为 Kemo 协议 `ToolDefinition` 格式。

### _usage_dict (新增)

```python
@staticmethod
def _usage_dict(usage: Usage) -> dict[str, Any]
```

将 Kemo 协议 `Usage` 转换为诊断字典。

### validate_json_schema / _parse_json_object / _type_matches

与旧版相同，不做改动。

## 关键变更（2026-07-22 追加）

### Provider 并发槽位

`provider.create()` 调用被 `provider_request_slot(config, cancel_event=context.cancel_event)` 包裹：

- 获取进程级 Provider 并发槽位后才发送请求
- 超时抛 `ProviderCongestionError`
- 取消事件优先于信号量等待
- 如果 `cancel_event` 已设置，转为 `AgentCancelledError`
