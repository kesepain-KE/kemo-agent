# RuntimeHost 与外部消息路由生命周期契约

## 本轮边界

本轮实现平台无关的后台宿主、消息契约、身份绑定、Transport 注册、权限交集、路由和 MockTransport。

不接入 OneBot、Telegram 或 Web，不保存真实平台凭据，不通过 `cli.py` 执行消息。

## 组件职责

```text
RuntimeHost
├── CronScheduler：定时任务扫描与执行
├── MessageRouter：入站消息到 RunEvent，再聚合成出站消息
├── IdentityResolver：外部身份映射到内部用户
└── TransportRegistry
    └── Transport：平台收发适配器
```

正确链路：

```text
Transport 收到平台消息
→ MessageEnvelope
→ IdentityResolver
→ MessageRouter
→ run.engine.iter_request_events
→ RouteResult / OutboundMessage
→ Transport.send
```

`cli.py`、Web 和具体平台都只是独立入口，不是其他入口的依赖。

## 入站消息 MessageEnvelope

必需字段：

| 字段 | 说明 |
|---|---|
| `message_id` | 平台内稳定消息 ID；同平台内用于幂等去重 |
| `platform` | Transport 名称，如 mock / onebot / telegram |
| `chat_type` | private / group / channel |
| `external_user_id` | 外部发送者 ID |
| `external_chat_id` | 外部会话或群 ID |
| `text` | 用户文本；本轮至少一个非空文本 |
| `timestamp` | 带时区 ISO 时间 |
| `attachments` | 附件描述列表；本轮只透传元数据，不下载 |
| `metadata` | 平台扩展元数据 |

## 出站消息 OutboundMessage

包含：`message_id`、`platform`、`chat_type`、`external_chat_id`、`text`、`reply_to`、`metadata`。

平台适配器只负责把标准出站消息转换为平台 API 请求。

## 身份绑定

配置位于全局 `message.bindings`：

```json
{
  "platform": "mock",
  "external_user_id": "external-1",
  "internal_user": "kesepain"
}
```

可选 `chat_type` 和 `external_chat_id` 用于更精确绑定。匹配按“精确字段更多者优先”。未绑定身份直接拒绝，不猜内部用户。

内部用户必须存在于 `users/<name>/`。

## 会话隔离

映射到 Run 请求：

```text
user      = 绑定的内部用户
prompt    = envelope.text
source    = message:<platform>
session_id= <chat_type>:<external_chat_id>
```

效果：

- 同一内部用户跨入口共享记忆；
- 不同平台的历史窗口隔离；
- 同平台私聊、群聊隔离；
- 不同聊天 ID 隔离；
- 同一 `(user, source, session_id)` 由 Run 核心现有锁串行；
- 不同会话可由 MessageRouter 工作线程并发。

## 幂等去重

处理记录保存于：

```text
users/<user>/message_state/processed.json
```

键为 `<platform>:<message_id>`。消息在进入 Run 前原子 claim，完成后写 `completed` 或 `failed`。已存在键不重复执行，避免上游重投造成工具副作用。记录按配置上限保留最近 N 条。

## 工具权限交集

最终可用工具：

```text
用户发现到且已启用的工具 ∩ TransportPolicy.allowed_tools
```

- `allowed_tools = null`：Transport 不额外限制；
- `allowed_tools = []`：Transport 不允许任何工具；
- 具体列表：仅保留同名工具。

权限过滤通过传给 Run 核心的 `tool_registry_factory` 实现，不改动 Run 引擎。

## Transport 协议

Transport 必须提供：

- `name`
- `capabilities`
- `start(on_message, on_error)`
- `send(message)`
- `stop()`
- `running`

注册表拒绝空名称和同名注册。单个 Transport 启动、运行或发送失败只标记该组件错误，不终止其他组件。

## RuntimeHost 状态

宿主状态：`stopped → starting → running → stopping → stopped`，启动整体失败时为 `failed`。

组件状态：`registered / starting / running / failed / stopped`。

启动顺序：

1. 恢复 Cron 的中断任务；
2. 启动 MessageRouter 工作池；
3. 按注册顺序启动 Transport，单个失败隔离；
4. 根据配置启动 CronScheduler；
5. 宿主进入 running。

停止顺序：

1. 设置宿主停止信号，不再接收新消息；
2. 停止所有 Transport；
3. 等待或取消消息路由工作；
4. 停止 CronScheduler；
5. 宿主进入 stopped。

重复 start/stop 必须幂等。

## 错误处理

- 契约错误、未绑定身份、重复消息：不进入 Run；
- Run error 事件：聚合为失败 RouteResult，并生成简短出站错误文本；
- Transport send 失败：RouteResult 标记发送失败，但已完成的 Run 历史不回滚；
- Cron/Transport 单组件异常：记录在 RuntimeHost 状态中，不拖垮宿主；
- Ctrl+C / SIGTERM：触发 RuntimeHost.stop() 优雅关闭。

## 下一阶段

本轮通过 MockTransport 验证完整闭环。OneBot/NapCat 和 Telegram 的真实适配顺序由用户后续决定。
