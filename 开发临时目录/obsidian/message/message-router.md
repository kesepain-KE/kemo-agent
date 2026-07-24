---
type: component
project: kemo-agent
domain: message
module: message-router
layer: L2
scope: project
status: active
summary: message/router.py — 消息路由（bound_user 直绑 / 多键幂等 / request_payload 钩子）
source: "message/message-router.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, message, 路由, Router, RunEvent, bound_user, claim_many, slash_commands, session_close]
---
# message/router.py — 消息路由

## 模块定位

把外部消息统一路由到 run 核心执行。

## 所属领域

message

## 职责

- 处理消息幂等（单键 / 多键 claim_many）
- 处理会话隔离
- 调用运行事件引擎
- 组装路由结果
- 支持 Transport.request_payload() 钩子构建 Engine 入参
- 支持 message_queue_token 回传

## 非职责

- 不负责消息传输底层
- 不负责 run 内部状态机实现
- 不负责 provider 协议定义

## 输入

- MessageEnvelope
- resolver
- transport registry

## 输出

- RouteResult
- RunEvent 流

## 主要入口

- MessageRouter.route()
- _session_lock

## 调用者

- RuntimeHost
- 外部 transport 层

## 被调用对象

- run.engine.iter_request_events
- provider.factory.create_provider
- run.tools.discover_tools
- run.users.user_dir

## 依赖

- message.identity
- message.schema
- message.state
- message.transport

## 关键变更（2026-07-22）

### 1. bound_user 直绑

```python
registered = self.transports.get(envelope.platform)
if registered.policy.bound_user:
    user = registered.policy.bound_user
    user_dir(user, self.root)
else:
    user = self.resolver.resolve(envelope)
```

文件夹插件通过 `TransportPolicy.bound_user` 直接绑定内部用户，跳过 IdentityResolver。

### 2. 多键幂等 (claim_many / complete_many)

```python
raw_dedupe_keys = envelope.metadata.get("dedupe_keys")
if isinstance(raw_dedupe_keys, list) and raw_dedupe_keys:
    dedupe_keys = tuple(dict.fromkeys(item.strip() for item in raw_dedupe_keys))
else:
    dedupe_keys = (envelope.dedupe_key,)
if not store.claim_many(dedupe_keys):
    # 重复，跳过
```

群聊合批时，批次内每个原始消息 ID 独立幂等。已存在的单条消息不会随新消息再次进入推理。

### 3. request_payload 钩子

```python
request_payload = getattr(registered.transport, "request_payload", None)
if callable(request_payload):
    prepared = request_payload(envelope)
    request["prompt"] = str(prepared.get("prompt") or "")
    request["content"] = prepared.get("content") or []
```

FileMessageTransport 实现此钩子，将附件转为 Kemo Content Blocks。

### 4. message_queue_token 回传

出站消息的 metadata 中回传 `message_queue_token`，供 FileMessageTransport.finalize() 追踪领取状态。

### 5. 移除 tool_pause 暂停处理（2026-07-22）

旧版：done 事件包含 `awaiting_tool_confirmation` 时投入暂停提示文本，并将 RouteResult.status 设为 `waiting_confirmation`。
新版：`max_per_round` 已移除，done 事件不再包含暂停信号。RouteResult.status 统一为 `completed`。

### 9. 持久化会话绑定（2026-07-22 第二批追加）

路由前通过 `get_or_reserve_active` 获取或预约持久会话，旧 `chat_type:external_chat_id` 格式作为 `preferred_session_id` 传入。request 中注入 `_history_active_key`。

### 10. Slash 指令支持（2026-07-23 新增）

消息路由层处理平台无关的 slash 指令：

- `/new [标题]`：关闭当前会话 → `queue_memory_extraction` 排队记忆提取 → `close_session` → 创建并切换到新会话。回复确认消息并告知旧对话记忆将在后台继续提取。
- `/clear`：清空当前会话历史（`clear_session`）。
- 未知指令：回复支持列表提示。

Telegram input 模块新增 `filters.COMMAND` handler，确保 slash 指令消息能到达路由层而非被过滤。

指令在路由 `_session_lock` 内、事件引擎调用前处理，直接发送出站消息并返回 `RouteResult`，不进入 LLM 推理。

## 关键变更（2026-07-22 追加）

### 6. 有界等待队列与容量控制

新增 `max_queued_messages` 参数（默认 20），总容量为 `max_workers + max_queued_messages`：

- `_capacity` 信号量控制总容量
- 队列满时抛 `MessageQueueFullError`（继承自 `MessageRouteError`）
- `max_queued_messages=0` 时保持无界兼容模式
- 新增 `_pending_lock`、`_outstanding_count`、`_active_count` 跟踪

### 7. queue_status 方法

```python
def queue_status(self) -> dict[str, int]:
    # 返回 active_workers, max_workers, queued_messages, max_queued
```

供运行时状态 API 的 `congestion.message_router` 使用。

### 8. _transport_registry 透传

route 时将 `self.transports` 作为 `_transport_registry` 注入 request payload，使引擎内的工具能访问 Transport 注册表。

## 配置

- `message.max_workers`（默认 8）
- `message.max_queued_messages`（默认 20，0 表示无界）
- `message.dedupe_max_entries`

## 使用前提

- 外部消息已解析为 envelope
- resolver 能定位用户（传统 Transport）或 bound_user 已配置（文件夹插件）

## 代码证据

| 关系 | 目标 | 源码路径 | 条件 | 置信度 |
|---|---|---|---|---|
| calls | [[run-engine]] | MessageRouter.route() | 有效消息进入路由 | high |
| calls | [[provider-factory]] | MessageRouter.__init__ | 构造路由器时 | high |
| calls | [[run-tools]] | MessageRouter.__init__ | 构造工具注册表 | high |
| calls | [[message-state]] | claim_many / complete_many | 幂等处理 | high |
| calls | [[run-users]] | user_dir(user, self.root) | bound_user 路径 | high |

## 相关测试

- 路由幂等测试（含多键 claim_many）
- session 隔离测试
- 重复消息测试
- 文件夹插件路由测试

## 相关笔记

- [[message-总览]]
- [[message-plugin]]
- [[message-state]]
- [[provider-factory]]
- [[run-engine]]
- [[原理-消息路由]]