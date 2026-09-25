# kemo-agent 框架骨架与全局知识索引

本文是框架目录导航，也是全局知识库的主索引。这里只保留稳定结构和文档入口；具体创建步骤、字段约束与示例请进入对应专题文档。

## 顶层骨架

```text
kemo-agent/
├── run/                    # 对话引擎、历史、Prompt、工具、记忆与运行时宿主
├── provider/               # Provider 协议、chat 兼容桥与 kemo 原生适配
├── web/                    # Web 后端与 React 前端
├── cron/                   # 定时任务调度器与系统维护任务
├── message/                # 平台中立消息路由；out/ 存放热插拔平台模块
├── agents/                 # 内置子智能体与子智能体运行时
├── plugins/                # Provider 可调用的工具插件
├── global_sense/           # 全局感知模块
├── global_expand/          # 全局拓展模块
├── shared_expand/          # 跨用户共享拓展模块
├── shared_skills/          # 跨用户共享技能
├── global_knowledge/       # 全局知识库（本目录）
├── shared_knowledge/       # 共享知识库
├── users/<name>/           # 用户配置、人格、历史、记忆和私有资源
├── template/               # 用户、子智能体、技能、拓展等创建模板
├── update/                 # 更新调度、版本校验、备份、锁、恢复与四板块实现
├── tests/                  # 后端测试
├── config/                 # 全局配置与全局人格
├── runtime/                # 结构化运行日志 SQLite（自动生成，不入 Git）
├── tmp/                    # 智能体中间文件，不作为用户交付目录
├── agents.md               # 智能体自身运行手册
├── .env / .env.example     # 本机环境变量与无密钥示例
├── cli.py / start_web.py   # CLI 与 Web 启动入口
├── user_create.py          # 用户创建入口
├── update.py               # 薄更新入口，调用 update.cli.main
├── version.json            # 总版本与板块版本
└── LICENSE                 # Apache License 2.0 正文
```

## 一次请求经过的主要层级

```text
入口（Web / CLI / 外部消息 / Cron）
  → run/engine.py 稳定公共门面
  → conversation_runtime / run_state / request_input / provider_events 领域运行模块
  → user + source + session_id 会话隔离
  → 全局配置与用户配置合并
  → 人格、子代理、插件、技能、知识索引、记忆、计划、拓展、感知拼接
  → Provider 与工具循环
  → 运行中引导在安全边界追加文本或多模态资产
  → 用户级 SQLite archive 完整归档 + 有界进程内 runtime 上下文工作区
  → 记忆、计划和后台维护管线
```

## 资源层级

| 资源 | 全局/共享层 | 用户层 | 核心区别 |
|------|-------------|--------|----------|
| 知识库 | `global_knowledge/`、`shared_knowledge/` | `users/<name>/knowledge/` | 只自动注入索引，正文按需读取 |
| 技能 | `shared_skills/` | `users/<name>/user_skills/` | 只提供指令，不注册可执行工具 |
| 拓展 | `global_expand/`、`shared_expand/` | `users/<name>/expand/` | 可提供状态注入和外部操控入口 |
| 感知 | `global_sense/` | 无 | 单向采集并注入，不提供操控 |
| 子智能体 | `agents/` | `users/<name>/agents/` | 独立 Prompt、权限与工具循环 |
| 工具插件 | `plugins/` | 无 | 唯一可注册 Provider function call 的目录 |

## 运行中引导的数据链路

网页对话执行期间的引导不是纯文本旁路。`web` 接口接收可选文本和最多 20 个当前用户上传文件路径，转换为带 `guidance_id` 的结构化消息后进入 `run/conversation/` 引导邮箱。该领域在下一个 Provider/工具边界重新验证资产，并把图片、音频、视频和普通文件分别按能力声明处理：Kemo 可通过 Asset API 直接接收已声明支持的模态；Chat 只沿既有视觉链路直传图片；其余资产继续向当前 Run 的 `multimodal` 或 `file` 工具开放。

引导附件与首次用户输入使用同一套用户目录隔离、类型识别、签名验证、大小限制和资产 ID 规则。当前 Run 未能接收时，文本与附件作为一个整体排入下一轮；已生效内容写入轮次的 `guidance_details`，其中只保留 UI 安全元数据，不保存绝对路径或内联数据。旧版纯文本 `guidance` 数据仍可读取。

## 专题文档

| 文件 | 内容 |
|------|------|
| `prompt-authoring-standard.md` | 提示词分层、全局/用户默认人格、操作手册、知识索引、插件提示词规范、技能/拓展/感知定义、按需读取来源、健康快照与数据/指令边界 |
| `module-development.md` | 拓展、感知、技能、子智能体和外部智能体桥接的创建与运行合同；`module/panel.json` 用户配置组件面板、status/config/action、preset、masked 密钥与快速配置 |
| `builtin-expansions.md` | 内置 kemo app 桥接激活意愿、端口/Token/用户绑定面板与异常诊断；Kemo 网关 IP/端口面板和状态拓展；Kemo Graph IP/端口、在线检测及图谱/实体/缓存/日志/维护/项目与文档组织边界 |
| `provider-reliability.md` | Provider 工具调用完整性、网络恢复、运行级连续失败 5 次重试（首轮加 5 次共 6 次尝试）、`retrying` 事件、SSE 续传和取消边界；含 Kemo 1.0 双仓库共享 Fixture 门禁，以及 Chat 兼容传输的宽容聚合、请求净化与独立 2 次传输预算 |
| `knowledge-and-user-data.md` | 三层知识库、索引和用户目录骨架 |
| `storage-and-persistence.md` | 历史、记忆、运行状态、日志、高频写盘规则；archive 权威存储与 runtime 有界进程缓存（单项/全局/单用户三重容量）、跨进程版本校验和尾部重建；Web 历史默认时间倒序、复合游标分页与按上海自然日日期筛选；`history_search` 基于结构化正文、删除栅栏、来源/会话过滤和条数+字符预算分页；结构化运行日志、终端长行 pending 缓冲上限；记忆按独立更新/失效/加权/检索边界拆分，碎片数量以独立事实为准，A 类仅同一稳定子主题可成簇、B 类保持最小事实，手动 add/edit 同样执行 memory_type 与长度硬边界；晋升时超限必拆（显式 `memory_type`、A 类 1000 字异常硬上限、B 类通常 100/绝不超过 150 字、宿主落盘复验、不设总纲、继承时效、权重归零、防震荡，挂 `memory_promotion`）；临时重要记忆生命周期；加权证据轮次 committed_at、Shanghai 原日期、memory_ref 检索绑定、create/reinforce/revise、creation 零分日锁、operation_id 幂等、weighted/daily_locked 可观测性；Cron 历史默认 7 天保留；Web 启动及周期旧空间巡检、有数据入记忆队列、空空间离线清理、Web/App 在线租约、CLI 独立绑定、closed 入队补偿、schema v6、显式新空间链接、事务删除栅栏；执行记录分类、多用户有界读缓存与持久化边界 |
| `long-task-runtime.md` | 会话级长任务的隔离状态机、前台任务计划工具次数上限续跑、跨 Run 边界、HTTP/SSE 与客户端恢复合同 |
| `version-and-update-modules.md` | 当前正式版本、配套网关兼容基线，以及 core/agents/plugins/web 更新边界 |
| `configuration-reference.md` | `.env`、全局配置和用户配置字段与优先级；`cron.history_retention_days` 及全局配置 API |
| `task-automation.md` | 多步骤任务计划；Cron 的 once/daily/weekly/monthly/recurring、多时刻、生效区间、次数上限、失败终态、按任务索引的真实执行历史、历史只读访问；任务计划/定时任务/执行记录三栏独立容器与各自 6 条分页，定时任务近期执行排序、用户执行记录倒序、`cron/task_cron_system/` 系统维护记录隔离、按需脱敏详情、网页管理与隔离边界；聊天开始页不展示全局或已清理会话的孤立活动计划 |
| `external-message-route-creation.md` | 外部消息平台模块合同 |
| `module-template-validation.md` | 六类模块创建后的独立合同验收、报告语义与维护边界 |
| `plugin-development.md` | 插件发现、工具循环、执行规则与 SKILL.md 开发指南；受管理 Shell 后台作业、活动配额、启动宽限与失联 worker 对账 |
| `architecture-overview.md` | 事件驱动架构、模块职责、请求生命周期、并发模型、子代理进度气泡、模块注入预览片段来源、消息跟进队列与本轮引导/下一轮发送、Enter 跟进与 Ctrl+Enter 直接引导快捷键、暂停/停止后的 Run ID 状态收口与发送按钮恢复、文件空间排序及分页、新建此用户标签页、独立会话、离线清理后恢复、Web/App 存活租约、CLI 独立续接、closed 会话禁止复活、离线转记忆与入队失败补偿、会话生命周期兜底扫描 |
| `frontend-conventions.md` | Web 前端样式组织约定与调试经验：CSS Module 与主题变量、变量链断裂（别名宿主未挂载导致声明整条失效）、投影被父容器裁切、滚动条、SPA 壳与哈希构建产物缓存头、改样式后的验证步骤、文案与 DOM 契约、知识库编辑/预览单按钮与 Portal 放大预览 |
| `inline-widgets.md` | Web 智能体正文中的 `kemo-widget` 声明式卡片、图表、交互表格、标签页/折叠、差异、日程、看板、表单、建议追问、有限点击动作与站内媒体；未知名称通用渲染、流式闭合、Zod 校验、历史保存、复制降级、响应式与执行安全边界 |
| `project-introduction.md` | 项目定位、当前稳定版本与网关协议匹配、核心能力、部署和使用入口 |
| `open-source-license.md` | Apache-2.0 使用、分发与声明要求 |

## 维护原则

1. 代码、模板和配置文件是事实来源，文档不得发明未实现能力。
2. 新增、删除或移动全局知识文档后，同步更新本索引。
3. 凭据、个人隐私、Cookie、Token 和本机秘密不得进入全局知识库。
4. `.bak`、缓存、日志和运行产物不列入知识索引。
5. 目录中只有 `data_structure.md`、`index.md`、`索引.md`、`目录.md` 会作为知识索引自动注入；其他正文按需读取。
6. 全局知识库不保存版本发布说明、更新日志、单次审计记录或工作区提交清单；长期有效的功能规则应合并到对应专题文档。

## 模块目录的共同原则

感知、拓展、外部消息、技能、子代理和工具插件的模板都只展示框架可发现的最小合同，不定义模块内部架构。模块目录可以是极小实现，也可以容纳任意层级文件或完整工程；框架只读取清单、主文档和已声明入口，其他内部内容不会自动注册、注入或执行。具体合同与安全边界以对应专题文档为准，不能因为模板没有列出某个内部文件就判定其非法。

上述模块中的子代理、拓展、外部消息、感知、技能和用户包完成创建或实质修改后，应进入 `tests/template_tests/<kind>/` 运行对应的独立合同验收。具体映射、状态解释、沙箱边界和维护方法见 `module-template-validation.md`。

- **上帝模块 / 800 行 / 低耦合 / 高内聚 / 统一入口 / 页面组合根 / 模块尺寸合同**：见 `architecture-overview.md` 的“上帝模块边界与统一入口”，前端细则见 `frontend-conventions.md`。
