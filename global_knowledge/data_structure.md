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
├── update/                 # 分板块更新实现
├── tests/                  # 后端测试
├── config/                 # 全局配置与全局人格
├── runtime/                # 结构化运行日志 SQLite（自动生成，不入 Git）
├── tmp/                    # 智能体中间文件，不作为用户交付目录
├── agents.md               # 智能体自身运行手册
├── .env / .env.example     # 本机环境变量与无密钥示例
├── cli.py / start_web.py   # CLI 与 Web 启动入口
├── user_create.py          # 用户创建入口
├── update.py               # 更新入口
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
  → 用户级 SQLite archive 完整归档 + runtime 可裁剪上下文窗口
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

网页对话执行期间的引导不是纯文本旁路。`web` 接口接收可选文本和最多 20 个当前用户上传文件路径，转换为带 `guidance_id` 的结构化消息后进入 `run/guidance.py` 邮箱。`run/guidance_runtime.py` 在下一个 Provider/工具边界重新验证资产，并把图片、音频、视频和普通文件分别按能力声明处理：Kemo 可通过 Asset API 直接接收已声明支持的模态；Chat 只沿既有视觉链路直传图片；其余资产继续向当前 Run 的 `multimodal` 或 `file` 工具开放。

引导附件与首次用户输入使用同一套用户目录隔离、类型识别、签名验证、大小限制和资产 ID 规则。当前 Run 未能接收时，文本与附件作为一个整体排入下一轮；已生效内容写入轮次的 `guidance_details`，其中只保留 UI 安全元数据，不保存绝对路径或内联数据。旧版纯文本 `guidance` 数据仍可读取。

## 专题文档

| 文件 | 内容 |
|------|------|
| `expand-creation.md` | 拓展层级、清单、数据注入与操控入口 |
| `kemo-gateway-status-expand.md` | 内置 Kemo 网关状态拓展的激活、权限、产物与更新边界 |
| `kemo-graph-expand.md` | Kemo Graph 外挂文档站、绝对路径注册表、手动同步与按需查询边界 |
| `kemo-transport-reliability.md` | Kemo 请求幂等、SSE 续传、有限重试、取消、完整性与 HTTPS 边界 |
| `sense-creation.md` | 全局感知模块的创建与刷新规则 |
| `skill-creation.md` | 共享技能和用户技能的结构与作用域 |
| `knowledge-creation.md` | 三层知识库及索引维护规则 |
| `history-storage.md` | 用户级 SQLite 历史表、分页、搜索、备份与旧格式边界 |
| `memory-storage.md` | 用户级 SQLite 记忆表、生命周期事务、每日加权约束、热视图与旧格式边界 |
| `runtime-state-storage.md` | 任务计划、上下文摘要、消息幂等和外部路由状态的数据库分工与迁移 |
| `user-directory-skeleton.md` | 用户文件夹的完整骨架与目录所有权 |
| `version-and-update-modules.md` | core/agents/plugins/web 更新边界 |
| `env-reference.md` | `.env` 参数、优先级与安全要求 |
| `global-config-reference.md` | `config/global_config.json` 全字段 |
| `user-config-reference.md` | `users/<name>/user_config.json` 全字段 |
| `cron-task-creation.md` | 北京时间定时任务的创建与状态机 |
| `task-plan-creation.md` | 多步骤任务计划的创建与执行规则 |
| `subagent-creation.md` | 内置/用户子智能体包与授权规则 |
| `external-message-route-creation.md` | 外部消息平台模块合同 |
| `module-template-validation.md` | 六类模块创建后的独立合同验收、报告语义与维护边界 |
| `logging-storage.md` | Cron 与外部消息结构化日志、迁移和保留规则 |
| `provider-tool-call-safety.md` | Chat/Kemo 工具调用完整性、终态、截断与参数安全边界 |
| `plugin-development.md` | 插件发现、工具循环、执行规则与 SKILL.md 开发指南 |
| `architecture-overview.md` | 事件驱动架构、模块职责、请求生命周期与并发模型 |
| `project-introduction.md` | 项目定位、核心能力、部署与使用入口 |
| `open-source-license.md` | Apache-2.0 使用、分发与声明要求 |

## 维护原则

1. 代码、模板和配置文件是事实来源，文档不得发明未实现能力。
2. 新增、删除或移动全局知识文档后，同步更新本索引。
3. 凭据、个人隐私、Cookie、Token 和本机秘密不得进入全局知识库。
4. `.bak`、缓存、日志和运行产物不列入知识索引。
5. 目录中只有 `data_structure.md`、`index.md`、`索引.md`、`目录.md` 会作为知识索引自动注入；其他正文按需读取。

## 模块目录的共同原则

感知、拓展、外部消息、技能、子代理和工具插件的模板都只展示框架可发现的最小合同，不定义模块内部架构。模块目录可以是极小实现，也可以容纳任意层级文件或完整工程；框架只读取清单、主文档和已声明入口，其他内部内容不会自动注册、注入或执行。具体合同与安全边界以对应专题文档为准，不能因为模板没有列出某个内部文件就判定其非法。

上述模块中的子代理、拓展、外部消息、感知、技能和用户包完成创建或实质修改后，应进入 `tests/template_tests/<kind>/` 运行对应的独立合同验收。具体映射、状态解释、沙箱边界和维护方法见 `module-template-validation.md`。
