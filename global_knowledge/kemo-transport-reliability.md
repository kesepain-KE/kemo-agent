# Kemo 协议网络稳定性与恢复边界

本文说明 `provider.type=kemo` 时，kemo-agent 到 Kemo 网关之间的请求幂等、SSE 续传、有限重试、取消和安全边界。普通 `chat` 协议不使用本链路，也不会自动切换到 Kemo。

## 请求身份与幂等

- 每次真实 Provider 迭代生成独立 `request_id`，HTTP 同时发送 `Idempotency-Key` 和 `X-Request-ID`，两者均等于正文中的 `request_id`。
- 一次传输重试或 SSE 续传必须复用完全相同的请求正文和 `request_id`。不得为了“重试”重新生成 ID，否则网关会把它视为新的模型调用。
- 工具执行后的下一次 Provider 迭代是新的逻辑请求，会生成新 `request_id`，并通过 `parent_request_id` 连接上一迭代。这不属于网络重试。
- 网关若发现相同 ID 对应不同正文，应返回不可重试的幂等冲突；客户端不会把 HTTP 409 当成普通瞬时网络错误。

## 自动恢复范围

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

## SSE 断线续传

客户端只在成功校验一条完整事件后推进恢复游标。连接断开后：

1. 复用原请求正文与 `request_id`；
2. 发送最后一个完整事件的 `Last-Event-ID`；
3. 在本地保留最后接收的 `sequence`，使用同一个顺序守卫继续校验后续事件；
4. 丢弃事件 ID 完全相同的重放事件，不重复展示文本或工具调用。

Kemo 1.0 在线路上只定义 `Last-Event-ID` 作为服务端恢复游标。`sequence` 仅用于客户端本地连续性校验，不作为查询参数发送；内部调用若只提供 `resume_from_sequence` 而没有 `Last-Event-ID`，会在发起网络请求前被拒绝。

续传响应必须保持相同 `response_id`。如果网关重启或状态丢失后为同一请求创建了另一份 Response，客户端会拒绝拼接并明确失败；它不会把两份模型输出连接成一轮伪造回答。

SSE 注释心跳不会形成 Provider 事件，也不会进入对话历史；它只负责保持代理、CDN 和路由器上的空闲连接。心跳周期由网关控制。

## 取消

- 主运行时把 `cancel_event` 传入 Kemo 原生客户端。
- SSE、非流式 LLM、Embedding 和 Rerank 读取期间都有本地取消观察器；用户取消后会主动关闭阻塞连接，而不是固定等待 120 秒读取超时。
- 已知 `response_id` 时，客户端会以最多 2 秒的独立短请求尽力调用网关 `/cancel`。
- 远端取消失败不会把本地取消轮次改记为 Provider 失败；网关仍应依靠执行期限和租约清理孤立任务。

## 数据完整性

- SSE 对事件 JSON、`request_id`、`response_id`、`event_id`、`sequence`、唯一终态及终态后事件进行校验。
- Asset 上传发送大小和 SHA-256；下载先写随机 `.part`，校验大小和 SHA-256 后原子替换目标文件，失败时清理半成品。
- 这些校验用于发现截断、错序和内容损坏，不等同于链路加密。

## HTTPS 边界

Kemo 协议允许 `http://`，方便同机或可信内网部署，但 HTTP 不提供机密性和抗篡改能力。跨主机、非可信局域网或公网部署必须在网关前使用 HTTPS 反向代理，并保持系统 TLS 证书校验。不得通过关闭证书校验来处理证书配置错误。

## 验证

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
