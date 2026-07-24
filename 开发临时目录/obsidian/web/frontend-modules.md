---
type: component
project: kemo-agent
domain: web
module: frontend-modules
layer: L2
scope: project
status: active
summary: web/frontend — 13 个页面（AppShell 上下文抽屉重写 + ChatPage 对话操作 + SettingsPage 结构化表单 + RuntimeStatusPage 全面重写）
source: "web/frontend-modules.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, web, frontend, AppShell, 上下文抽屉, ChatPage, SettingsPage, RuntimeStatusPage]
---
# web/frontend — 模块页面 + ModuleUi

## 前端页面（13 个）

| 路由 | 页面 | 功能 |
|------|------|------|
| `/chat` | ChatPage | 流式聊天（welcome mode + 404 修复） |
| `/tasks` | TasksPage | 任务计划 + Cron CRUD + 执行记录 |
| `/knowledge` | KnowledgePage | 三层知识索引 + 内联编辑器 |
| `/memory` | **MemoryPage** | 四层记忆 + 临时重要记忆 CRUD |
| `/agents` | **AgentsPage**（新增） | 子代理列表 + 删除用户代理 |
| `/skills` | SkillsPage（重写） | 技能分类管理（builtin/shared/agent_generated/user_created）+ 文档读写 + 白名单 + ZIP 下载 + 删除 |
| `/sense` | SensePage（重写） | 感知模块 CRUD + 数据刷新 + 白名单控制 + 删除 |
| `/expand` | **ExpandPage**（新增） | 拓展库存 + 数据刷新 + 白名单控制 + 删除（仅用户层） |
| `/files` | FilesPage | 文件空间（上传/移动/建目录） |
| `/messages` | **MessagesPage**（新增） | 外部消息模块（Transport 状态 + 健康检查 + 日志记录 + 删除） |
| `/status` | **RuntimeStatusPage** | Provider/上下文/RuntimeHost 状态 |
| `/runtime` | RuntimeModulesPage | 合并兼容页 |
| `/profile` | ProfilePage | 用户资料 |
| `/settings` | SettingsPage | 配置 + 偏好 |

## 新增页面详情（2026-07-21）

### AgentsPage

- 列出用户子代理的名称、版本、描述、触发条件、工具权限
- 支持删除用户代理（含 tombstone 回滚保护）

### ExpandPage

- 按三层（global/shared/user）展示拓展模块
- 每个模块显示校验状态、输入健康、控制文档、注入文本
- 支持数据刷新（data_update.py）、白名单开关、删除（仅用户层）

### MessagesPage

- 列出绑定的消息 Transport
- 显示连接状态、能力、今日收发消息数
- 消息日志（入站/出站/附件，最多 500 条，含今日计数）
- 健康检查和消息模块删除

### SkillsPage 重写

- 统一 catalog（builtin + shared + agent_generated + user_created）
- 每项标注 editable/toggleable/downloadable
- 支持 SKILL.md 文档读写（agent_generated/user_created 可编辑）
- 白名单开关（builtin/shared）
- ZIP 下载（builtin/shared）
- 删除（agent_generated/user_created）

### SensePage 重写

- 感知模块列表含收集的 Markdown 和注入 Markdown
- 数据刷新（data_update.py）、白名单控制、删除

### TasksPage 更新

- 任务计划和 Cron 任务 CRUD 扩展
- 执行记录展示增强

## API 类型变更（types/api.ts）

新增类型：AgentDeleteResponse / TmpDeleteManyResponse / MessageModuleCheckResponse / MessageModuleDeleteResponse / ExpandRefreshResponse / ExpandToggleResponse / SkillDocumentResponse / SkillToggleResponse / SkillArchiveResponse / SenseRefreshResponse / SenseToggleResponse 等

## 相关笔记

- [[web-总览]]
- [[web-app]]
- [[frontend-client]]