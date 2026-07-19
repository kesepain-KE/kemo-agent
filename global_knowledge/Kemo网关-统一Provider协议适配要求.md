# Kemo 网关统一 Provider 协议适配要求

> 文档版本：v1.0  
> 协议版本：`1.0`  
> 对应 kemo-agent 实现日期：2026-07-19  
> 状态：网关联调基线 / 必须实现

## 1. 目标与边界

Kemo 网关需要原生接收 kemo-agent 的统一 Provider Request，并返回统一 Response 或统一 SSE 事件。网关是以下信息的权威来源：

- 模型与厂商协议转换；
- 模型能力声明与多模态降级；
- Provider 请求状态、查询、取消和断线恢复；
- Provider State 的生成、加密、绑定和恢复；
- Provider 精确 Usage、网关阶段 Usage 和原始请求 ID；
- 厂商错误到统一错误的映射。

kemo-agent 继续负责：系统提示词编译、上下文选择、会话历史、业务工具权限与执行、工具失败隔离、用户 Asset 访问策略和最终 Run 事件。网关不得执行 kemo-agent 的业务工具，也不得修改 `system_prompt` 的语义。

## 2. HTTP 接口

所有路径均相对于 kemo-agent 配置的 `provider.base_url`。

| 优先级 | 方法与路径 | 用途 | 成功响应 |
| --- | --- | --- | --- |
| P0 | `POST /model/responses` | 创建非流式或流式响应 | JSON `KemoResponse` 或 SSE |
| P0 | `GET /model/responses/{response_id}` | 查询已创建响应 | JSON `KemoResponse` |
| P0 | `POST /model/responses/{response_id}/cancel` | 取消仍在运行的响应 | JSON `KemoResponse(status=cancelled)` |
| P0 | `GET /model/capabilities?model={model}` | 获取模型权威能力 | `ModelCapabilities`，可直接返回或放在 `capabilities` 字段中 |

### 2.1 请求头

```http
Authorization: Bearer <api-key>
Content-Type: application/json
Accept: application/json | text/event-stream
X-Kemo-Protocol-Version: 1.0
Idempotency-Key: <request_id>
X-Request-ID: <request_id>
Last-Event-ID: <event_id>   # 仅恢复 SSE 时提供
```

要求：

- 必须校验 Bearer Token，不得把 Token 写入日志；
- `X-Kemo-Protocol-Version` 与正文 `protocol_version` 必须兼容；
- `Idempotency-Key` 和 `X-Request-ID` 当前均等于正文 `request_id`；
- `request_id` 在同一租户内必须幂等；相同 ID、相同正文返回同一逻辑响应，相同 ID、不同正文返回 `409 IDEMPOTENCY_CONFLICT`；
- 日志、链路追踪和 Provider 请求都必须关联 `request_id`、`response_id` 和 `provider_request_id`。

## 3. 创建响应请求

### 3.1 顶层字段

`POST /model/responses` 接收以下 JSON。除 `metadata`、`extensions` 和 `provider_options` 外，不得增加未知字段。

| 字段 | 类型 | 必需 | 规则 |
| --- | --- | --- | --- |
| `protocol_version` | string | 是 | 当前为 `1.0`；不支持的主版本必须拒绝 |
| `request_id` | string | 是 | 1–128 位稳定 ID；作为幂等键 |
| `parent_request_id` | string/null | 否 | 工具循环中指向上一次模型请求 |
| `attempt` | integer | 是 | 从 1 开始；同一逻辑请求重试次数 |
| `model` | string | 是 | 网关模型别名或路由名 |
| `stream` | boolean | 是 | `true` 返回 SSE，`false` 返回 JSON |
| `system_prompt` | string | 是 | kemo-agent 已编译完成的唯一系统提示词 |
| `reasoning` | object/null | 否 | 推理开关、强度、返回模式和上下文策略 |
| `generation` | object | 是 | 生成参数 |
| `output` | object | 是 | 期望输出模态及配置 |
| `tools` | array | 是 | 函数工具 Schema；可为空 |
| `input` | Item[] | 是 | 严格有序的完整上下文 |
| `provider_options` | object | 是 | 仅供网关/Adapter 读取的厂商扩展 |
| `metadata` | object | 是 | 当前含 user、source、session_id、run_id、iteration、window 等关联信息 |
| `extensions` | object | 是 | 协议未来扩展 |

`reasoning`：

```json
{
  "enabled": true,
  "effort": "medium",
  "return": "summary",
  "context": "auto"
}
```

`effort` 可取 `none|minimal|low|medium|high|xhigh|max`；`return` 可取 `none|summary|content|auto`；`context` 可取 `none|current_turn|all_turns|auto`。网关必须根据能力声明映射或返回 `CAPABILITY_ERROR`，不得静默忽略请求语义。

`generation`：

```json
{
  "max_output_tokens": 8192,
  "temperature": null,
  "top_p": null,
  "parallel_tool_calls": true
}
```

`output.modalities` 可包含 `text|audio|image|video`。请求了不支持的输出模态时，网关应执行明确的能力路由，或返回 `CAPABILITY_ERROR`。

### 3.2 Item

所有 Item 共有字段：

| 字段 | 规则 |
| --- | --- |
| `id` | 请求或响应内唯一，建议使用 `msg_`、`rs_`、`call_`、`result_` 前缀 |
| `type` | `message|reasoning|tool_call|tool_result` |
| `status` | `in_progress|completed|incomplete|failed` |
| `created_at` | RFC 3339 时间 |
| `metadata` | 业务元数据 |
| `extensions` | 未来扩展 |

`message`：

```json
{
  "id": "msg_user_1",
  "type": "message",
  "role": "user",
  "status": "completed",
  "content": [
    {"type": "text", "text": "分析这张图"},
    {"type": "image", "asset_id": "asset_image_1", "detail": "high"}
  ]
}
```

- `role` 仅为 `user|assistant`；
- `phase` 仅用于 assistant，可为 `commentary|final_answer`；
- user message 不允许 `phase`；
- `content` 至少一个 Content Block；文本和相关媒体必须保留在同一 `content[]` 中。

`reasoning`：

```json
{
  "id": "rs_1",
  "type": "reasoning",
  "status": "completed",
  "summary": "已检查设备告警和图片",
  "provider_state": {
    "kind": "encrypted",
    "data": "opaque-ciphertext",
    "provider": "upstream-name",
    "model": "model-name",
    "version": "1",
    "expires_at": "2026-07-20T00:00:00Z"
  }
}
```

`reasoning` 至少具有 `summary`、`content` 或 `provider_state` 之一。不得伪造厂商内部思维链；不透明状态应放在 `provider_state`。

`tool_call` 与 `tool_result`：

```json
{
  "id": "call_item_1",
  "type": "tool_call",
  "call_id": "call_1",
  "name": "lookup",
  "arguments": {"query": "E13"},
  "arguments_raw": "{\"query\":\"E13\"}",
  "status": "completed"
}
```

```json
{
  "id": "result_item_1",
  "type": "tool_result",
  "call_id": "call_1",
  "name": "lookup",
  "is_error": false,
  "content": [{"type": "json", "data": {"manual": "..."}}],
  "status": "completed"
}
```

要求：

- `call_id` 在单个请求的 `input[]` 内唯一；
- `tool_result` 必须位于匹配 `tool_call` 之后，且 `call_id`、`name` 完全相同；
- 工具参数增量未完成前不得执行工具；
- 网关只负责产生/回放工具调用，实际权限判定和执行由 kemo-agent Run 完成；
- `requires_action` 响应必须至少包含一个 `tool_call`。

### 3.3 Content Block

| `type` | 主要字段 | 要求 |
| --- | --- | --- |
| `text` | `text`, `language?` | `text` 为字符串 |
| `image` | `asset_id/source`, `mime_type?`, `detail`, `width?`, `height?` | `detail=auto|low|high` |
| `audio` | `asset_id/source`, `mime_type?`, `duration_ms?`, `transcript?` | 不支持原生音频时必须 ASR 或报能力错误 |
| `video` | `asset_id/source`, `duration_ms?`, `derived?` | 可路由到抽帧、ASR、时间轴阶段 |
| `file` | `asset_id/source`, `mime_type?`, `filename?` | 应按 MIME 与能力处理 |
| `json` | `data`, `schema_name?` | 任意 JSON 数据 |
| `reference` | `target_id`, `label?` | 指向历史或 Asset 派生对象 |

媒体必须至少提供 `asset_id` 或 `source`。`asset_id` 是稳定 ID，不得是本地文件路径。`source.kind` 可为 `object_store|url|data_url|provider_file_id|inline_base64`；网关必须执行第 11 节的媒体安全策略。

### 3.4 工具定义

```json
{
  "type": "function",
  "name": "lookup",
  "description": "查询资料",
  "parameters": {"type": "object", "properties": {}},
  "strict": true,
  "permission": "read",
  "metadata": {},
  "extensions": {}
}
```

`permission` 仅是提示信息，不代表授权；网关不得据此自行执行工具。

## 4. 非流式响应

```json
{
  "protocol_version": "1.0",
  "id": "resp_1",
  "request_id": "req_1",
  "object": "kemo.response",
  "status": "completed",
  "model": "gateway/model",
  "output": [],
  "usage": {},
  "error": null,
  "incomplete_details": null,
  "provider_response_id": "upstream-response-id",
  "metadata": {},
  "extensions": {}
}
```

| `status` | 必须满足 |
| --- | --- |
| `completed` | `output` 包含完成结果；最终文字使用 `message.role=assistant, phase=final_answer` |
| `requires_action` | `output` 至少包含一个完整 `tool_call` |
| `incomplete` | 必须包含 `incomplete_details.reason`，保留部分 `output` 和 Usage |
| `failed` | 必须包含统一 `error` |
| `cancelled` | 保留已生成的部分 `output` 和当前 Usage |

`output[]` 与 `input[]` 使用相同 Item 类型，并按实际产生顺序排列。kemo-agent 会把响应 Item 和工具结果写入 `items.json`，并在下一次请求中回放。

## 5. SSE 协议

当正文 `stream=true` 时，返回：

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive

id: evt_1
event: output_text.delta
data: {"type":"output_text.delta","event_id":"evt_1","sequence":1,"request_id":"req_1","response_id":"resp_1","item_id":"msg_1","content_index":0,"delta":"你好"}
```

### 5.1 事件信封

每个事件必须包含：

- `type`；
- 全局唯一且稳定的 `event_id`；
- 在单个 `response_id` 内从 0 开始严格连续的 `sequence`；
- `request_id`、`response_id`；
- RFC 3339 `timestamp`；
- 与事件相关时提供 `item_id`、`content_index`、`call_id`、`name`、`delta`、`item`、`usage`、`response` 或 `error`；
- `data`、`run_id`、`run_sequence` 可选。Run 的任务级 `run_sequence` 最终由 kemo-agent 重新编排。

### 5.2 事件类型和载荷

| 事件 | 载荷要求 |
| --- | --- |
| `response.created` | sequence=0；可在 `data` 中给出模型和路由信息 |
| `output_item.added` | `item_id`；可给出 `item` 或 `data.item_type` |
| `reasoning.summary.delta` | `item_id`, `delta` |
| `reasoning.content.delta` | `item_id`, `delta`；仅厂商允许公开时发送 |
| `tool_call.arguments.delta` | `item_id`, `call_id`, `name`, `delta`；delta 是 JSON 字符串片段 |
| `tool_call.completed` | 完整 `ToolCallItem`，且参数可解析 |
| `output_text.delta` | `item_id`, `content_index`, `delta` |
| `output_audio.delta` | `item_id`, `content_index`, `delta`；仅用于受限的小型音频流 |
| `output_media.completed` | 完整媒体 Item/Content 信息，不得只返回临时本地路径 |
| `usage.updated` | 完整或阶段性统一 `usage` |
| `response.completed` | 完整 `KemoResponse`；其状态可为 `completed` 或 `requires_action` |
| `response.incomplete` | 完整 `KemoResponse(status=incomplete)` |
| `response.failed` | 完整 `KemoResponse(status=failed)` |
| `response.cancelled` | 完整 `KemoResponse(status=cancelled)` |
| `error` | 完整统一 `error`；用于无法形成有效 Response 的流错误 |

`response.completed|incomplete|failed|cancelled|error` 均为终态。每个响应只能出现一个终态，终态后不得继续发送事件。`[DONE]` 可省略；无论是否发送 `[DONE]`，统一终态事件都不可省略。

### 5.3 去重和恢复

- kemo-agent 会按 `event_id` 去重，并验证 `sequence` 严格连续；跳号、倒序或终态后事件会被视为协议错误；
- SSE 中断不等于取消；网关必须继续维护响应状态；
- 恢复时 kemo-agent 使用相同 `request_id` 和请求正文重新 `POST /model/responses`，并提供 `Last-Event-ID`，也可能附加 `?resume_from_sequence=N`；
- 网关应从已确认事件之后重放；允许重放最后一个事件，但其 `event_id` 必须保持不变；
- 若事件缓存已过期，返回可重试错误并允许客户端通过查询接口取得完整终态；
- 查询和取消必须与创建响应使用相同的认证/租户边界。

## 6. Usage 与计量

### 6.1 顶层 Usage

```json
{
  "input_tokens": 1834,
  "cached_input_tokens": 620,
  "output_tokens": 928,
  "reasoning_tokens": 704,
  "visible_output_tokens": 224,
  "total_tokens": 2762,
  "measurement": {
    "mode": "gateway",
    "exact": true,
    "exact_fields": ["input_tokens", "output_tokens", "total_tokens"],
    "estimated_fields": []
  },
  "media": {
    "input_images": 2,
    "input_audio_seconds": 18.4,
    "input_video_seconds": 34.7,
    "output_audio_seconds": 0,
    "output_images": 0,
    "output_video_seconds": 0
  },
  "stages": [],
  "provider_raw": {}
}
```

`measurement.mode`：

- `provider`：直接使用上游精确值；
- `gateway`：网关进行了精确计量；
- `estimated`：估算；
- `mixed`：多阶段中既有精确值又有估算值；
- `unknown`：无法可靠取得。

### 6.2 StageUsage

```json
{
  "stage": "audio_transcription",
  "provider": "upstream",
  "model": "asr-model",
  "input_tokens": null,
  "cached_input_tokens": null,
  "output_tokens": 312,
  "reasoning_tokens": null,
  "total_tokens": 312,
  "measurement": {"mode": "provider", "exact": true},
  "media": {"input_audio_seconds": 18.4},
  "metadata": {},
  "extensions": {}
}
```

要求：

- ASR、视觉分析、视频抽帧/理解、主推理、TTS、图像/视频生成等阶段都要独立记录；
- 缺失字段必须为 `null` 或省略，不能用 0 冒充精确的零消耗；
- `reasoning_tokens` 通常已包含在 `output_tokens` 内，计算 `total_tokens` 时不得重复累加；
- `total_tokens` 优先使用 Provider 原始总量；
- `usage.updated` 可以是阶段值，终态 Response 的 Usage 必须是最终权威值；
- `provider_raw` 生产默认关闭或脱敏并限制大小，禁止包含密钥、完整签名 URL 或未加密 Provider State；
- 成本统计与 Token 统计分离，并带价格版本，当前协议不要求成本字段。

## 7. 模型能力接口

请求：

```http
GET /model/capabilities?model=gateway%2Fmodel
```

响应：

```json
{
  "model": "gateway/model",
  "input_modalities": ["text", "image", "audio", "video", "file"],
  "output_modalities": ["text", "audio", "image", "video"],
  "streaming": true,
  "reasoning": {
    "supported": true,
    "efforts": ["minimal", "low", "medium", "high"],
    "summary": true,
    "persisted_state": true
  },
  "tools": {
    "function_calling": true,
    "parallel_calls": true,
    "multimodal_results": true
  },
  "structured_output": true,
  "metadata": {"source": "gateway_registry"},
  "extensions": {}
}
```

要求：

- 返回值必须反映所选模型及网关路由流水线的真实能力，不能用统一“全支持”占位；
- 若能力由预处理/专用模型组合实现，应在 `metadata` 或 `extensions` 说明路由来源；
- 能力变化必须可缓存但应有明确刷新策略；
- Adapter 会严格解析该对象，无效或未知顶层字段会导致 `gateway_protocol_error`。

## 8. Provider State

- `provider_state.data` 必须是不透明密文或厂商不透明 Token，不得返回可执行代码或明文密钥；
- 必须绑定租户、用户、会话、上游 Provider、模型和协议版本；建议把这些值作为 AEAD 附加认证数据；
- 跨租户、跨会话、跨 Provider 或跨模型回放必须拒绝并返回 `PROVIDER_STATE_MISMATCH`；
- 必须支持 `expires_at`；过期返回 `PROVIDER_STATE_EXPIRED`，并说明是否可退化为 reasoning summary；
- 不支持 persisted state 的模型必须在能力接口中声明 `persisted_state=false`；
- 日志只记录状态 ID、哈希或版本，不得记录 `data`；
- 网关不得把某厂商的 State 发送给另一厂商。

## 9. 错误、HTTP 状态和重试

统一错误对象：

```json
{
  "type": "provider_rate_limit",
  "code": "RATE_LIMITED",
  "message": "Provider returned HTTP 429.",
  "retryable": true,
  "retry_after_ms": 3000,
  "provider_status": 429,
  "provider_request_id": "upstream-request-id",
  "details": {}
}
```

建议错误码和 HTTP 映射：

| HTTP | `code` | `retryable` | 场景 |
| --- | --- | --- | --- |
| 400 | `VALIDATION_ERROR` | false | 字段或判别联合无效 |
| 400 | `TOOL_LINKAGE_ERROR` | false | call_id 重复、缺失或 name 不一致 |
| 400 | `INVALID_MEDIA` | false | MIME、文件头、媒体结构无效 |
| 400 | `CAPABILITY_ERROR` | false | 无法支持或降级请求能力 |
| 400/413 | `REQUEST_TOO_LARGE` | false | JSON 或 Asset 超限 |
| 401 | `AUTHENTICATION_ERROR` | false | Token 缺失或无效 |
| 403 | `AUTHORIZATION_ERROR` | false | 跨租户/资源越权 |
| 404 | `RESPONSE_NOT_FOUND` | false | response_id 不存在或不可见 |
| 404 | `ASSET_NOT_FOUND` | false | asset_id 不存在 |
| 409 | `IDEMPOTENCY_CONFLICT` | false | 同 request_id 对应不同请求正文 |
| 409 | `PROVIDER_STATE_MISMATCH` | false | State 绑定不一致 |
| 410 | `PROVIDER_STATE_EXPIRED` | false | State 已过期 |
| 408/504 | `PROVIDER_TIMEOUT` | true | 上游超时 |
| 429 | `RATE_LIMITED` | true | 限流，必须尽量给出 retry_after_ms |
| 500 | `INTERNAL_ERROR` | 视情况 | 网关内部错误 |
| 502 | `PROVIDER_BAD_RESPONSE` | true | 上游响应不可解析 |
| 503 | `PROVIDER_UNAVAILABLE` | true | 上游不可用 |

在已创建 Response 后失败，优先返回 `KemoResponse(status=failed,error=...)`；在创建前即失败，可返回：

```json
{
  "protocol_version": "1.0",
  "request_id": "req_1",
  "error": {"type": "validation", "code": "VALIDATION_ERROR", "message": "...", "retryable": false}
}
```

不得把上游完整错误体、密钥、内部堆栈或用户敏感数据写入 `message`。诊断细节放入脱敏后的 `details`。

## 10. 查询、取消与生命周期

- 创建成功后立即分配稳定 `response_id`，流式首事件必须是 `response.created`；
- 查询运行中的响应统一返回 HTTP 202 和 `KemoResponse(status=incomplete)` 快照，并设置 `incomplete_details.reason=running`；该查询快照不代表 SSE 终态，只有实际发送 `response.incomplete` 才表示响应终止；
- 取消必须幂等；已取消再次取消仍返回同一 `cancelled` 响应；
- 已完成响应的取消不得把它改写为 cancelled，应返回原终态或 `409 RESPONSE_ALREADY_TERMINAL`；
- 网关应保存响应状态和 SSE 重放缓存，保存期至少覆盖 kemo-agent 的最大请求超时与常见断线重连窗口；
- 仅在没有输出且错误可重试时自动重试最安全；已有部分输出时必须使用同一响应恢复，避免重复文本和重复工具调用；
- `parent_request_id` 用于工具循环关联，不等同于厂商 `previous_response_id`；是否复用厂商响应由网关基于 Provider State 决定。

## 11. 安全与资源限制

P0 要求：

- Asset 必须经过租户和用户授权；仅知道 `asset_id` 不能取得资源；
- 校验 MIME、魔数、大小、时长、分辨率、解压比和压缩炸弹；
- 外部 URL 仅允许 HTTPS，实施 DNS/IP 双重 SSRF 防护、受控重定向和下载上限；
- 禁止访问 loopback、链路本地、私网、云元数据和网关管理网段；
- Base64 与 Data URL 默认关闭或只允许受控小对象；不得在日志中记录正文；
- 所有签名 URL 必须短时有效，并在日志中移除查询参数；
- 媒体 OCR、ASR 和文件文本均是不可信用户数据，不能提升为 system/developer 指令；
- 工具参数必须完整解析并通过 JSON Schema 后才交给 kemo-agent；
- `system_prompt`、Provider State、工具结果和 `provider_raw` 分级脱敏；
- 每个请求设置总超时、上游阶段超时、并发上限、内存上限和取消传播。

建议初始默认上限（允许按部署调小，并应在配置或能力元数据中公开）：

| 对象 | 建议默认上限 |
| --- | --- |
| 请求 JSON（不含 Asset 字节） | 2 MiB |
| 单个 inline/data URL | 1 MiB，生产默认关闭 |
| 单张图片 | 20 MiB、40 MP |
| 单音频 | 100 MiB、30 分钟 |
| 单视频 | 1 GiB、2 小时 |
| 单普通文件 | 100 MiB |
| SSE 单事件 data | 1 MiB；大型媒体必须转 Asset |
| `provider_raw` | 64 KiB 且必须脱敏 |

超过限制统一返回 `REQUEST_TOO_LARGE` 或 `INVALID_MEDIA`，不得静默截断后继续推理。

## 12. 最小联调示例

### 12.1 非流式文本

请求：

```json
{
  "protocol_version": "1.0",
  "request_id": "req_demo_1",
  "attempt": 1,
  "model": "gateway/default",
  "stream": false,
  "system_prompt": "You are a helpful assistant.",
  "generation": {"parallel_tool_calls": true},
  "output": {"modalities": ["text"]},
  "tools": [],
  "input": [
    {
      "id": "msg_demo_user",
      "type": "message",
      "role": "user",
      "status": "completed",
      "content": [{"type": "text", "text": "你好"}],
      "metadata": {},
      "extensions": {}
    }
  ],
  "provider_options": {},
  "metadata": {"user": "alice", "session_id": "s1", "iteration": 1},
  "extensions": {}
}
```

响应：

```json
{
  "protocol_version": "1.0",
  "id": "resp_demo_1",
  "request_id": "req_demo_1",
  "object": "kemo.response",
  "status": "completed",
  "model": "gateway/default",
  "output": [
    {
      "id": "msg_demo_answer",
      "type": "message",
      "role": "assistant",
      "phase": "final_answer",
      "status": "completed",
      "content": [{"type": "text", "text": "你好！"}],
      "metadata": {},
      "extensions": {}
    }
  ],
  "usage": {
    "input_tokens": 12,
    "output_tokens": 4,
    "total_tokens": 16,
    "measurement": {"mode": "gateway", "exact": true},
    "media": {},
    "stages": [],
    "provider_raw": {}
  },
  "metadata": {},
  "extensions": {}
}
```

### 12.2 工具 `requires_action`

```json
{
  "protocol_version": "1.0",
  "id": "resp_tool_1",
  "request_id": "req_tool_1",
  "object": "kemo.response",
  "status": "requires_action",
  "model": "gateway/default",
  "output": [
    {
      "id": "call_item_demo",
      "type": "tool_call",
      "call_id": "call_demo",
      "name": "lookup",
      "arguments": {"query": "E13"},
      "status": "completed",
      "metadata": {},
      "extensions": {}
    }
  ],
  "usage": {
    "measurement": {"mode": "gateway", "exact": true},
    "media": {},
    "stages": [],
    "provider_raw": {}
  },
  "metadata": {},
  "extensions": {}
}
```

kemo-agent 执行工具后，会用新的 `request_id` 发起下一次请求，设置 `parent_request_id=req_tool_1`，并在 `input[]` 中按顺序包含原 `tool_call` 和匹配 `tool_result`。

## 13. 联调验收清单

### P0：上线阻塞项

- [ ] 四个 HTTP 接口均按第 2 节实现并启用认证、租户隔离；
- [ ] 协议 `1.0` 请求和响应可严格 JSON 往返，不返回未知顶层字段；
- [ ] `request_id` 幂等，相同 ID 不会重复创建响应或重复计费；
- [ ] 纯文本非流式可返回 `final_answer` 和精确/明确标注的 Usage；
- [ ] SSE 从 sequence=0 严格递增，event_id 稳定唯一，终态携带完整 Response；
- [ ] SSE 断线后可按 Last-Event-ID 或 resume_from_sequence 恢复；
- [ ] 查询和取消可用，取消幂等，终态后无额外事件；
- [ ] `requires_action` 包含完整工具参数，call_id 在下一轮严格匹配；
- [ ] Provider 429、503、超时、无效响应能映射为统一错误；
- [ ] Usage 区分 provider/gateway/estimated/mixed/unknown，不用 0 冒充缺失值；
- [ ] 能力接口返回真实模型能力；不支持的模态明确降级或报错；
- [ ] Provider State 加密、绑定、过期、跨模型拒绝均通过测试；
- [ ] Asset 授权、MIME/魔数、SSRF、大小限制和日志脱敏通过安全测试。

### P1：完整多模态项

- [ ] 多图片与文本在同一 message.content[] 中正确关联；
- [ ] 音频不原生支持时产生 ASR StageUsage；
- [ ] 视频不原生支持时产生抽帧、音频转录、时间轴等 StageUsage；
- [ ] 多模态输出通过 Asset 引用返回，不传网关本地路径；
- [ ] 并行与连续多批工具调用的 Item 顺序和 call_id 不混乱；
- [ ] reasoning summary / provider_state 可在工具循环中原样回放；
- [ ] provider_raw 默认关闭，调试模式也有脱敏、大小与保留期限制；
- [ ] 压测中 SSE 有背压和连接上限，不因慢客户端无限占用内存。

## 14. kemo-agent 当前实现对应关系

| 能力 | kemo-agent 位置 |
| --- | --- |
| Request / Response / Item / Usage | `provider/protocol/models.py` |
| JSON 严格解析 | `provider/protocol/serialization.py` |
| SSE 信封、解析和顺序校验 | `provider/protocol/streaming.py` |
| 工具关联校验 | `provider/protocol/validation.py` |
| 网关 HTTP Adapter | `provider/adapters/gateway.py` |
| 旧 Chat 协议兼容 | `provider/adapters/compat.py` |
| Run 协议编译、事件映射与旧 Provider 回退 | `run/engine.py` |
| Item v2 双写和 v1 历史迁移 | `run/history.py`、`run/context.py` |
| Web `content[]` / asset_id 边界 | `web/app.py`、`web/service.py` |

联调时以本文件和上述 Pydantic 模型为准。若网关需要新增字段，优先放入 `metadata` 或 `extensions`；任何核心字段改义都必须升级协议主版本。
