---
type: component
project: kemo-agent
domain: web
module: frontend-chat
layer: L2
scope: project
status: active
summary: web/frontend — ChatPage + RunEventCards（V16 视觉还原版）
source: "web/frontend-chat.md"
updated: 2026-07-22
verified: partial
tags: [kemo-agent, web, frontend, 聊天, SSE, V16, 会话管理, 工具追踪, MarkdownMessage, undo_last_round]
---
# web/frontend — ChatPage + RunEventCards

V16 视觉还原版。`E:\code\kemo-agent\web\frontend\src/pages/ChatPage.tsx`

## 会话管理

- 会话重命名：PATCH `/api/users/{user}/sessions/{id}`，向当前 session 的所有窗口写入标题
- 会话删除：DELETE `/api/users/{user}/sessions/{id}`，删除已确认的所有窗口
- 全部删除：DELETE `/api/users/{user}/sessions`，清空 user/source 下所有会话
- 运行中的会话拒绝删除

## 工具调用追踪

`history()` 响应新增 `round_traces` 字段，按轮次组织的 reasoning 与工具调用全过程。

每个 trace 包含：
- `round`：轮次号
- `reasoning`：该轮思考过程
- `tools`：工具调用列表（含 call_id、name、status、elapsed_ms、arguments_text、result_text 及截断标记）

## round_traces 渲染

ChatPage 将 round_traces 渲染为可折叠的工具调用卡片，支持 streaming 状态动画、参数/结果预览和复制。

## 三层：欢迎 → 消息 → 输入

### 欢迎页（无消息时）

```text
welcome-top
├── greeting-card
│   ├── hero-logo (kemo-agent.jpg)
│   ├── greeting-copy: h1 + p + role-line (users/{user})
└── snapshot-card (4 项指标: POST SSE / 7类RunEvent / 只读历史 / 独立会话)

quick-start (4 张 quick-card)
├── 检查运行状态 (Zap)
├── 总结架构 (FileText)
├── 查询知识库 (BookOpen)
└── 规划今日任务 (CheckCircle2)
```

点击 `quick-card` → `setDraft(prompt)` 填入输入框。

### 消息区 (`messages`)

| 类型 | 组件 | 说明 |
|------|------|------|
| user/assistant text | `message.user` / `message.ai` | msg-avatar + bubble + **MarkdownMessage** 组件（2026-07-22 替换 ReactMarkdown） |
| reasoning | `ReasoningTrace` | 可折叠思考过程 |
| tool | `ToolCallCard` | 调用名/参数/结果 |
| error | `chat-error` | 红色错误提示 |

`reduceRunEvent()` — 纯函数，累积 `ChatItem[]`：text_delta 追加、reasoning 折叠、tool 创建/更新、done 标完成。

### 输入区 (`composer-zone`)

```text
composer
├── textarea (Enter 发送, Shift+Enter 换行)
└── composer-row
    ├── tool-dock (5 个 disabled 工具按钮)
    ├── composer-meta (用户 / 会话 / usage)
    └── composer-submit
        ├── composer-more-btn → conversation-menu (弹出)
        └── send-btn (Send ↗) / stop-btn (Square)
```

### 对话操作弹出 (`conversation-menu`)

- 创建新对话（`Plus`）→ `createSessionId()`
- 保存当前对话（`FileText`）→ 占位
- 压缩上下文（`Zap`）→ 占位
- `conversation-menu-foot`：阈值说明

### MarkdownMessage 组件（2026-07-22 新增）

`components/Chat/MarkdownMessage.tsx` — 增强版 Markdown 渲染器，替换原 ChatPage 内联的 ReactMarkdown：

- **流式模式** (`streaming=true`)：仅启用 remark-gfm + remark-math，跳过 sanitize/highlight/katex，保证低延迟逐字渲染
- **完成模式** (`streaming=false`)：全功能渲染 — rehype-sanitize（自定义 Schema 允许 code/span/div 类名）、rehype-katex（数学公式）、rehype-highlight（语法高亮）、remark-breaks（换行）、remark-emoji（短码 Emoji）
- **Mermaid 图表**：检测 `language-mermaid` 代码块，动态 import mermaid 并渲染 SVG
- **安全 URL**：`safeUrlTransform` 过滤非 HTTP/mailto/相对路径的协议
- **表格包装**：外层 div 滚动容器
- 配套文件：`MarkdownMessage.module.css`（样式隔离）、`MarkdownMessage.test.tsx`（单元测试）

### 重新发送消息（撤销+重放，2026-07-22 重构）

`regenerateLastResponse` 不再直接重发，改为：

1. 计算 `expectedRound = persistedRounds + liveRounds`
2. 调用 `undoLastRound(user, session, expectedRound, prompt)` 撤销上一轮
3. 清除 live items 中的最后一轮用户消息（`dropLastLiveRound`）
4. 刷新历史查询（invalidateQueries）
5. 用 `send(prompt, { sessionId, content: undo.content })` 重放原消息

效果：不增加对话轮数，旧回复被新回复替代。

### 工具暂停 UI 移除（2026-07-22）

`max_per_round` 已移除，所有 `toolPause` / `visibleToolPause` / `persistedToolPause` 状态和 UI 横幅（"本轮已执行 N/M 次"）全部删除。Composer placeholder 不再有暂停提示。

## 关键变更（2026-07-22 追加）

### 对话块分组（groupConversationItems）

新增 `groupConversationItems()` 纯函数，将 ChatItem 列表按用户消息为界分组为 `ConversationBlock[]`：

- `{ kind: 'user', item }` — 用户消息
- `{ kind: 'assistant', items: ChatItem[] }` — 智能体回复块（含思考、工具、正文、统计）

渲染改为遍历 blocks，每个 assistant 块用 `<article className="assistant-turn">` 包裹（avatar + content 两栏布局），保持同轮的思考/工具/正文/统计稳定在同一边框内。

### 跟随最新回复按钮（follow-output）

当用户滚动离开底部时，显示 `chat-follow-output` 按钮（sticky 定位），点击平滑滚回底部并恢复跟随。

### 保存对话前提取记忆

`saveAndNewConversation()` 中，在刷新 sessions 之前调用 `extractSessionMemory(user, sessionId)`，从当前会话最后一轮提取记忆候选。

### ReasoningTrace 自动滚动

`RunEventCards.tsx` 的 `ReasoningTrace` 组件新增 `bodyRef` 和 `followBodyRef`，打开且 streaming 时自动滚到底部。用户手动上滚后停止跟随。

### 前端 API 新增

- `extractSessionMemory(user, sessionId)` — POST extract-memory 端点
- `SessionMemoryExtractionResponse` 类型

## 保留的真实链路

- `streamChat()` POST SSE
- `AbortController` 停止
- `getHistory()` 历史恢复
- React Router `?session=` URL 状态
- TanStack Query 自动刷新