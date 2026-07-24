---
type: domain_overview
project: kemo-agent
domain: events
module: events-总览
layer: L1
scope: project
status: active
summary: events.py — 统一事件协议
source: "events/events-总览.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, events, 事件, 流式, guidance_applied]
---
# events.py — 统一事件协议

> 注意：`events/` 不是独立的源代码目录。实际代码在项目根目录的单文件 `events.py` 中。

`E:\code\kemo-agent\events.py`

## 概览

定义引擎与传输层之间的事件契约。一种 `RunEvent` 抽象，覆盖 text/reasoning/tool_call/error/done 等场景。

## 类

### RunEvent (frozen dataclass)

```python
@dataclass(frozen=True)
class RunEvent:
    type: str           # text_delta | reasoning_delta | tool_call_start
                        # | tool_call_result | guidance_applied | usage
                        # | error | done
    content: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)
    result: Any = None
    usage: dict = field(default_factory=dict)
    error: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
```

**8 种事件**：

| type | 说明 |
|------|------|
| `text_delta` | 文本增量（流式/非流式） |
| `reasoning_delta` | 思考增量（可选，--show-reasoning） |
| `tool_call_start` | 工具调用开始 |
| `tool_call_result` | 工具调用结果 |
| `guidance_applied` | 引导消息已应用到 Provider 请求（2026-07-23 新增） |
| `usage` | Token 用量统计 |
| `error` | 错误事件 |
| `done` | 完成事件（含最终 metadata） |

## 函数

### error_event

```python
def error_event(exc: BaseException, *, phase: str = "run") -> RunEvent
```

从异常构造标准 error RunEvent，包含 exception_type 和 message。
