---
type: component
project: kemo-agent
domain: archive
module: message-外部消息路由
layer: L2
scope: project
status: archived
summary: message — 外部消息路由
source: "archive/message-外部消息路由.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, message, runtime-host, 消息路由, MockTransport]
created: 2026-07-15
---
# message — 外部消息路由

**状态**：✅ 平台无关核心已实现；具体 OneBot / Telegram 适配尚未接入。

## 文件结构

```text
message/
├── __init__.py       公共接口
├── schema.py         MessageEnvelope / OutboundMessage
├── transport.py      Transport 协议、注册表、权限、MockTransport
├── identity.py       外部身份绑定与工具权限交集
├── state.py          processed.json 消息幂等
├── router.py         MessageRouter → RunEvent → OutboundMessage
└── 外部消息路由.txt   源码内简要说明

run/runtime_host.py   Cron + Router + Transport 生命周期宿主
start_host.py         独立后台启动入口
message_lifecycle.md  完整生命周期契约
tests/test_message_runtime.py  21 项测试
```

## 核心链路

```text
Transport 收到外部消息
→ MessageEnvelope
→ IdentityResolver 映射内部用户
→ MessageRouter
→ run.engine.iter_request_events
→ 聚合 RunEvent
→ OutboundMessage
→ Transport.send
```

不调用 `cli.py`。CLI、Web、Cron 和外部消息都是 Run 核心的独立入口。

## 会话隔离

```text
source     = message:<platform>
session_id = <chat_type>:<external_chat_id>
```

- 同一内部用户跨入口共享记忆；
- 不同平台、聊天类型、聊天 ID 隔离上下文；
- 同会话串行（MessageRouter + Run 双层会话锁）；
- 跨会话通过线程池并行。

## 身份绑定

全局配置：

```json
{
  "message": {
    "bindings": [
      {
        "platform": "mock",
        "external_user_id": "external-1",
        "internal_user": "kesepain"
      }
    ]
  }
}
```

可选 `chat_type` / `external_chat_id` 精确绑定，匹配字段更多者优先。未绑定身份拒绝，不猜内部用户。

## 工具权限

最终工具为：

```text
用户已启用工具 ∩ TransportPolicy.allowed_tools
```

`null` 表示不额外限制；空数组表示该入口不允许工具。

## 消息幂等

每个用户：

```text
users/<user>/message_state/processed.json
```

在进入 Run 前按 `<platform>:<message_id>` 原子 claim。重复投递不再执行。重启时 processing 记录转 failed，不自动重放潜在副作用。

## RuntimeHost

启动：

```powershell
Set-Location 'E:\code\kemo-agent'
python .\start_host.py
```

Mock 诊断：

```powershell
python .\start_host.py --mock --status-json
```

启动顺序：Cron 恢复 → 消息状态恢复 → Router → Transports → Cron。
停止顺序相反，响应 Ctrl+C / SIGTERM。

## 测试

`tests/test_message_runtime.py` 共 21 项，覆盖合同、注册、权限、绑定、幂等、会话串并行、Run 成败、MockTransport 闭环、Cron 托管、故障隔离和优雅停机。

## 下一阶段边界

真实 Transport 尚未实现：

- OneBot / NapCat
- Telegram Bot API

接入顺序由用户决定。Web 仍未修改。
