---
type: component
project: kemo-agent
domain: web
module: web-API_CONTRACT
layer: L2
scope: project
status: active
summary: web/API_CONTRACT.md — v1 API 契约（含会话管理/历史追踪）
source: "web/web-API_CONTRACT.md"
updated: 2026-07-21
verified: partial
tags: [kemo-agent, web, API, 契约, v1, 会话管理, round_traces]
---
# web/API_CONTRACT.md — v1 API 契约

`E:\code\kemo-agent\web\API_CONTRACT.md`

## 通用约定

- 前缀 `/api`，UTF-8 JSON
- source 固定 `"web"`
- 用户必须来自 `list_users()`
- session_id: 1-128 字符，无控制字符
- 不返回 API Key / 环境变量 / 完整配置 / 异常堆栈

## 错误格式

```json
{"error": {"code": "invalid_request", "message": "...", "status": 400}}
```

`400 invalid_request` / `404 not_found` / `409 conflict` / `500 internal_error`

## 端点

### GET /api/health

```json
{"status": "ok", "service": "kemo-agent-web", "version": 1}
```

### GET /api/users

```json
{"users": [{"name": "kesepain"}]}
```

### GET /api/users/{user}/sessions?source=web

```json
{"user": "kesepain", "source": "web", "sessions": [...]}
```

### GET /api/users/{user}/sessions/{id}/history

不存在返回 404。

### 会话管理端点

| 方法 | 路径 | 说明 |
|------|------|------|
| PATCH | `/api/users/{user}/sessions/{session_id}?source=web` | 重命名会话，body: `{"title": string}` |
| DELETE | `/api/users/{user}/sessions/{session_id}?source=web` | 删除单个会话 |
| DELETE | `/api/users/{user}/sessions?source=web` | 删除所有会话 |

运行中的会话拒绝删除，返回 409 Conflict。

### 历史响应变更

`GET /api/users/{user}/sessions/{id}/history` 响应新增：

- `round_traces` — 按轮次组织的数组，每项含 `round`、`reasoning`、`tools[]`
- 每个 tool 调用含 `call_id`、`name`、`status`、`elapsed_ms`、`arguments_text`、`result_text`、`arguments_truncated`、`result_truncated`

### POST /api/chat

```json
{"user": "kesepain", "session_id": "default", "prompt": "你好"}
```

返回 `text/event-stream`。事件类型：`text_delta / reasoning_delta / tool_call_start / tool_call_result / usage / error / done`。

### 设置响应变更

`limits` 新增 `context_rounds` 字段。

### Overview 响应变更

`context` 段新增 `rounds` 和 `round_limit` 字段。

## 规则

1. SSE event 与 payload type 相同
2. 不拼接正文、不改写工具结果
3. 流未给终态时桥接层补充 error
4. 客户端断开时独立 cancel_event
5. 校验错误返回 JSON；Run 错误通过 SSE error

## 明确不实现

React 前端 / WebSocket / 鉴权 / CORS / 会话管理 API / 任务计划/Cron/记忆接口 / RuntimeHost 托管 / 真实平台适配