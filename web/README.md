# kemo-agent Web

同一仓库中的 Web 模块：

```text
web/
├── app.py              # FastAPI 应用与 /api 路由
├── service.py          # Run/历史/用户服务适配
├── API_CONTRACT.md     # 当前后端契约
└── frontend/           # React + TypeScript + Vite
```

静态设计原型仅作为视觉参考，正式前端位于 `web/frontend/`。

## 技术栈

- React 19 + TypeScript + Vite
- React Router
- TanStack Query
- Zustand（主题、字号、侧栏等本地 UI 偏好）
- Zod（RunEvent 校验）
- fetch + ReadableStream（POST SSE）
- react-markdown + remark-gfm
- Vitest + Testing Library + MSW

## 安装

Windows 当前环境直接调用 `npm.cmd`：

```powershell
cd E:\code\kemo-agent\web\frontend
npm.cmd install --registry=https://registry.npmmirror.com --no-audit --no-fund
```

## 开发启动

终端一：

```powershell
cd E:\code\kemo-agent
python start_web.py --no-host
```

终端二：

```powershell
cd E:\code\kemo-agent\web\frontend
npm.cmd run dev
```

访问：`http://127.0.0.1:5173`。

Vite 默认将 `/api` 代理到 `http://127.0.0.1:1357`，与 `start_web.py` 默认端口一致。需要联调其他端口时设置 `VITE_DEV_API_TARGET`。

省略 `--no-host` 会同时启动 RuntimeHost（Cron + 消息路由），适合完整运行；纯 UI/API 开发建议保留该参数，避免重复后台宿主。

## 构建与测试

```powershell
cd E:\code\kemo-agent\web\frontend
npm.cmd test
npm.cmd run build
```

构建产物位于 `web/frontend/dist/`，不提交 Git。

## 环境变量

可在构建或部署环境配置：

```text
VITE_API_BASE_URL=
VITE_DEV_API_TARGET=http://127.0.0.1:1357
```

- 留空：同源访问 `/api`，适合反向代理统一域名。
- 指定 URL：前端直接访问该 Web API 基址；需要后端和部署层另外处理 CORS。
- `VITE_DEV_API_TARGET`：仅控制 Vite 开发代理目标，不进入生产 API 调用逻辑。

不要把 Provider API Key 或其他凭据放进 `VITE_*`，因为它们会进入浏览器构建产物。

## 已接接口

- `GET /api/health`
- `GET /api/users`
- `GET /api/users/{user}/sessions`
- `GET /api/users/{user}/sessions/{session_id}/history`
- `GET /api/users/{user}/overview`
- `GET /api/users/{user}/tasks`
- `GET /api/users/{user}/knowledge`
- `GET /api/users/{user}/skills`
- `GET /api/users/{user}/sense`
- `GET /api/users/{user}/settings`
- `POST /api/chat`（POST SSE）

聊天页支持：

- 用户与 Web 会话切换
- 文本历史恢复
- 新会话 ID
- `text_delta` 正文
- `reasoning_delta` 思考
- `tool_call_start` / `tool_call_result` 工具卡片
- `usage`、`error`、`done`
- AbortController 停止读取

注意：当前历史接口只恢复用户/助手文本；刷新后不会恢复过去的思考和工具卡片。

## 分层观察模块

以下页面已经按 V16 分层 UI 拆分为独立 React 页面，并读取当前用户的真实只读运行态：

- 任务计划与 Cron：读取磁盘权威摘要，不返回步骤结果或 Cron prompt
- 知识库：读取用户层与全局层文件索引元数据，不返回正文
- 技能：读取工具注册表，不加载或执行入口
- 全局感知：只展示实际注册来源；当前没有注册表时显示真实空态
- 配置概览：展示脱敏 Provider、功能开关和运行限制

这些模块目前不提供 Web 写操作。当前也未实现：鉴权、OneBot/Telegram、kemo-graph 连接管理、多模态上传、会话删除/手动压缩 API。

## 设计迁移

详见 `frontend/PROTOTYPE_MIGRATION.md`。基础设计系统与分层页面样式位于 `src/styles/prototype.css`，React 专用的模型菜单、命令面板、活动任务和最近活动样式位于 `src/styles/app.css`。
