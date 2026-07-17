# kemo-agent Web 后端契约（v1）

本阶段只实现可供后续前端接入的 FastAPI 后端，不实现 React/Vite 页面。

## 运行拓扑

```text
浏览器前端（后续提供）
→ FastAPI /api/*
→ WebRunService
→ run.engine.iter_request_events
```

Web 后端不启动 `RuntimeHost`、CronScheduler、OneBot、Telegram 或其他 Transport。长期后台宿主仍由 `start_host.py` 独立运行，避免 Web 开发/重载造成 Cron 重复实例。

## 通用约定

- API 前缀：`/api`
- Web 对话来源固定为 `source = "web"`
- 用户必须来自 `run.users.list_users(root)`，不得由路径参数访问其他目录
- `session_id` 是 1–128 字符非空字符串
- JSON 使用 UTF-8
- 不返回 Provider API Key、环境变量、完整用户配置或内部异常堆栈

## JSON 错误结构

```json
{
  "error": {
    "code": "invalid_request",
    "message": "面向调用方的错误说明",
    "status": 400
  }
}
```

状态映射：

- `400 invalid_request`：JSON/字段/会话参数无效
- `404 not_found`：用户或会话不存在
- `409 conflict`：当前会话状态冲突（本阶段预留）
- `500 internal_error`：未分类服务错误，消息脱敏

## HTTP API

### GET /api/health

不调用 Provider，不启动后台组件。

```json
{
  "status": "ok",
  "service": "kemo-agent-web",
  "version": 1
}
```

### GET /api/users

```json
{"users": [{"name": "kesepain"}]}
```

### GET /api/users/{user}/sessions

查询参数：`source` 可选，默认 `web`；本阶段只允许 `web`。

```json
{
  "user": "kesepain",
  "source": "web",
  "sessions": [
    {"session_id": "default", "window": "...", "rounds": 2, "updated_at": "..."}
  ]
}
```

### GET /api/users/{user}/sessions/{session_id}/history

不存在的会话返回 404，不以空数组伪装存在。

```json
{
  "user": "kesepain",
  "source": "web",
  "session_id": "default",
  "messages": [{"role": "user", "content": "..."}]
}
```

### POST /api/chat

请求：

```json
{
  "user": "kesepain",
  "session_id": "default",
  "prompt": "你好"
}
```

响应：`text/event-stream; charset=utf-8`。

## SSE 事件

每个 RunEvent 无损使用 `RunEvent.to_dict()` JSON 编码：

```text
event: text_delta
data: {"type":"text_delta","content":"你"}

```

事件类型固定复用：

```text
text_delta
reasoning_delta
tool_call_start
tool_call_result
usage
error
done
```

规则：

1. SSE `event` 与 payload `type` 相同。
2. 保留 RunEvent 的 content、tool_call_id、tool_name、arguments、result、usage、error、metadata。
3. Web 桥接层不拼接正文、不改写工具结果、不创建第二套事件协议。
4. Run 流未给终态时，桥接层补充一个 `error` 终态。
5. 路由/校验错误发生在流建立前时返回 JSON 错误；Run 执行错误发生在流建立后时通过 SSE `error`。
6. 客户端断开时设置该请求独立的 cancel_event；不影响其他会话。

## 本阶段明确不实现

- React/TypeScript/Vite 前端
- WebSocket
- 登录、Token、Cookie 或公网鉴权
- 跨域开放策略
- 会话删除、压缩和取消 API
- 任务计划、Cron、记忆、配置管理 API
- RuntimeHost 进程内托管
- OneBot/NapCat、Telegram
- kemo-graph/RAG
- 多模态上传
- update.py

这些能力在后续前端与部署需求明确后分阶段加入，不在 v1 隐式扩张。
