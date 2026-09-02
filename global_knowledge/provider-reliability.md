# Provider 与传输可靠性功能说明

本文集中说明 Provider 工具调用完整性、网络传输恢复、SSE 续传、取消和安全边界。工具参数校验与网络请求恢复是两个独立阶段，分别按对应章节执行。

## Kemo 协议网络稳定性与恢复边界

本文说明 `provider.type=kemo` 时，kemo-agent 到 Kemo 网关之间的请求幂等、SSE 续传、有限重试、取消和安全边界。普通 `chat` 协议不使用本链路，也不会自动切换到 Kemo。

### 请求身份与幂等

- 每次真实 Provider 迭代生成独立 `request_id`，HTTP 同时发送 `Idempotency-Key` 和 `X-Request-ID`，两者均等于正文中的 `request_id`。
- 一次传输重试或 SSE 续传必须复用完全相同的请求正文和 `request_id`。不得为了“重试”重新生成 ID，否则网关会把它视为新的模型调用。
- 工具执行后的下一次 Provider 迭代是新的逻辑请求，会生成新 `request_id`，并通过 `parent_request_id` 连接上一迭代。这不属于网络重试。
- 网关若发现相同 ID 对应不同正文，应返回不可重试的幂等冲突；客户端不会把 HTTP 409 当成普通瞬时网络错误。

### 自动恢复范围

Kemo 原生客户端对 LLM、Embedding 和 Rerank 的以下传输故障执行有限恢复：

- 建连失败、DNS/连接异常和读取超时；
- 网关明确声明 `retryable=true` 的 HTTP 错误；错误没有显式声明时，HTTP 408、425、429、500、502、503、504 按瞬时故障处理；
- SSE 在统一终态前结束；
- 连接在一条 SSE JSON 中间断开。

一次逻辑请求最多进行 3 次网络尝试。重试使用指数退避和小幅随机抖动；网关提供 `Retry-After` 或 `retry_after_ms` 时优先采用，但单次等待最多 10 秒。等待过程可被当前 Run 的取消信号中断。

HTTP 错误中的显式 `retryable` 是最高优先级声明。即使状态码通常可重试，只要网关返回 `retryable=false`，客户端也必须立即停止；只有缺少该字段时才使用状态码默认值。Embedding 和 Rerank 重试同样复用首次序列化出的正文、`request_id` 和幂等键。

以下情况不会自动重试：

- 鉴权、权限、请求校验、能力不匹配和幂等冲突；
- 完整但不符合 Kemo Schema 的 JSON/SSE 事件；
- 模型已经通过统一终态返回的业务失败；
- 上下文超限。该情况仍由对话运行时原有的上下文压缩与受控重试处理。

业务失败不由传输层重新生成，避免重复计费、重复工具调用或在用户已经看到部分输出后启动另一份回答。特别是已经持久化的 LLM `response.failed` 终态，即使其中包含 `retryable=true`，使用同一 `request_id` 也只会重放该失败终态；若上层策略决定重新执行模型，必须创建新的逻辑请求和新 ID，并承担可能产生的新计费。

工具调用同样服从统一业务终态。只有完整 `ToolCallItem` 才能进入运行时；网关为诊断保留 `arguments_raw` 和 `parse_error` 时，`parse_error` 非空表示参数不可执行，Agent 会在工具循环前明确失败。该情况不是网络故障，不进行传输重试，也不会切换到 Chat 协议。

### SSE 断线续传

客户端只在成功校验一条完整事件后推进恢复游标。连接断开后：

1. 复用原请求正文与 `request_id`；
2. 发送最后一个完整事件的 `Last-Event-ID`；
3. 在本地保留最后接收的 `sequence`，使用同一个顺序守卫继续校验后续事件；
4. 丢弃事件 ID 完全相同的重放事件，不重复展示文本或工具调用。

Kemo 1.0 在线路上只定义 `Last-Event-ID` 作为服务端恢复游标。`sequence` 仅用于客户端本地连续性校验，不作为查询参数发送；内部调用若只提供 `resume_from_sequence` 而没有 `Last-Event-ID`，会在发起网络请求前被拒绝。

续传响应必须保持相同 `response_id`。如果网关重启或状态丢失后为同一请求创建了另一份 Response，客户端会拒绝拼接并明确失败；它不会把两份模型输出连接成一轮伪造回答。

SSE 注释心跳不会形成 Provider 事件，也不会进入对话历史；它只负责保持代理、CDN 和路由器上的空闲连接。心跳周期由网关控制。

### 取消

- 主运行时把 `cancel_event` 传入 Kemo 原生客户端。
- SSE、非流式 LLM、Embedding 和 Rerank 读取期间都有本地取消观察器；用户取消后会主动关闭阻塞连接，而不是固定等待 120 秒读取超时。
- 已知 `response_id` 时，客户端会以最多 2 秒的独立短请求尽力调用网关 `/cancel`。
- 远端取消失败不会把本地取消轮次改记为 Provider 失败；网关仍应依靠执行期限和租约清理孤立任务。

### 数据完整性

- SSE 对事件 JSON、`request_id`、`response_id`、`event_id`、`sequence`、唯一终态及终态后事件进行校验。
- Asset 上传发送大小和 SHA-256；下载先写随机 `.part`，校验大小和 SHA-256 后原子替换目标文件，失败时清理半成品。
- 这些校验用于发现截断、错序和内容损坏，不等同于链路加密。

### HTTPS 边界

Kemo 协议允许 `http://`，方便同机或可信内网部署，但 HTTP 不提供机密性和抗篡改能力。跨主机、非可信局域网或公网部署必须在网关前使用 HTTPS 反向代理，并保持系统 TLS 证书校验。不得通过关闭证书校验来处理证书配置错误。

### 验证

客户端故障测试位于 `tests/provider/test_provider_protocol.py` 和 `tests/core/test_runtime_features.py`，覆盖：

- 同一请求的 SSE 断线续传；
- 恢复游标与幂等键；
- SSE 半帧截断；
- 新 `response_id` 拼接拒绝；
- 非流式瞬时错误重试；
- Embedding/Rerank 瞬时错误重试时保持正文和幂等键不变；
- 显式 `retryable=false` 覆盖 HTTP 状态默认值；
- 完整协议损坏不重试；
- SSE 与非流式阻塞读取取消、远端取消传播；
- Kemo 1.0 `Last-Event-ID` 恢复边界。

这组单元测试验证客户端状态机，不替代真实 Nginx/CDN、丢包、网关进程崩溃和磁盘故障测试。发布前应同时运行 Agent 与网关两侧的传输稳定性测试。

---

## Provider 工具调用完整性与终态边界

本文说明 `provider.type=chat` 与 `provider.type=kemo` 的工具调用何时可执行，以及输出截断、参数损坏和任务计划暂停时应如何诊断。实现入口主要位于 `provider/openai_chat.py`、`provider/adapters/compat.py`、`run/conversation/` 和 `run/tasks/`。

### 共同原则

工具名称出现不等于工具调用已经完整。一次调用进入 `run/conversation/` 的执行循环前必须同时满足：

1. Provider 已给出允许继续工具循环的终态；
2. 参数已经完整解析为 JSON 对象；
3. 没有协议层或适配层记录的解析错误；
4. 当前 Run 没有进入取消、失败或受控限制终态。

任何一项不满足，都不得把残缺文本包装成普通参数后尝试执行。原始参数仅用于有界诊断，不能成为工具输入。

### Chat Completions 兼容链路

Chat Bridge 支持现代 `message.tool_calls[]` 和旧式单个 `message.function_call`，流式参数按调用索引拼接。参数规则如下：

- JSON 对象：可执行；
- 缺省或空参数：兼容为 `{}`；
- 无效 JSON：记录 `parse_error`，不可执行；
- JSON 数组、字符串、数字或布尔值：根节点不是对象，不可执行；
- 原始参数值不得进入错误、SSE、历史或日志；诊断只保留是否存在、长度和固定解析位置，不能以 `_raw` 字段伪装成合法参数。

`finish_reason` 必须参与终态判断：

| Chat 终态 | 统一结果 | 工具行为 |
|---|---|---|
| `tool_calls` / `function_call`，参数完整 | `requires_action` | 进入工具循环 |
| `stop` 或兼容空值，无工具调用 | `completed` | 正常结束 |
| `length` / `max_tokens` / `max_output_tokens` | `incomplete` | 不执行该批工具 |
| `content_filter` | `incomplete` | 不执行该批工具 |
| 工具终态但没有完整调用 | `incomplete` | 报告缺失调用 |
| 参数解析失败 | `incomplete` | 报告 `invalid_tool_arguments` |

普通兼容 Provider 仍可实时转发文本和完整工具事件。官方 Chat HTTP 传输会把已知 `finish_reason` 附在工具事件上，使截断调用在发布前即可被拦截；即使第三方内部适配器到最后才给出不完整终态，对话运行时也会在实际执行工具前收到终态错误并停止。

标准 SSE 以 `[DONE]` 收束。为兼容部分实现，已经出现明确 `finish_reason` 后的干净 EOF 也可以结束；没有两者的 EOF、半个 JSON 帧或连接异常仍是 `stream_interrupted`。

### Kemo 原生链路

Kemo 网关已经提供类型化 `ToolCallItem`、明确响应状态和有序 SSE。Agent 不重新定义线路协议，只增加执行前防御：

- `status=requires_action` 且工具项完整时才继续工具循环；
- `ToolCallItem.parse_error` 非空时转为 `ProviderToolArgumentsError`；
- `arguments_raw` 只用于诊断，不代替 `arguments`；
- `response.incomplete/failed/cancelled` 进入明确运行错误，不触发工具执行；
- 网络重试与业务终态分离，参数损坏不是可重试传输错误。

### 工具参数生成恢复

`tools.invalid_tool_arguments_retries` 默认是 2。Chat 或 Kemo 被统一映射为
`invalid_tool_arguments` 后，主运行时与所有 `AgentRunner` 子智能体都会建立新的逻辑 Provider
请求，临时在系统消息中加入“只重新生成完整工具调用”的纠错指令。该恢复不是传输重试，
不会复用失败请求的 `request_id`。

自动恢复遵守以下边界：

- 已流出的文本与思考作为本轮可见内容保留，纠错请求会被要求不要重复这些内容；
- 同一 Provider 响应中的所有工具调用先在内存中整批校验，校验完成前不发布工具卡片、不登记待
  执行状态，也不调用工具；
- 任一并行工具调用的参数损坏时，失败响应内的全部工具调用都被丢弃，不能先执行其中的有效调用；
- 已经发布媒体时不做自动恢复，因为媒体发布属于不可安全重放的外部可见副作用；
- 当前恢复次数尚未达到配置上限。

恢复成功后只发布并执行新响应中整批有效的调用；失败尝试的工具卡片不会进入事件流或历史，
但已经产生的文本、思考和 Provider Usage 仍会进入本轮统计。达到恢复上限后保持明确失败终态。
终态指标中的 `tool_argument_retries` 记录实际恢复次数，失败终态仍使用正确的
`provider_responses` 字段保留可诊断的 Provider 响应标识。

### 任务计划终态

后台任务计划调用主智能体时不能只检查是否收到 `done`。`done.metadata.status` 的语义为：

- 缺省：兼容旧式成功事件；
- `completed` / `success`：成功终态；
- `limited`：受工具轮数、上下文保护等边界限制；
- `cancelled`：用户或运行时取消；
- `failed`：明确失败；
- 其他显式非成功值：按不完整运行处理。

关键步骤遇到非成功终态会暂停计划，步骤错误保留 `agent_status`、`stop_reason` 和可用的 `failure` 详情。不得统一折叠为 `PlanAgentToolMissing`，否则前端只会显示“暂停”而看不到真正原因。

### 不应改变的边界

- 不修改 Kemo 网关 URL、请求字段、响应字段或 SSE 事件名称；
- 不在 Chat 与 Kemo 之间自动回退；
- 不根据模型名猜测工具能力；
- 不将 `reasoning_effort_map` 等模型能力映射用于工具终态判断；
- 不自动重试已经可能产生副作用的工具调用；
- 不延迟正常流式文本与思考；工具调用卡片允许延迟到同一 Provider 响应的整批参数校验完成后展示。

### 验证

相关测试位于：

- `tests/config/test_config_provider.py`：真实 Chat HTTP/SSE 拼接、截断参数、干净 EOF；
- `tests/provider/test_provider_protocol.py`：Chat 终态映射、Kemo `parse_error` 拦截、运行错误传播；
- `tests/cron/test_task_plan.py`：非成功主运行终态与暂停原因；
- `tests/core/test_runtime_features.py`：正常流式实时转发、工具续轮、错误和取消回归。

