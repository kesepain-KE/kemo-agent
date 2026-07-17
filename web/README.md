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
python start_web.py
```

终端二：

```powershell
cd E:\code\kemo-agent\web\frontend
npm.cmd run dev
```

访问：`http://127.0.0.1:5173`。

Vite 将 `/api` 代理到 `http://127.0.0.1:1478`。

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
```

- 留空：同源访问 `/api`，适合反向代理统一域名。
- 指定 URL：前端直接访问该 Web API 基址；需要后端和部署层另外处理 CORS。

不要把 Provider API Key 或其他凭据放进 `VITE_*`，因为它们会进入浏览器构建产物。

## 已接接口

- `GET /api/health`
- `GET /api/users`
- `GET /api/users/{user}/sessions`
- `GET /api/users/{user}/sessions/{session_id}/history`
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

## 未接模块

以下页面保留 V16 原型的信息架构，但明确标记“待接入”，不会显示演示数据或假成功操作：

- 任务计划与 Cron
- 知识库浏览和索引管理
- 技能注册表
- 全局感知
- 脱敏配置和 Provider 状态

当前也未实现：鉴权、OneBot/Telegram、kemo-graph、多模态上传、会话删除/压缩管理 API。

## 设计迁移

详见 `frontend/PROTOTYPE_MIGRATION.md`。原型9个样式块先汇总在 `src/styles/prototype.css`，React专用修正在 `src/styles/app.css`；后续视觉稳定后再按tokens、shell、components和pages继续拆分。
