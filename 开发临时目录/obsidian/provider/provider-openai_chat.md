---
type: component
project: kemo-agent
domain: provider
module: provider-openai_chat
layer: L3
scope: project
status: active
summary: provider/adapters/chat_bridge.py — ChatBridgeProvider，已替代 openai_chat.py
source: "provider/provider-openai_chat.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, provider, chat, bridge, chat_completions]
---
# provider/adapters/chat_bridge.py — ChatBridgeProvider

`E:\code\kemo-agent\provider\adapters\chat_bridge.py`

## 定位

`ChatBridgeProvider` 将 Kemo 协议契约暴露为标准 `/v1/chat/completions` API。这是主动选择的传输模式，不是 Kemo 原生网关的 fallback。可移植基线：文本/图片输入、文本输出、流式、函数工具。

## 类

### ChatBridgeProvider

```python
class ChatBridgeProvider:
    mode = "chat"

    def __init__(self, config: dict[str, Any])
    def create(self, request: KemoRequest) -> KemoResponse
    def stream(self, request: KemoRequest) -> Iterator[ProviderStreamEvent]
```

- `create()`: KemoRequest → HTTP POST → KemoResponse
- `stream()`: KemoRequest → SSE 流 → ProviderStreamEvent

通过 `compat.py` 中的转换函数处理 Kemo ↔ Chat 格式映射。

## 废弃说明

`provider/openai_chat.py` 中的 `OpenAIChatProvider` 已被 `ChatBridgeProvider` 替代。旧类的 `chat()`/`chat_stream()` 方法不再使用。
