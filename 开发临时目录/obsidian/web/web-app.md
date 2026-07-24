---
type: component
project: kemo-agent
domain: web
module: web-app
layer: L2
scope: project
status: active
summary: web/app.py — FastAPI 应用工厂（新增会话压缩和运行状态端点）
source: "web/web-app.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, web, FastAPI, SSE, compress, runtime_status, undo_last_round, session_lease, client_id, history_pagination, summary_retry_count]
---
# web/app.py — FastAPI 应用工厂

`E:\code\kemo-agent\web\app.py`

## 概览

`create_app()` 返回可测试的 FastAPI 应用。认证中间件放行 `/api/health`、`/api/logo` 和 `/api/auth/*`。

## 端点清单

### 基础与认证

GET `/api/health` | GET `/api/auth/status` | POST `/api/auth/login` | POST `/api/auth/logout`

### 用户与会话

GET `/api/users` | GET `/api/users/{user}/sessions` | DELETE sessions | PATCH session | DELETE session

### 历史与聊天

GET `/api/users/{user}/sessions/{id}/history` | POST `/api/chat` | POST `/api/runs/{run_id}/guidance`

### Observer 只读端点

GET overview | GET tasks | GET knowledge | GET skills | GET sense | GET memory/summary | GET settings | GET prompt/sections

### 文件空间

GET/DELETE/POST/PUT 文件 CRUD | GET/DELETE/POST/PUT/GET tmp CRUD | POST tmp/upload | POST tmp/directory | PATCH tmp/move

### 新增 tmp 批量操作（2026-07-21）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tmp/delete-many` | 批量删除 tmp 文件（paths 数组，最多 10000 个） |
| DELETE | `/api/tmp/all` | 删除全部 tmp 文件 |

### 头像

POST `/api/users/{user}/avatar` | GET `/api/users/{user}/avatar`

### 人格编辑

GET/PUT `/api/users/{user}/soul` | GET/PUT `/api/global-soul`

### 品牌与运行模块

GET `/api/users/{user}/agents` | GET `/api/users/{user}/message/status` | GET `/api/logo` | GET `/api/users/{user}/expand`

### 新增 Agent 删除（2026-07-21）

| 方法 | 路径 | 说明 |
|------|------|------|
| DELETE | `/api/users/{user}/agents/{agent}` | 删除用户子代理（含 tombstone 回滚保护） |

### 新增消息模块管理（2026-07-21）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/users/{user}/message/modules/{module_name}/check` | 消息模块健康检查 |
| DELETE | `/api/users/{user}/message/modules/{module_name}` | 删除消息模块（含 Transport 注销） |

### 新增拓展模块管理（2026-07-21）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/users/{user}/expand/{scope}/{module_name}/refresh` | 刷新拓展模块数据 |
| PATCH | `/api/users/{user}/expand/{scope}/{module_name}/enabled` | 启用/禁用拓展白名单 |
| DELETE | `/api/users/{user}/expand/{scope}/{module_name}` | 删除拓展模块（仅用户层） |

### 新增技能管理（2026-07-21）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/users/{user}/skills/{category}/document` | 读取技能 SKILL.md |
| PUT | `/api/users/{user}/skills/{category}/document` | 更新技能 SKILL.md |
| DELETE | `/api/users/{user}/skills/{category}` | 删除技能 |
| PATCH | `/api/users/{user}/skills/{category}/enabled` | 启用/禁用技能白名单 |
| GET | `/api/users/{user}/skills/{category}/download` | 下载技能 ZIP |

### 新增感知模块管理（2026-07-21）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/users/{user}/sense/{module_name}/refresh` | 刷新感知模块数据 |
| PATCH | `/api/users/{user}/sense/{module_name}/enabled` | 启用/禁用感知白名单 |
| DELETE | `/api/users/{user}/sense/{module_name}` | 删除感知模块 |

### 任务计划与 Cron CRUD

POST/PUT/DELETE plans | POST/PUT/DELETE crons

### 新增会话压缩端点（2026-07-21）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/users/{user}/sessions/{session_id}/compress` | 手动触发上下文压缩，调用 engine.compress_context |

### 新增撤销上一轮端点（2026-07-22）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/users/{user}/sessions/{session_id}/undo-last-round` | 撤销最近一轮对话，含 expected_round + prompt 乐观锁校验 |

### 新增记忆提取端点（2026-07-22 追加）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/users/{user}/sessions/{session_id}/extract-memory` | 从最新完整归档轮次提取记忆候选（2026-07-23 改为调用 _extract_memory_backlog 统一游标管线） |

### 新增系统重启端点（2026-07-23）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/system/restart` | 重启智能体 Web 服务。检查无活跃运行后调用 restart.py 辅助进程：`python restart.py --port=PORT --parent-pid=PID`。存在活跃对话时返回 409 Conflict。 |

### 文件上传限制提升（2026-07-23）

`FILE_UPLOAD_MAX_BYTES` 从 25MB 提升到 80MB。

### Chat 端点异步包装（2026-07-22 追加）

`POST /api/chat` 改为先放入工作线程 `asyncio.to_thread` 获取事件流，再 `asyncio.shield` 保护。客户端断开时主动关闭生成器释放 Web 并发槽位。close 也改为 `asyncio.to_thread` 执行。

### ChatBody.plan_id（2026-07-23 新增）

`ChatBody` 新增 `plan_id` 字段，允许从聊天端点触发计划执行。当 `plan_id` 非空时，后端调用 `stream_plan` 而非 `stream_chat`。`model_validator` 放宽校验：`prompt`、`content`、`plan_id` 三者至少一个非空即可。

### 计划动作端点（2026-07-23）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/users/{user}/tasks/plans/{plan_id}/actions/{action}` | 计划状态指令，支持 pause/cancel |

### 文件批量操作端点（2026-07-23）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/users/{user}/files/{scope}/delete-many` | 批量删除文件（file_upload/download 空间） |
| DELETE | `/api/users/{user}/files/{scope}/all` | 删除全部文件 |

TmpDeleteManyBody 重命名为 DeleteManyBody，同时用于 tmp 和文件批量删除。

### 错误响应 headers 支持

`WebServiceError` 新增 `headers: dict[str, str] | None` 属性。异常处理器返回时带上 headers（如 `TooManyChatsError` 的 `Retry-After`）。

### 会话租约端点（2026-07-23 新增）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/users/{user}/sessions/{session_id}/lease` | 续租会话（client_id 在 body 中） |
| POST | `/api/users/{user}/sessions/{session_id}/lease/release` | 释放会话租约 |

`client_id` 为 8–128 位字母/数字/下划线/连字符。所有会话操作端点（active/create/close/delete）新增 `client_id` 查询参数或 body 字段。

### 历史分页查询参数（2026-07-23 新增）

`GET /api/users/{user}/sessions/{session_id}/history` 新增查询参数：
- `limit`（可选，1-100）：返回最近 N 轮对话
- `before`（可选，>=1）：返回此轮次之前的内容

响应新增 `pagination` 字段。

### 持久化会话端点（2026-07-22 第二批追加）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/users/{user}/sessions/active` | 获取或预约用户持久交互会话 |
| POST | `/api/users/{user}/sessions` | 创建新会话 |
| POST | `/api/users/{user}/sessions/{session_id}/close` | 关闭会话 |

### 记忆 CRUD 层级化（2026-07-22）

GET/DELETE `/api/users/{user}/memory/item` 新增 `tier` 查询参数，与 `filename` 组合精确定位。

### 新增运行状态端点（2026-07-21）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/users/{user}/runtime/status` | 返回 RuntimeStatusResponse：上下文、Token 统计、Prompt 预览、组件健康、记忆、任务、系统 Cron、消息路由 |

### 知识正文 CRUD

GET/PUT/PATCH/DELETE knowledge document

### 记忆 CRUD

GET/PUT/DELETE memory item | GET/PUT memory important（2026-07-23 移除 DELETE）

### 配置与偏好

PATCH user config | GET/PATCH global config | GET/PATCH preferences

## skill category 分类

| category | 说明 | 可编辑 | 可切换 | 可下载 |
|----------|------|:---:|:---:|:---:|
| builtin | 基础插件 | 否 | 是 | 是 |
| shared | 共享技能 | 否 | 是 | 是 |
| agent_generated | 智能体生成技能 | 是 | 否 | 否 |
| user_created | 用户自建技能 | 是 | 否 | 否 |

## 相关笔记

- [[web-service]]
- [[web-总览]]
- [[frontend-client]]
- [[frontend-modules]]