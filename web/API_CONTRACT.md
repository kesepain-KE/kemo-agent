# kemo-agent Web 后端契约（v2）

FastAPI 为 React/Vite 前端提供聊天链路与只读 Observer 接口。写操作仍只开放 `POST /api/chat`。

## 运行拓扑

```text
浏览器前端（后续提供）
→ FastAPI /api/*
→ WebRunService
→ run.engine.iter_request_events
```

`create_app()` 本身不启动 `RuntimeHost`、CronScheduler 或消息 Transport。`start_web.py` 默认提供一站式运行并在 Web API 之外启动 RuntimeHost；使用 `--no-host` 可保持纯 Web API 模式，避免开发时重复后台宿主。

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
  "version": 2
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

### GET /api/users/{user}/overview

查询参数：`session_id` 可选。聚合当前用户的真实上下文占用、脱敏 Provider 元数据、会话/知识/工具/子代理/活动任务计数、当前活动计划和最近活动。

### GET /api/users/{user}/tasks

只读返回 `PlanStore` 与 `CronStore` 的安全摘要。不会返回计划步骤结果、工具参数、Cron prompt 或错误详情。

### GET /api/users/{user}/knowledge

只读返回文件索引元数据和检索配置摘要。不会通过列表接口返回知识正文或绝对路径。

### GET /api/users/{user}/skills

只读返回工具注册表的名称、描述、版本、启用状态、来源层级和覆盖数量；不会加载或执行工具入口。

### GET /api/users/{user}/sense

只读返回实际注册的感知来源与注入开关。`global_sense` 说明目录不等同于已注册来源；不存在注册表时明确返回真实空态。

### GET /api/users/{user}/settings

返回脱敏配置镜像：Provider 类型/端点/模型、凭据来源状态、功能开关和运行限制。禁止返回 API Key、环境变量值、完整配置对象或内部绝对路径。

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

## 当前明确不实现

- WebSocket
- 登录、Token、Cookie 或公网鉴权
- 跨域开放策略
- 会话删除、压缩和取消 API
- 任务计划、Cron、技能、感知和系统配置的 Web 写操作
- 通过 Web API 管理 RuntimeHost 生命周期
- OneBot/NapCat、Telegram
- kemo-graph/RAG Web 连接管理
- 多模态上传
- update.py

这些能力在部署需求和写入权限模型明确后分阶段加入，不在 v2 隐式扩张。
