---
type: component
project: kemo-agent
domain: web
module: frontend-shell
layer: L2
scope: project
status: active
summary: web/frontend — AppShell / UI Store / Router（含会话管理/模型选择器）
source: "web/frontend-shell.md"
updated: 2026-07-22
verified: partial
tags: [kemo-agent, web, frontend, 布局, 状态, 路由, 毛玻璃, 命令面板, 模型, 会话管理, 侧边栏折叠, session_lease, client_id, RouteErrorPage, cross_tab]
---
# web/frontend — AppShell / UI Store / Router

votx-agent 毛玻璃设计系统版。`E:\code\kemo-agent\web\frontend\src/`

## AppShell 会话管理

AppShell 支持会话重命名和删除功能，通过 renameSession / deleteSession API 调用。侧边栏集成 SessionHistoryPanel 组件用于浏览历史会话，AgentComposer 用于快速创建新会话。

## 设计系统

`prototype.css` 已完全替换为 votx-agent 毛玻璃主题：

| 变量组 | 说明 |
|------|------|
| `--glass-bg/border/hover/shine` | 毛玻璃面板基础 |
| `--text-primary/secondary/tertiary` | 三级文字色 |
| `--accent/accent-dim/accent-glow` | 紫色系强调色（暗 `#8b85ff` / 亮 `#665cff`） |
| `--shadow-panel/card/button` | 三级阴影 |
| `--r-xs/sm/md/lg/full` | 圆角尺度 |
| `--s-1` 到 `--s-6` | 间距尺度 |

暗色/亮色双主题通过 `[data-theme="light"]` 切换，`body::before` 径向渐变背景 + `body::after` 网格遮罩。

## AppShell (`components/AppShell.tsx`)

### 新增组件

| 组件 | 类名 | 说明 |
|------|------|------|
| **模型选择器** | `model-wrap` → `model-btn` + `model-menu` | 显示当前 Provider 类型/模型/base_url，点击弹出详情 + 跳转设置 |
| **命令面板** | `command-layer` → `command-box` | Ctrl+K 打开，搜索 7 条命令（新建对话/任务/知识/技能/感知/运行状态/配置） |
| **导航徽标** | `nav-badge` | 任务数实时徽标（`/tasks`） |

### 增强抽屉

| 区域 | 数据来源 |
|------|------|
| 当前上下文 | `overview.context` — Token 使用/上限/百分比 + 进度条 |
| 核心能力 | `healthQuery` + `overview.counts` — Web 后端/模型/知识/工具/子代理 |
| 最近活动 | `overview.activities` — 最多 4 条（session/plan/cron） |

### Overview 集成

```typescript
ShellOutletContext {
  user, sessionId, setSessionId,
  overview?: OverviewResponse,  // 30s 轮询
  refreshOverview: () => void
}
```

`getOverview(user, sessionId)` — 返回上下文用量 / Provider 信息 / 能力计数 / 最近活动。

### 键盘快捷键

| 按键 | 操作 |
|------|------|
| Ctrl+K | 切换命令面板 |
| Esc | 关闭所有弹窗（角色/字号/模型/抽屉/命令） |

## UI Store

不变：Zustand + localStorage（theme/fontSize/sidebarCollapsed/drawerOpen）

### 侧边栏折叠改进（2026-07-22）

- **品牌区简化**：移除副标题 "Personal Agent Runtime"，仅保留产品名 "kemo-agent"
- **折叠态交互**：折叠时 logo 区域被展开按钮替代（hover 时 logo 淡出，按钮淡入），不再显示导航提示文字
- **NavLink aria-label**：折叠态添加 `title` 和 `aria-label`，保证无障碍可访问
- **CSS**：`.brand-copy strong` 加粗样式（780 weight），折叠态 `.sidebar-head` 绝对定位切换效果，`.nav-scroll` 横向滚动禁用，`.nav-tip` 折叠态隐藏

## Router

6 页面全部已接入真实 API（`PendingModulePage` 已删除）。新增 `RouteErrorPage` 作为路由错误边界（`errorElement`）。

## 会话租约集成（2026-07-23 新增）

### AppShell 变更

- `clientId`：通过 `getPageClientId()` 获取单页唯一标识，作为 `client_id` 参数传入所有会话 API
- 心跳续租：每 15 秒 `touchSessionLease`，页面卸载时 `releaseSessionLease(keepalive=true)`
- `sessionTransitioning` 状态：防止过渡期间 `activeSessionQuery` 抢回旧会话
- `detachSession()`：清除活跃会话绑定，用于保存/清空前的瞬态状态
- `notifySessionDeleted(id)`：向其他标签页广播会话删除事件

### 跨标签页通信

`sessionClient.ts` 使用 `BroadcastChannel`（`kemo-session`）实现跨标签页同步：
- 收到 `session-deleted` 事件（非自身 `clientId`）时：清除历史缓存、刷新会话列表、自动解绑被删除的会话
- 路由错误边界 `RouteErrorPage` 捕获全局异常

### ChatPage 变更

- 所有 API 调用（createSession/closeSession/deleteSession/streamChat）传入 `clientId`
- 保存/清空前调用 `detachSession()，` 失败时回退 `setSessionId`
- `executePlan` 传入 `clientId`
- 修复重新生成的 `historyUserMessages` 基线计算

### 其他

- `client.ts`：`StreamChatOptions` 新增 `clientId`，所有会话函数新增 `clientId` 参数
- `AppShell.test.tsx`：新增交叉标签页并发测试（保存期间切换页面、其他页面在用对话时清空 409）