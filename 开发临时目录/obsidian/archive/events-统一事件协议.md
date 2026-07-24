---
type: component
project: kemo-agent
domain: archive
module: events-统一事件协议
layer: L2
scope: project
status: archived
summary: events — 统一运行时事件协议
source: "archive/events-统一事件协议.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, events, 事件协议, RunEvent, 流式]
created: 2026-07-17
---
# events — 统一运行时事件协议

**状态**：✅ 第三轮新增，Provider → Run → CLI 共享

## 文件

`events.py` — 顶层模块，被 `provider/`、`run/`、`cli.py` 共同引用。

## 事件类型

```python
EventType = Literal[
    "text_delta",         # 文本增量
    "reasoning_delta",    # 思考增量
    "tool_call_start",    # 工具调用开始（含 id/name/arguments）
    "tool_call_result",   # 工具调用结果（含 result/status）
    "usage",              # Token 用量统计
    "error",              # 运行错误（含 message/exception_type/phase）
    "done",               # 对话完成（含 metadata）
]
```

终止事件：`error` 或 `done`（`TERMINAL_EVENTS`）。

## RunEvent 数据结构

```python
@dataclass(slots=True)
class RunEvent:
    type: EventType
    content: str = ""                # text_delta / reasoning_delta
    tool_call_id: str = ""           # tool_call_start / tool_call_result
    tool_name: str = ""              # 工具名
    arguments: dict | None = None    # 工具参数
    result: Any = None               # 工具结果
    usage: dict | None = None        # Token 统计
    error: dict | None = None        # 错误信息
    metadata: dict = {}              # 附加元数据
```

支持 `to_dict()` / `from_dict()` 序列化。

## error_event 快捷构建

```python
def error_event(exc, *, phase="run") -> RunEvent:
    # → RunEvent(type="error", error={"message":..., "exception_type":..., "phase":...})
```

## 数据流

```
Provider (SSE chunk)
  → RunEvent (text_delta / reasoning_delta / tool_call_start)
  → engine (工具循环调度)
  → RunEvent (tool_call_result / usage / error / done)
  → CLI (emit_event_stream)
  → stdout / stderr
```
