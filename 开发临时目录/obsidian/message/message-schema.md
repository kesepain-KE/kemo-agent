---
type: component
project: kemo-agent
domain: message
module: message-schema
layer: L3
scope: project
status: active
summary: message/schema.py — 消息契约（text/附件互验 + file_path 出站）
source: "message/message-schema.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, message, 契约, MessageEnvelope, OutboundMessage, file_path]
---
# message/schema.py — 消息契约

`E:\code\kemo-agent\message\schema.py`

## 类

### MessageEnvelope (frozen)

```python
@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    message_id: str
    platform: str
    chat_type: ChatType           # "private" | "group" | "channel"
    external_user_id: str
    external_chat_id: str
    text: str                     # 可为空，但必须有附件
    timestamp: str
    attachments: tuple[dict] = ()
    metadata: dict = {}
```

**属性**：
- `dedupe_key` = `f"{platform}:{message_id}"`

**方法**：`from_dict()` / `to_dict()`

**校验变更**：
- `text` 可为空字符串，但 `text` 和 `attachments` **不能同时为空**
- `text` 会做 `.strip()` 规范化

### OutboundMessage (frozen)

```python
@dataclass(frozen=True, slots=True)
class OutboundMessage:
    message_id: str
    platform: str
    chat_type: ChatType
    external_chat_id: str
    text: str
    file_path: str = ""           # 新增：文件发送路径
    reply_to: str = ""
    metadata: dict = field(default_factory=dict)
```

**类方法**：`reply(envelope, text, *, file_path="", metadata=None)` — 从入站消息构造回复。

**校验**：
- `text` 和 `file_path` 都必须是字符串
- `text` 和 `file_path` **至少一个非空**（strip 后）

**`to_dict()`** 包含 `file_path` 字段。

## 常量

```python
ChatType = Literal["private", "group", "channel"]
```

## 函数

### MessageContractError

`MessageContractError(ValueError)` — 入站/出站消息不符合合约。

### _required_text / _iso_timestamp

内部校验函数。

## 变更记录

| 旧版 | 新版 |
|------|------|
| `text` 必须非空 | `text` 可为空，但 `text` 和 `attachments` 不能同时为空 |
| OutboundMessage 无 `file_path` | 新增 `file_path` 字段，`text` 和 `file_path` 至少一个非空 |
| `reply(envelope, text)` | `reply(envelope, text, *, file_path="", metadata=None)` |
| `to_dict()` 不含 file_path | `to_dict()` 包含 `file_path` |