# Provider 与传输可靠性功能说明

本文集中说明 Provider 工具调用完整性、网络传输恢复、SSE 续传、取消和安全边界。工具参数校验与网络请求恢复是两个独立阶段，分别按对应章节执行。

## 共享 API 请求池的空间公平

`provider_request_slot` 使用进程级公平请求池，沿用
`provider_runtime.max_concurrent_requests`（默认 10）和 `request_semaphore_timeout`
（默认 300 秒）。主智能体、子代理及已有受控请求共同计数，不按窗口分别创建额度。
按可信安装根/用户/来源/会话分组，优先服务占用较少的等待空间，同等占用时轮转；
同组 FIFO，没有竞争时允许一个空间使用全部空闲槽位。线程工具和子代理执行入口
恢复框架传递的空间身份，不从工具业务参数、任务 ID 或浏览器窗口数分配额外配额。

注册等待票据和选择当前池使用同一工厂锁；有在途请求或已登记等待者时不替换池，
避免空闲重配与尚未开始等待的线程竞态突破总上限。取消/等待超时移除票据并唤醒其他
等待者，真正离开请求上下文后才释放占用。API 状态快照字段与网页现有计数接口不变。
不抢占已发出的请求，不承诺服务端未返回时新空间立即启动，也不宣称跨服务进程共享。
子任务入队余量、执行额度与 API 槽位是三层不同约束，详见 `agent-task-coordination.md`。

## 跨轮缓存前缀与脱敏诊断

历史工具结果重建应保留原始 Kemo 内容块及 JSON 字段顺序，不把 `type/data` 内容块数组再次包装成结果数据；历史工具参数也沿用本轮发送时的字段顺序。旧 `tool.calls` 格式同样不额外排序。原始历史不修改；只有现有中途压缩规则或工具结果硬上限确实改写了 Provider 视图时，才移除原生结果副本，防止绕过限制。

每次主对话 Provider 调用在映射前生成脱敏指纹，保存到 `history_rounds.metric_json` 的 `provider_request_diagnostics`。记录包含请求标识、迭代/尝试序号、系统提示词/各段/工具定义/控制参数的 SHA-256，以及逐 Item 内容和累计前缀的 SHA-256、字节长度；不保存额外提示词、工具正文、凭据或 Provider State。随机 Item ID、保存时补充的元数据及状态不参与前缀比较，工具配对 ID 和实际内容仍参与。失败、取消与受控停止也保存已经尝试的请求指纹。

指纹作用域为 `agent_before_provider_mapping`，只能证明 Agent 侧输入是否变化，不代表网关映射后模型实际收到的字节；不能仅凭指纹一致就断言云端故障。历史请求没有这项记录时，不能事后补造当时的系统提示词或前缀证据。

## 运行级中断自动重试

输入上下文超限在主运行时单独恢复：既处理异常，也检查结构化失败终态中的嵌套错误；
`context_length_exceeded`、`input_length_exceeded`、`maximum context length`、
`prompt is too long` 等输入长度证据才触发压缩。只有网关包装码
`PROVIDER_BAD_RESPONSE` 和 HTTP 400 不足以认定输入超限；输出的
`max_output_tokens` 或 `finish_reason=length` 不属于此路径。

每次内部恢复保留当前用户输入、运行中引导、已完成的本轮工具调用及结果，
只移除被新摘要完整覆盖的旧轮次。即使本地估算尚未超过配置上限，服务端明确拒绝后，
仍必须比上次减少历史；最多两次内部恢复，最后一次以仅保留当前轮重试。
有效上限取配置 `token_limit` 与 Provider 声明输入上限的较小值；
摘要完成后重新计算包括摘要、固定提示词、工具定义与当前轮尾部的预算。
没有可移除历史、摘要失败、最后一轮仍超限或恢复预算耗尽时明确结束当前尝试，
之后是否进行运行级重试仍遵循下文独立规则。已执行工具不因上下文恢复而重新执行。

主对话运行（L2）和子代理运行（L4）共用 `run/retry/` 的错误序列账本。一次**没有进展的连续错误序列**最多执行 **5 次重试**，即该序列的第一次失败加 5 次重试共 **6 次尝试**；`retrying` 事件中的 `max_attempts` 因此仍为 6，但它表示当前错误序列的尝试上限，不是整个 Run 的总尝试上限。每次尝试都会创建新的逻辑 Provider 请求和新的 `request_id`，可能产生新的模型计费，不属于同一请求的网络续传。

重试额度只属于当前 Run 内当前错误序列，不写入会话级持久计数器。一次尝试在失败前只要成功接受过至少一次完整 Provider 响应，该错误序列就已经结束；尝试末尾的新失败按新错误序列的第一次失败计算，重新获得完整的 5 次重试额度。因此上游在同一长工具循环里反复“中断—恢复—再中断”时，单个 Run 的累计尝试次数理论上不设上限；只有连续 6 次都没有接受过完整响应的失败才会停止。半截流式文本、半截推理内容和提前到达的 usage 不算进展，避免上游每次只输出少量 token 就无限刷新额度。用户稍后重新发起或失败后点击“继续”形成新 Run 时，同样重新计满。

`retrying.metadata.failed_attempt` 与 `consecutive_failures` 表示当前错误序列内的失败序号，会在有进展后重新从 1 开始；`run_attempt` 表示当前 Run 的累计尝试序号，始终单调递增；`retry_progress` 表示这次失败前是否已经接受过完整响应。前端使用 `run_attempt` 生成重试快照边界，避免错误序列重置后出现重复组件 key；展示文案仍使用当前错误序列的 `next_attempt/max_attempts`。

除以下边界外，错误声明为 `retryable=false`、HTTP 400/401/403/404/409/422、鉴权错误、请求校验错误、协议错误、上下文压缩恢复耗尽、工具参数修复耗尽和普通运行时异常都必须消耗当前错误序列的完整预算后才停止：

- 用户取消：尊重用户意志，不重试，立即落盘；
- `completed` 和工具轮数上限产生的 `limited`：属于正常或受控结束，不重试；
- `SystemExit` / `KeyboardInterrupt` / `GeneratorExit` / `MemoryError` 是宿主控制或资源异常，
  原异常向外传播，不消耗业务失败重试计数，也不改写为工具失败或超时。
  后台子代理线程必须先记录真实 failed / host_exception 终态、完成通知并释放执行占用；
  同步 wait 重新抛出原异常，不把异常对象写入公开快照或持久化回执。

工具参数修复和上下文压缩属于尝试内部的修复型重发（L3），各自预算不计入上述 5 次；只有其自身预算耗尽后，最终失败才进入 L2/L4 的错误序列。成功工具结果的跨尝试重放仍受 32 次、120,000 字符的上限和副作用安全规则约束。用户取消（包括重试退避等待期间取消）始终立即停止，不因已经取得进展而继续重试。

主/子代理共用 `run/retry/recovery.py` 的动作级复用策略。file 动态读取、
expand_call 状态查询、subagent_dispatch list/status、shell status、network get/read
必须重新查询，不能把“仍在运行”缓存成永远运行。相反，成功的发消息、Shell 启动、
子任务派发/续跑及写操作仍保留同签名复用，避免运行级恢复制造重复副作用。
这里“禁止复用”代表会重新执行，不代表禁止调用；不能把默认策略翻转成仅复用只读工具。

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

Kemo 2.0 在线路上只定义 `Last-Event-ID` 作为服务端恢复游标。`sequence` 仅用于客户端本地连续性校验，不作为查询参数发送；内部调用若只提供 `resume_from_sequence` 而没有 `Last-Event-ID`，会在发起网络请求前被拒绝。

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
- Kemo 2.0 `Last-Event-ID` 恢复边界。

这组单元测试验证客户端状态机，不替代真实 Nginx/CDN、丢包、网关进程崩溃和磁盘故障测试。发布前应同时运行 Agent 与网关两侧的传输稳定性测试。

### Kemo 2.0 固定协议制品与契约门禁

`kemo-agent 1.5.2` 与 `kemo-adapter-api 1.0.0` 使用精确 Kemo 2.0 线路协议，并与 `kemo-graph 1.6.0` 的请求/响应合同对齐。权威源码在
`provider/protocol/`，其 `spec/` 生成 `schema.json`、`model-index.json`、`invariants.json`、
`freeze.json` 与脱敏 Fixture；Gateway 只消费由该目录构建并以 lock 文件固定 SHA-256 的
`vendor/kemo_protocol-2.0.zip`。这保证两仓不各自维护模型副本，也不提供 Kemo 1.x shim。

Agent 门禁：

```powershell
python -m pytest tests/contracts/kemo_v2 -q
python -m provider.protocol.spec.build_artifact E:\code\kemo-adapter-api\vendor
```

Gateway 门禁：

```powershell
python -m pytest tests/contracts/kemo_v2 -q
python -m tests --suite kemo-contract -q
```

修改 Request/Response/Batch、Item/ItemStart、Asset、Embedding、Rerank、能力、工具或 SSE 时，
必须先更新 Agent 权威模型和 Fixture，再重建制品、更新 lock 摘要，并通过双仓 artifact contract。
`protocol_version` 精确为 `2.0`，所有公开端点使用 `X-Kemo-Protocol-Version: 2.0`；错误 envelope、
SSE sequence、ItemStart、工具整批提交和终态规则均属于同一制品。上述离线门禁不替代真实代理、
进程崩溃或上游联调，未测的外部行为必须单独注明。

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

Chat Bridge 支持现代 `message.tool_calls[]` 和旧式单个 `message.function_call`。流式聚合遵守以下身份与参数规则：

- `tool_calls[].id` 与 `function.name` 只在首帧赋值：兼容服务每帧重复下发完整 `id`/`name` 不会被拼成 `call_xxxcall_xxx`；官方把长名拆成多帧（如 `history_` + `search`）时由 `arguments` 分片拼接承担语义。
- `tool_calls[].index` 允许 null、字符串或负数等宽松取值（`_safe_call_index`），非法时回退到帧内枚举位置，不再使整条流中断。
- `function.arguments` 通常是字符串分片，按序拼接；部分兼容服务直接下发完整 JSON 对象，此时整体采纳并忽略重复对象（`_merge_tool_arguments`）。
- 流结束发布 `tool_call_start` 时透传 `raw_arguments` 原文，供多轮工具循环按原文回放，避免重序列化损失。

参数规则如下：

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

#### Chat 请求净化

Chat 请求在 HTTP 序列化前按 adapter 内的策略净化；DeepSeek 保留独立分支：

- 普通 Chat 默认不把内部逻辑控制 `reasoning_enabled` / `reasoning_effort` 发给上游，
  也不发送框架 metadata。模型支持思考不代表它接受这两个 HTTP 字段；厂商参数必须由
  有效 transport 档案声明（框架内置或用户显式覆盖），不能根据“OpenAI 兼容”承诺所有高级能力；
- DeepSeek 分支使用 `thinking.type` 和 `reasoning_effort`，显式关闭思考时尊重关闭设置；缺失历史思考字段按下一节的兼容占位策略处理；
- 不注入 `stream_options.include_usage`：usage 依赖上游自愿返回，缺失时按输出文本估算 completion tokens；
- 工具终态携带的 `finish_reason` 附着在工具事件上，截断调用在发布前即可被拦截。

`provider.capabilities.transport.reasoning.request_format` 支持以下值。下列配置是已有
capabilities 的 **transport 片段**，不代替完整模型能力声明；既有外层 capabilities 包装也支持。

| 值 | Chat HTTP 请求行为 |
|---|---|
| `none`（默认） | 不自动发送思考控制字段 |
| `fields` | 按 `enabled_field` / `effort_field` 发送；只配置一个时只发送该字段 |
| `effort` | 仅发送 `effort_field`，默认名为 `reasoning_effort` |
| `openrouter` | 使用嵌套 `reasoning.enabled` / `reasoning.effort`；已有 `max_tokens` 预算时不再添加 effort |

未写 `request_format` 但配置了字段名时，按 `fields` 处理；显式选择 `fields` 且两个字段名
都未配置时，沿用 `reasoning_enabled` / `reasoning_effort`。可选 `effort_map` 把内部档位映射为
上游取值。字段名必须是合法标识符，不能覆盖 `model`、`messages`、`tools`、`stream`；
开关和档位不能映射到同一字段。

例如，只接受思考开关的服务使用：

```json
{
  "transport": {
    "reasoning": {
      "enabled_field": "enable_thinking"
    }
  }
}
```

只接受 effort 的服务使用 `{"reasoning":{"request_format":"effort"}}`；
使用嵌套格式的服务使用 `{"reasoning":{"request_format":"openrouter"}}`，均放在 transport 内。
仅在上游明确支持时才设置 `transport.metadata_supported=true`。
这些声明只控制 Chat adapter 的请求映射，不修改 Kemo 请求字段或原生网关路径。

#### Chat 原生图片与 Kemo 内联边界

- Chat 使用内部 `ChatImageContent` 支持至多 20 MiB 的原始图片字节；同现有媒体装载上限一致，
  不为了复用 Kemo 对象而额外套用 1 MiB 限制，也不自动压缩原图。厂商实际限制仍需分别遵守。
- 主循环 Chat→内部请求映射必须显式传入真实 transport；子代理媒体投影采用相同图片类型，
  保持原始校验和、MIME、detail 与工具调用/结果配对。
- 默认协议模型和 Chat→Kemo 转换仍按原生 1 MiB 上限处理；内部专用图片类型不得绕过
  Kemo Provider 的原生验证。大图通过 Kemo Asset 引用发送，不修改 wire Schema。
- 媒体准备中的 Pydantic 校验失败转换为脱敏的媒体错误，并准确记录 failed；
  不能将完整 Base64 放入异常文案、诊断或历史。

#### 普通 Chat 的响应兼容与思考续接

- 解析真实思考时依次支持 `reasoning_content`、字符串 `reasoning` 和 `reasoning_details`
  内的可读 `text` / `summary`；加密块和签名不作为可见思考展示。
- 正文支持字符串以及 `text` / `output_text` 文本块；usage 兼容 `input_tokens` /
  `output_tokens`，已有标准 Chat 计数字段优先。
- 非流式、SSE 和流式请求返回 JSON 的降级路径支持一层 `data` 包装；顶层 choices 优先，
  不递归解包，`error` / `success=false` 仍按失败处理。空响应或仅有 `[DONE]` 不能靠
  伪思考变成成功响应。
- 缺失上游思考而正文、拒绝说明或工具响应有效时，不凭空补成模型思考。
  兼容占位仅作为本地诊断处理，不进入协议 `ReasoningItem.content`、公开思考事件或下一轮
  `reasoning_content`；真实上游思考及签名状态仍按各自合同保留。
- `reasoning_details` 按原值保留；SSE 按 index 聚合文本、密文和签名分片，
  不改写签名、跨模型或跨地址复用。状态使用现有 `ReasoningItem.provider_state`
  中 `provider=chat-adapter` 的 opaque 数据，不新增协议字段；同次响应正文和工具调用
  合并为一个 assistant 消息，使状态留在工具调用消息上。
- 只有配置 `transport.reasoning.history_field` 为 `reasoning_content` 或 `reasoning`
  时才保存和回放对应的真实普通思考；`reasoning_details` 自动保留原始续接块，也可以显式指定。
  损坏、版本不符或地址/模型不匹配的状态不回放，内部标记在发 HTTP 前移除。
- 状态先按现有凭据脱敏规则检查，再压缩；编码后最多 8192 字符、解码后最多 262144 字节，
  超限明确失败，不能截断签名再执行工具。状态通过既有内部持久化通道保存，
  不写入公开流事件；公开帧也移除含签名或密文的 `reasoning_details`。
- 普通 Chat 地址保留配置的 API 前缀和查询参数，已包含 `/chat/completions` 时不重复拼接，
  不擅自增加或移除 `/v1`。

这些分支已用本地模拟中转站覆盖字段拒绝、真实思考别名、签名续接、持久化回放、
空响应和流式降级；不代表已对所有厂商或真实中转站完成联调。DeepSeek 的专用占位与请求策略
按下一节的传输规则执行；适配器占位不能越过原生协议边界。

#### DeepSeek 官方接口与第三方中转站

DeepSeek 的特殊适配只在 Chat adapter 内按官方主机或 `deepseek-` 模型名前缀识别。
思考开关与档位继续映射为 `thinking` 和 `reasoning_effort`，不修改 Kemo 线路协议。

- 官方 `api.deepseek.com` 兼容根地址与 `/v1` 两种地址写法；第三方中转站必须原样保留配置的
  API 前缀，例如 `/v1` 或 `/proxy/v1`，再拼接 `/chat/completions`。不能因为模型属于
  DeepSeek 就移除中转站的版本路径，否则可能命中返回 HTTP 200 的网站 HTML 页面。
- 上游提供真实 `reasoning_content` 时完整保留并回传；同一响应中的正文说明和工具调用
  组合到同一个 assistant 消息，避免思考内容只附在说明消息上、工具消息反而丢失思考。
  合并只发生在紧邻的正文与工具调用之间，不跨越新的思考片段或其他消息。
- 开启思考并携带工具时，历史 assistant 消息缺少思考字段或字段为空，Chat adapter 为其补入
  固定占位 `[DeepSeek compatibility placeholder: upstream reasoning was not provided.]`。
  官方接口和中转站默认均启用此兼容方式。占位只说明上游缺失数据，不冒充真实模型思考；
  已有非空思考内容保持原样，序列化不回写调用方传入的历史字典。
- 非流式、SSE 和流式 JSON 降级共用此策略。新响应缺失思考时，兼容占位保留
  `reasoning_synthetic=true` 和 `missing_upstream_reasoning` 诊断，方便识别其来源。
  完整工具调用仍可继续执行，截断、无效参数和其他错误继续按原合同拦截。
- 需要禁止缺失工具思考的部署可在
  `provider.capabilities.transport.reasoning.require_tool_reasoning` 设置布尔值 `true`，
  显式启用严格校验；此字段也支持既有 capabilities 包装结构。严格模式下新工具响应缺失真实
  思考仍返回 incomplete，不发布可执行工具调用。
- DeepSeek 分支接受一层 `{"data": <标准 Chat 响应>}` 包装，前提是内部有非空 `choices`
  及消息对象。已有顶层 `choices` 优先，不展开错误响应或任意递归 `data`。
  非流式与流式请求返回 JSON 的既有降级路径共用此解析；普通 Chat 的包装兼容见上一节。
- Chat 返回明确的 HTML/XHTML Content-Type 时，以 `invalid_response` 提示核对
  `base_url` 和 `/v1` 路径，不再把网页正文当 SSE 解析后误报流式中断；缺省 Content-Type
  和其他既有兼容响应仍按原路径处理。

#### Chat 网络恢复归属

Chat HTTP 传输层对一次调用只发起一次请求，连接失败、429、5xx 和流式中断向统一运行级重试所有者报告，不在传输层另开重试循环：

- 错误保留 HTTP 状态、错误分类和可用的 `Retry-After`（上限 10 秒），运行级预算按本文前面的错误序列规则执行；
- 已产出文本、思考或工具分片后，HTTP 传输层也不自行重发，避免重复拼接输出或调用工具；
- 401、403、409 和普通 400 同样向上报告，不在本层改写请求规避错误。

#### 工具不支持错误

上游 400 且错误信息匹配"tools/function calling 不受支持"类短语（`_TOOLS_UNSUPPORTED_PHRASES`）时，Chat 传输层将其标记为 `tools_unsupported` 并向统一运行级重试所有者报告：

- 不剥离 `tools` 或 `tool_choice` 再次发送，不把需要工具的请求静默降级为普通文本请求；
- 普通 400（模型名错误、参数 schema 错误等）保持原有错误分类。

#### 流式 JSON 响应降级

部分兼容服务对 `stream=true` 请求返回 `application/json` 而非 SSE。Chat 传输层按 Content-Type 识别后直接按非流式响应解析并合成事件序列（`response_format=json_fallback`），不重发请求。

#### Chat 取消边界（当前限制）

`cancel_event` 契约已在传输层就绪，但主运行时对 Chat 模式尚未传递该参数，流式读取期间的取消只能等待读取超时。这是已知边界，不是缺陷回退。

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
- `tests/provider/test_provider_protocol.py`：Chat 终态映射、Kemo `parse_error` 拦截、运行错误传播、传输单次请求、429 Retry-After、工具不支持时不剥离工具重发、JSON 响应降级、id/name 幂等聚合、完整 JSON 对象参数和截断工具拦截；
- `tests/provider/test_chat_compatibility.py`：请求字段净化与显式映射、思考别名、一层中转包装、地址保留、真实签名和密文经过流式/非流式及持久化的工具续接、公开事件状态隔离、状态容量与凭据检查；
- `tests/cron/test_task_plan.py`：非成功主运行终态与暂停原因；
- `tests/core/test_runtime_features.py`：正常流式实时转发、工具续轮、错误和取消回归。

### Kemo 2.0 当前线协议硬边界

当前 Agent 与 `kemo-adapter-api 1.0.0` 使用精确 `Kemo 2.0`，服务端只接受并输出
`protocol_version="2.0"`；不提供 Kemo 1.x shim，也不按 `2.x` 前缀放行未来版本。所有公开端点
通过 `X-Kemo-Protocol-Version: 2.0` 协商；有 JSON body 时 header 与 body 必须一致。

- 非流式 `n>1` 返回 `kemo.response_batch`，一次请求只计一份 batch usage；流式请求、工具、历史工具条目与 structured output 互斥。
- Asset、Embedding、Rerank、模型目录和错误响应均属于同一 2.0 制品；错误 body 使用 `object="kemo.error"`。
- SSE 事件使用 `evt_`、`req_`、`resp_` 标识，sequence 必须连续；`output_item.added` 身份快照先于 delta，文本块按 `(response_id,item_id,content_index)` 隔离，done 后不得继续 delta。
- `tool_call.completed` 只作整批暂存，只有 `response.completed(status=requires_action)` 到达后调用方才可执行；断流不得提前执行。
- 双仓共享固定 `vendor/kemo_protocol-2.0.zip`，由 Agent 的 `provider/protocol` 生成，Gateway 通过 lock 文件校验 SHA-256。

## Chat 自动兼容档案（2026-10-26）

普通用户只配置 Chat 地址、模型和凭据，不必手写 `capabilities` 或运行检测工具。
`provider/chat_profiles.py` 按实际请求模型统一解析有效档案，并由 Chat Bridge 将相同档案交给
能力校验、请求转换和 HTTP 策略；子代理换模型重新解析，不借用主模型档案。
解析不发探测请求、不改用户配置，亦不修改原生 Kemo 的协议默认与 vendor 选项授权。

- 优先级：用户显式覆盖 → 内置精确型号兼容档案 → 基础 Chat 兼容策略。
  显式能力组整体替换该组，避免关闭 function_calling 后还继承 parallel=true；transport 单独合并。
- 基础策略允许普通 function calling、并行工具、auto/none/required/named、常用采样字段
  temperature/top_p/stop/presence_penalty/frequency_penalty，以及非 strict 的 json_object。
  来源标记为 `capability_source=compat-default`、`capability_verification=assumed`，
  表示兼容尝试而不是端点经过验证。无凭据探测、无剥离工具重试，服务端不支持仍明确报错。
- 目前内置 `deepseek-flash`、`deepseek-v4-flash`、`deepseek-v4-flash-vision-exp`、
  `deepseek-v4-pro` 精确别名的思考和工具规则；不使用 r1*/v3* 前缀断言工具不可用。
  官方地址标记 documented-endpoint，同名中转标记 assumed-relay，不冒充中转已联调。
  官方依据为 2026-10-26 的 DeepSeek Models & Pricing 和 Thinking Mode 文档。
- DeepSeek 逻辑档位按既有明确映射发成 thinking.type / reasoning_effort；工具模式保守限制
  auto/none，与当前转换器一致。Flash 档案声明 image 输入，v4-pro 不声明；没有显式
  input_modalities 限制时，视觉路由可直接采用本地档案。已有 text-only 限制仍保留。
- 未知 Chat 不声明 reasoning、strict JSON schema、思考摘要、跨轮持久思考状态和多媒体输出。
  显式思考声明没有对应 wire 控制时，有效档案标记 reasoning_unmapped 并关闭该能力，
  不再出现“框架声称已开启、实际发送时悄悄丢弃控制”的成功假象。
- 当前工具结果仅原生表达 text/json/reference，multimodal_results 始终 false；媒体工具结果
  明确拒绝，不把图片描述 JSON 当作原生看图。已有 attach_media 队列可把图片投影到下一次
  user 消息，是否接受仍按实际模型模态与视觉路由校验。
- limits 的 None 表示没有声明上限，不保证服务端无限。地址保留自定义 API 前缀、完整
  chat/completions 路径及查询参数；只有裸根地址沿用 /v1 默认，不对自定义路径强行追加 /v1。

验证同时断言最终 HTTP body、流式与非流式、工具调用→真实插件→工具结果→继续回答、
历史提交和子代理模型切换。测试使用离线模拟服务，不等于所有真实厂商都已完成在线联调。

## 请求与能力的权威边界

- 原生协议校验不接受 `allow_chat_images` 参数；其 ImageContent 始终按 wire 1 MiB 重验。
  Chat 20 MiB 原图由适配器本地校验，不调用一个放宽原生模型的开关。
- Chat 有效档案按上一节自动选择，显式覆盖保留；未知端点的基础兼容假设明确标记，
  未实现/未确认的高级能力保持关闭，不能用自造的全支持默认能力自验。
- reasoning 必须同时有有效能力与对应传输映射；主对话和子代理据此自动选择并显式提交
  启用标记，不要求用户自行测试，也不因“Chat兼容”而向所有端点发思考字段。
- `provider_options` 全量校验，只有 `vendor.<provider>.<option>` 命名且 profile 显式登记的键
  可以进入适配；非 vendor 键、私有前缀和嵌套传输凭据均拒绝，不把未知键当作内部选项透传。
  映射目标使用有限白名单；正式 tool_choice/structured_output/generation/reasoning 只有各自
  正式字段一个来源，不能借 options 绕过能力闸门。基础采样字段只走 generation，不从 options 旁路。
- 原生 LLM/Embedding/Rerank 的非空 options 同样需要本地 `capabilities.provider_option_map` 授权，校验后保持
  vendor 键原样发送，不把 Chat 上游键写入 Kemo 正文；空 options 不增加网络能力探测。
- ChatRequest 最终 payload 的 extra 使用已知字段白名单；仅适配器显式选择的映射字段可扩展，
  不接受任意私有簿记键，仍禁止覆盖 messages/system/input/instructions、模型、工具与凭据。
- 反向合同同时验证旁路参数不存在、未知高级能力仍被拒、基础兼容标记与最终 payload 一致、合成思考不进入协议/回放。
