# V16 静态原型迁移盘点

源文件：`users/kesepain/history/file/kemo-agent-ui-v16-layered-view.html`（只读设计参考）
实际位置：`D:\votx-agent\users\kesepain\history\file\kemo-agent-ui-v16-layered-view.html`

## 页面

- 对话 `/chat` ✅ 已接入
- 任务 `/tasks` ⬜ 待接入
- 知识库 `/knowledge` ⬜ 待接入
- 技能 `/skills` ⬜ 待接入
- 全局感知 `/sense` ⬜ 待接入
- 配置概览 `/settings` ⬜ 待接入

## 已还原的 V16 视觉结构

### AppShell (components/AppShell.tsx)

- **侧栏**：brand-mark + brand-copy("Personal Agent Runtime") + sidebar-toggle + nav-section(6项含nav-icon/nav-label/nav-tip) + sidebar-rule + recent-block(最近对话列表) + sidebar-spacer + role-wrap
- **用户切换**：role-button(头像+用户名+users/路径) + role-menu弹出式(mini-avatar/strong/small/space-option-id/check/space-mode-badge) 替代原生select
- **顶部栏**：page-title + agent-line(status-dot绿/红) + role-chip + context-button(上下文窗口控件含进度条) + font-size-wrap(弹出式字号菜单Aa+小/中/大+font-sample) + theme-toggle-btn + 运行状态icon-btn + 命令面板icon-btn
- **运行状态抽屉**：drawer + drawer-head + drawer-section(后端/用户/会话状态 + 能力边界说明)
- **响应式**：mobile-menu在780px以下显示，侧栏变为fixed定位

### ChatPage (pages/ChatPage.tsx)

- **欢迎页**：greeting-card(hero-logo+问候语+role-line含users/路径) + snapshot-card(4项:已接通POST SSE/7类RunEvent/只读历史恢复/独立Web会话) + quick-start(4卡片:检查运行状态/总结架构/查询知识库/规划今日任务，每卡含quick-icon+strong+span)
- **消息区**：message.ai/user + msg-avatar(Bot/UserRound图标) + bubble(ReactMarkdown渲染) + messages.show控制显示
- **输入区**：composer + textarea + composer-row(tool-dock 5按钮disabled待接入 + composer-meta含绿点/用户/会话/usage + composer-more弹出conversation-menu + send-btn带↗箭头)
- **对话操作菜单**：conversation-menu弹出式(创建新对话/保存当前对话/压缩上下文)，每项含conversation-action-icon+strong+span，底部含说明
- **运行/停止状态**：running时send-btn变为stop(红色)，AbortController取消

### RunEventCards (components/RunEventCards.tsx)

- **思考过程**：trace + trace-head(可折叠) + trace-body(pre)
- **工具调用**：tool-call + tool-call-head(icon+name+callId+status+chevron) + tool-call-body(tool-call-grid双面板:输入参数+返回结果)
- **状态**：running(LoaderCircle旋转/amber) / success(CheckCircle2/green) / error(AlertCircle/red)

## 保留的数据链路

- POST SSE 聊天 (streamChat + ReadableStream)
- AbortController 取消流
- 历史恢复 (getHistory + React Query)
- 会话管理 (getSessions + URL参数)
- RunEvent 归约 (reduceRunEvent: text_delta/reasoning_delta/tool_call_start/tool_call_result/usage/error/done)
- Zustand UI 持久化 (theme/fontSize/sidebar)

## 仍需人工审美确认

- V16原型的字号系统(V9 style block)与当前html font-size映射是否一致
- 主题切换时暗色侧栏与V16暗色主题的色值是否完全匹配
- tool-call-grid在窄屏(1180px)下单列显示是否可接受
- quick-card hover动画与V16原型是否一致
- context-button进度条暂为静态(—)需后续接入真实Token计数

## CSS 处理

V16的9个style块已提取到 `src/styles/prototype.css`，React专用覆盖和语义修正在 `src/styles/app.css`。
两者通过V16语义类名(如role-menu/font-size-menu/conversation-menu/tool-dock等)配合工作。
