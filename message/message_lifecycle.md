# RuntimeHost 与外部消息路由生命周期契约

## 当前边界

当前已实现平台无关后台宿主、消息契约、身份绑定、Transport 注册、权限交集、路由、MockTransport，以及 `message/out/<platform>/` 文件夹插件运行时。

核心不内置 OneBot、Telegram SDK，也不保存真实平台凭据；具体平台连接由各插件的 `input.py` / `output.py` 实现，不通过 `cli.py` 执行消息。

## 组件职责

```text
RuntimeHost
├── CronScheduler：定时任务扫描与执行
├── MessageRouter：入站消息到 RunEvent，再聚合成出站消息
├── IdentityResolver：外部身份映射到内部用户
└── TransportRegistry
    ├── Transport：传统平台收发适配器
    └── FileMessageTransport：文件夹插件与 message.md 队列
```

正确链路：

```text
input.py 收到平台消息并写入 message.md / files/
→ FileMessageTransport 领取并解析队列
→ MessageEnvelope
→ message.json.bound_user（传统 Transport 使用 IdentityResolver）
→ MessageRouter
→ run.engine.iter_request_events
→ RouteResult / OutboundMessage
→ output.py.send
→ 日志、附件与队列领取文件清理
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
| `text` | 用户文本；可为空，但必须至少有一个附件 |
| `timestamp` | 带时区 ISO 时间 |
| `attachments` | 已落地到插件 `files_dir` 的附件描述；路径必须保持在该目录内 |
| `metadata` | 平台扩展元数据 |

## 出站消息 OutboundMessage

包含：`message_id`、`platform`、`chat_type`、`external_chat_id`、`text`、`file_path`、`reply_to`、`metadata`。`text` 与 `file_path` 至少一个非空。

平台适配器只负责把标准出站消息转换为平台 API 请求。

## 身份绑定

传统 Transport 的绑定配置位于 `config/message_config.json` 的 `bindings` 数组：

```json
{
  "platform": "mock",
  "external_user_id": "external-1",
  "internal_user": "kesepain"
}
```

可选 `chat_type` 和 `external_chat_id` 用于更精确绑定。匹配按“精确字段更多者优先”。未绑定身份直接拒绝，不猜内部用户。

文件夹插件不逐个绑定外部账号，而是使用 `message.json.bound_user` 将该平台实例整体绑定到一个内部用户。

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

处理记录保存于每用户历史库：

```text
users/<user>/history/history.sqlite3
└── message_processed_messages
```

键为 `<platform>:<message_id>`。消息在进入 Run 前以 `BEGIN IMMEDIATE` 原子 claim，完成后写 `completed` 或 `failed`。群聊即使合批，也会原子领取批次内每个原始消息 ID；已存在的单条消息不会随新消息再次进入推理。记录按配置上限只淘汰最旧终态，绝不删除 `processing`。

## 工具权限交集

最终可用工具：

```text
用户发现到且已启用的工具 ∩ TransportPolicy.allowed_tools
```

- `allowed_tools = null`：Transport 不额外限制；
- `allowed_tools = []`：Transport 不允许任何工具；
- 具体列表：仅保留同名工具。

权限过滤通过传给 Run 核心的 `tool_registry_factory` 实现，不改动 Run 引擎。

## 文件夹插件协议

RuntimeHost 启动时扫描 `message/out/` 的直接子目录。每个有效插件必须包含：

- `message.json`：平台、机器 ID、绑定用户、能力、工具权限和模块路径；
- `message.md`：YAML front matter + Markdown 正文文件队列；
- `input.py`：阻塞式 `start()` 与幂等 `stop()`；
- `output.py`：返回 bool 的 `send(message)`；
- `detect.py`：返回完整状态对象的 `check(config, state)`；
- `files/`：附件落地目录。运行状态与收发日志统一位于 `runtime/logs.sqlite3`。

同一群聊在一次队列领取中的多条消息合并为一个 MessageEnvelope，私聊逐条提交。领取文件保留到全部 RouteResult 进入终态，宿主重启后仍可重新发现；用户级幂等状态避免重复副作用。

附件路由遵循当前 Provider 统一协议：图片、音频、PDF 以 `inline_base64` Kemo Content Blocks 进入 Engine，文本附件直接读取，视频和未知类型只注入说明。Chat 模式仍受其公开多模态能力边界限制。

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
- Run error 事件：聚合为失败 RouteResult，记录错误但不伪造成功回复；
- Transport send 失败：RouteResult 标记发送失败，但已完成的 Run 历史不回滚；
- Cron/Transport 单组件异常：记录在 RuntimeHost 状态中，不拖垮宿主；
- Ctrl+C / SIGTERM：触发 RuntimeHost.stop() 优雅关闭。

## 平台扩展

新增 OneBot/NapCat、Telegram 等平台时，复制 `message/out/example/` 并实现三个模块即可；核心路由、幂等、会话隔离、附件分流、日志和健康检测无需修改。
