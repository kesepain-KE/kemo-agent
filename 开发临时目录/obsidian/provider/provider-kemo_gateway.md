---
type: component
project: kemo-agent
domain: provider
module: provider-kemo_gateway
layer: L3
scope: project
status: active
summary: provider/kemo_gateway.py — Kemo 原生网关 Provider（独立类，不再继承 OpenAIChatProvider）
source: "provider/provider-kemo_gateway.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, provider, Kemo, 网关, 原生]
---
# provider/kemo_gateway.py — Kemo 原生网关 Provider

`E:\code\kemo-agent\provider\kemo_gateway.py`

## 类

### KemoGatewayProvider

```python
class KemoGatewayProvider:
    mode = "kemo"

    def __init__(self, config: dict[str, Any])
    def create(self, request: KemoRequest) -> KemoResponse
    def stream(self, request: KemoRequest) -> Iterator[ProviderStreamEvent]
```

- 独立类，不再继承 `OpenAIChatProvider`
- 内部使用 `KemoGatewayAdapter` 处理原生 Kemo 协议
- 支持 Asset、音视频、媒体输出、Provider State、查询取消、流恢复
- `mode = "kemo"` 标识原生网关模式

## 变更记录

| 版本 | 说明 |
|------|------|
| 旧版 | 继承 OpenAIChatProvider，使用 `/v1/chat/completions`，保留 `chat()` legacy 方法 |
| **新版** | 独立类，仅原生 Kemo 协议，无 Chat Completions fallback，不再暴露 `chat()` |
