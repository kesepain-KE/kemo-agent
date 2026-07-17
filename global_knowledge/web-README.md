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
| POST | `/api/chat` | SSE 流式聊天 |

## 前端页面

| 页面 | 数据来源 | 读写 |
|------|---------|------|
| 聊天 | SSE 流式 + REST 历史 | 读/写（chat） |
| 任务计划与 Cron | 磁盘文件摘要 | 只读 |
| 知识库 | 用户层 + 全局层索引 | 只读 |
| 技能 | 工具注册表 | 只读 |
| 全局感知 | 注册来源 | 只读 |
| 配置概览 | 脱敏 Provider/开关 | 只读 |

## 聊天 SSE 事件类型

`text_delta`、`reasoning_delta`、`tool_call_start`、`tool_call_result`、`usage`、`error`、`done`。支持 AbortController 中途停止。

## 当前限制

- 所有观察模块只读，无 Web 写操作
- 历史接口只恢复用户/助手文本，刷新后丢失思考和工具卡片
- 未实现：鉴权、OneBot/Telegram、kemo-graph 连接管理、多模态上传、会话删除/手动压缩 API

## 环境变量

| 变量 | 用途 |
|------|------|
| `VITE_API_BASE_URL` | 生产 API 基址；留空则同源 `/api` |
| `VITE_DEV_API_TARGET` | Vite 开发代理目标，默认 `http://127.0.0.1:1357` |

Provider API Key 等凭据不得放入 `VITE_*`，会进入浏览器构建产物。
