---
type: component
project: kemo-agent
domain: archive
module: provider-外部API适配
layer: L2
scope: project
status: archived
summary: provider — 外部 API 适配
source: "archive/provider-外部API适配.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, provider, API, events, RunEvent, 流式]
created: 2026-07-15
---
# provider — 外部 API 适配

**状态**：✅ 第三轮：统一 RunEvent + 流式工具调用

## 第三轮变更

### schema.py

- `ChatProvider.chat_stream()` 返回类型从 `Iterable[StreamEvent]` 改为 `Iterable[RunEvent]`
- 删除了旧 `StreamEvent` dataclass（被 `events.RunEvent` 替代）
- 引入 `from events import RunEvent`

### openai_chat.py — chat_stream 重构

**之前**：yield `StreamEvent(type="text"/"reasoning"/"tool_call"/"usage"/"done")`

**现在**：yield `RunEvent(type="text_delta"/"reasoning_delta"/"tool_call_start"/"usage"/"done")`

关键改进：

#### 工具调用跨 chunk 拼接

```python
tool_parts: dict[int, dict[str, str]] = {}
# 逐 chunk 累积 id / name / arguments
for position, raw_call in enumerate(delta.get("tool_calls") or []):
    index = int(raw_call.get("index", position))
    part = tool_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
    part["id"] += str(raw_call.get("id", ""))
    part["name"] += str(function.get("name", ""))
    part["arguments"] += str(function.get("arguments", ""))
```

流结束后按 index 排序输出完整的 `tool_call_start` 事件。

#### 事件顺序

```
text_delta / reasoning_delta (逐 chunk)
→ tool_call_start (每个工具，完整参数)
→ usage
→ done (含 finish_reason / model / response_id)
```

#### StreamEvent 已废弃

旧 `StreamEvent` 在 `schema.py` 中已移除。整个项目统一使用 `events.RunEvent`。

### ChatProvider 协议更新

```python
class ChatProvider(Protocol):
    mode: ProviderMode
    def chat(self, request: ChatRequest) -> ChatResponse: ...
    def chat_stream(self, request: ChatRequest) -> Iterable[RunEvent]: ...
```

### kemo_gateway.py

无变更，继承自 `openai_chat.py`，自动获得 RunEvent 流式输出。

---

## 当前完整文件结构

```
provider/
├── __init__.py        # 公开 API
├── schema.py          # ChatRequest/ChatResponse/Usage/ToolCall/ChatProvider
├── factory.py         # create_provider()
├── openai_chat.py     # HTTP 传输 + SSE→RunEvent + 工具参数拼接
├── kemo_gateway.py    # Kemo 网关（继承 openai_chat）
└── 外部提供商api.txt
```
