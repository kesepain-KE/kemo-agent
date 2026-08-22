# Provider 工具调用完整性与终态边界

本文说明 `provider.type=chat` 与 `provider.type=kemo` 的工具调用何时可执行，以及输出截断、参数损坏和任务计划暂停时应如何诊断。实现入口主要位于 `provider/openai_chat.py`、`provider/adapters/compat.py`、`run/conversation/` 和 `run/tasks/`。

## 共同原则

工具名称出现不等于工具调用已经完整。一次调用进入 `run/conversation/` 的执行循环前必须同时满足：

1. Provider 已给出允许继续工具循环的终态；
2. 参数已经完整解析为 JSON 对象；
3. 没有协议层或适配层记录的解析错误；
4. 当前 Run 没有进入取消、失败或受控限制终态。

任何一项不满足，都不得把残缺文本包装成普通参数后尝试执行。原始参数仅用于有界诊断，不能成为工具输入。

## Chat Completions 兼容链路

Chat Bridge 支持现代 `message.tool_calls[]` 和旧式单个 `message.function_call`，流式参数按调用索引拼接。参数规则如下：

- JSON 对象：可执行；
- 缺省或空参数：兼容为 `{}`；
- 无效 JSON：记录 `parse_error`，不可执行；
- JSON 数组、字符串、数字或布尔值：根节点不是对象，不可执行；
- 原始参数最多保留 500 字符进入错误诊断，不能以 `_raw` 字段伪装成合法参数。

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

## Kemo 原生链路

Kemo 网关已经提供类型化 `ToolCallItem`、明确响应状态和有序 SSE。Agent 不重新定义线路协议，只增加执行前防御：

- `status=requires_action` 且工具项完整时才继续工具循环；
- `ToolCallItem.parse_error` 非空时转为 `ProviderToolArgumentsError`；
- `arguments_raw` 只用于诊断，不代替 `arguments`；
- `response.incomplete/failed/cancelled` 进入明确运行错误，不触发工具执行；
- 网络重试与业务终态分离，参数损坏不是可重试传输错误。

## 工具参数生成恢复

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

## 任务计划终态

后台任务计划调用主智能体时不能只检查是否收到 `done`。`done.metadata.status` 的语义为：

- 缺省：兼容旧式成功事件；
- `completed` / `success`：成功终态；
- `limited`：受工具轮数、上下文保护等边界限制；
- `cancelled`：用户或运行时取消；
- `failed`：明确失败；
- 其他显式非成功值：按不完整运行处理。

关键步骤遇到非成功终态会暂停计划，步骤错误保留 `agent_status`、`stop_reason` 和可用的 `failure` 详情。不得统一折叠为 `PlanAgentToolMissing`，否则前端只会显示“暂停”而看不到真正原因。

## 不应改变的边界

- 不修改 Kemo 网关 URL、请求字段、响应字段或 SSE 事件名称；
- 不在 Chat 与 Kemo 之间自动回退；
- 不根据模型名猜测工具能力；
- 不将 `reasoning_effort_map` 等模型能力映射用于工具终态判断；
- 不自动重试已经可能产生副作用的工具调用；
- 不延迟正常流式文本与思考；工具调用卡片允许延迟到同一 Provider 响应的整批参数校验完成后展示。

## 验证

相关测试位于：

- `tests/config/test_config_provider.py`：真实 Chat HTTP/SSE 拼接、截断参数、干净 EOF；
- `tests/provider/test_provider_protocol.py`：Chat 终态映射、Kemo `parse_error` 拦截、运行错误传播；
- `tests/cron/test_task_plan.py`：非成功主运行终态与暂停原因；
- `tests/core/test_runtime_features.py`：正常流式实时转发、工具续轮、错误和取消回归。
