# V16 静态原型迁移盘点

源文件：`users/kesepain/history/file/kemo-agent-ui-v16-layered-view.html`（只读设计参考）

## 页面

- 对话 `/chat`
- 任务 `/tasks`
- 知识库 `/knowledge`
- 技能 `/skills`
- 全局感知 `/sense`
- 配置概览 `/settings`

## 可复用视觉

- `:root` 与 `[data-theme="dark"]` 颜色、表面、边框、阴影语义
- 两栏 AppShell、可折叠侧栏、顶部栏、内容工作区
- 消息、执行过程、工具调用卡片、编辑器
- 任务/知识/技能/感知/设置的 module shell、metric、panel、table、filter
- 运行状态 drawer、用户菜单、主题和字号控件
- 24 组媒体查询与 reduced-motion 规则
- 内联 SVG 改由 lucide-react 或保留必要 SVG；Logo 使用项目 `kemo-agent.jpg`

## 必须替换

- `setTimeout` 模拟回复 → POST SSE
- DOM 查询与 onclick → React state / handlers
- localStorage 对话历史 → 后端四文件历史
- 硬编码用户、会话、数量、模型、Token、任务和插件状态 → API 或明确“待接入”
- 假压缩、假索引进度、假 kemo-graph 状态 → 禁用

## 组件边界

- `AppShell`, `Sidebar`, `Topbar`, `RuntimeDrawer`
- `UserSwitcher`, `SessionList`, `StatusView`
- `ChatPage`, `MessageList`, `ReasoningTrace`, `ToolCallCard`, `Composer`
- `TasksPage`, `KnowledgePage`, `SkillsPage`, `SensePage`, `SettingsPage`

## CSS 处理

9 个 `<style>` 已原样提取到 `src/styles/prototype.css`，先确保视觉兼容；React 专用的覆盖和语义修正放入 `src/styles/app.css`。原型 CSS 不作为长期唯一维护入口，稳定后再按 tokens/shell/components/pages 分拆。
