# web — Web 模块

kemo-agent 的 Web 服务端与前端，同仓库内 `web/` 目录。

## 结构

```
web/
├── app.py           # FastAPI 应用工厂，挂载 /api 路由
├── service.py       # WebRunService：封装 Run 核心、历史、用户服务
└── frontend/        # React 19 + TypeScript + Vite
    ├── dist/        # 构建产物（不提交 Git）
    └── src/         # 组件、状态、样式
```

## 技术栈

| 层 | 组件 |
|----|------|
| 运行时 | Python FastAPI + Uvicorn |
| 前端框架 | React 19 + TypeScript + Vite |
| 路由 | React Router |
| 数据请求 | TanStack Query |
| 本地状态 | Zustand（主题、字号、侧栏） |
| 流式事件校验 | Zod（RunEvent） |
| SSE 读取 | fetch + ReadableStream |
| Markdown 渲染 | react-markdown + remark-gfm |
| 测试 | Vitest + Testing Library + MSW |

## 端口与入口

- Web 后端默认端口：**1357**（轮询 1357–1366，首个可用端口）
- 前端开发服务器：`localhost:5173`，`/api` 代理到后端
- 启动命令：`python start_web.py`（含 RuntimeHost），开发用 `--no-host` 关闭 Cron/消息路由

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/users` | 用户列表 |
| GET | `/api/users/{user}/sessions` | 会话列表 |
| GET | `/api/users/{user}/sessions/{id}/history` | 会话历史 |
| GET | `/api/users/{user}/overview` | 用户概览 |
| GET | `/api/users/{user}/tasks` | 定时任务 |
| GET | `/api/users/{user}/knowledge` | 知识库索引 |
| GET | `/api/users/{user}/skills` | 技能清单 |
| GET | `/api/users/{user}/sense` | 感知状态 |
| GET | `/api/users/{user}/settings` | 配置摘要 |
| GET | `/api/users/{user}/config/full` | 脱敏的用户配置编辑快照与 ETag |
| PUT | `/api/users/{user}/config` | 认证且显式开启后的原子用户配置写入 |
| GET | `/api/users/{user}/prompt/sections` | 固定 14 段 Prompt 与 Expand 诊断（无正文） |
| GET | `/api/users/{user}/memory/summary` | 记忆挡位、权重、时间与内容预览 |
| POST | `/api/runs/{run_id}/guidance` | 向活动运行的安全边界提交引导 |
| POST | `/api/chat` | SSE 流式聊天 |

## 前端页面

| 页面 | 数据来源 | 读写 |
|------|---------|------|
| 聊天 | SSE 流式 + REST 历史 | 读/写（chat） |
| 任务计划与 Cron | 磁盘文件摘要 | 只读 |
| 知识库 | 用户层 + 全局层索引 | 只读 |
| 技能 | 插件工具 + 共享/用户 Prompt 技能 | 只读 |
| 全局感知 | 公共注册库存 + 用户模块过滤 | 只读 |
| 配置概览 | 脱敏 Provider/开关、Prompt、Expand、记忆、用户配置 | 默认只读；满足写入双闸门后可保存用户配置 |

## 聊天 SSE 事件类型

`text_delta`、`reasoning_delta`、`tool_call_start`、`tool_call_result`、`usage`、`error`、`done`。支持 AbortController 中途停止。

## 当前限制

- 运行中 guidance 只能在 Provider 或工具调用结束后的安全边界生效，不能中断阻塞中的外部调用。
- 历史页会恢复用户/助手消息和逐轮统计；思考与完整工具卡片目前仍只在实时流中展示。
- RuntimeHost 只有通过 `start_web.py` 一体启动时能展示真实组件状态；独立 Web 模式显示 `unmanaged`。
- kemo-graph 仍只有 `disabled` / `not_connected` 占位，不启动或调用外部项目。
- 尚未实现多模态上传、会话删除和手动压缩 Web API。

## 环境变量

| 变量 | 用途 |
|------|------|
| `VITE_API_BASE_URL` | 生产 API 基址；留空则同源 `/api` |
| `VITE_DEV_API_TARGET` | Vite 开发代理目标，默认 `http://127.0.0.1:1357` |
| `WEB_ALLOW_CONFIG_WRITE` | 是否允许已认证 Web 会话写 `user_config.json`，默认 false |

Provider API Key 等凭据不得放入 `VITE_*`，会进入浏览器构建产物。
