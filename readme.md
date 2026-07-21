# kemo-agent

<p align="center">
  <img src="kemo-agent.jpg" alt="kemo-agent logo" width="200">
</p>

事件驱动的多用户智能体框架。

[![版本](https://img.shields.io/badge/version-0.1.0--dev-blue)](https://github.com/kesepain-KE/kemo-agent)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![许可证](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

## 目录

- [背景](#背景)
- [安装](#安装)
- [用法](#用法)
- [架构概览](#架构概览)
- [相关项目](#相关项目)
- [主要项目负责人](#主要项目负责人)
- [参与贡献方式](#参与贡献方式)
- [开源协议](#开源协议)

## 背景

kemo-agent 是一个事件驱动的多用户智能体框架，当前版本 0.1.0-dev。

框架围绕一次完整的智能体请求链路设计：从配置加载、Prompt 编排、上下文选取、Provider 调用、工具循环到历史提交与记忆更新。各模块独立演进，通过 17 段固定顺序的 PromptBundle 管线组装。

支持四种输入源：Web（SSE 流式）、CLI（交互式命令行）、外部消息路由（OneBot/Telegram/文件夹插件）、定时任务（Cron）。

### 主要模块

- **对话引擎** — 事件驱动模型↔工具循环，会话锁串行隔离，工具签名去重，运行中引导注入，成功才提交历史，失败/取消不污染数据
- **Prompt 编排** — 17 段固定顺序 PromptBundle，每段独立字符上限，顺序偏离即抛异常
- **记忆系统（v2）** — 四档文件型存储：seven_days → one_month → half_year → permanent。独立 `.md` 正文 + `data.json` 轻量索引，引用/修改触发加权，到期自动晋升或删除，无每日衰减，有 v1→v2 迁移工具
- **知识索引** — 三层双层架构（用户级 > 共享级 > 全局级），索引全量化 + 确定性注入，支持 kemo-graph 外部知识图谱替换（六子层开关，当前占位）
- **工具系统** — 12 个内置插件，`SKILL.md` 的 `## Tool` JSON 块为唯一工具声明来源，`discover_plugin_manifests()` 自动发现
- **上下文管理** — 双层历史（归档层完整无界 + temp 工作区有界裁剪），整轮不可拆分，Token + 轮次双预算，多轮摘要循环至预算达标
- **子代理** — 5 个内置子代理，精简清单模式（`agent.json` 四字段 + `trigger.md`），用户可通过热插拔数据包扩展。独立授权、超时、取消信号
- **配置系统** — `config/global_config.json` + `users/<user>/user_config.json` 深合并，`.env` 仅做密钥兜底。资源准入策略（SourcePolicy）白名单控制
- **后台维护** — 独立于 Cron 的 MaintenanceScheduler，定期执行重要记忆审阅、每日记忆到期审核、活跃会话上下文压缩
- **资源分层** — 用户级 > 共享级 > 全局级，覆盖知识库、技能、拓展与感知。感知/拓展已标准化为 `sense.json` / `expand.json` JSON 元数据驱动
- **Web 界面** — FastAPI + React/TypeScript/Vite，13 个页面：流式聊天、任务计划、Cron 管理、知识库编辑、四层记忆管理、子代理管理、技能分类管理、感知/拓展 CRUD、文件空间、消息模块状态、运行时状态、用户配置
- **消息路由** — 平台无关路由核心；传统 Transport + 文件夹插件（`message/out/<platform>/`）双路径，Markdown 文件队列、附件分流、多键幂等去重、会话隔离
- **定时调度** — 四层架构（CronStore → schedule → executor → CronScheduler 守护线程），支持 daily/once/recurring，原子领取 + 持久化结果
- **Provider 双模式** — `chat`（OpenAI 兼容 `/v1/chat/completions`）和 `kemo`（原生 Kemo 网关），统一 `KemoRequest`/`KemoResponse` 协议层，两种模式不互相回退
- **统一事件协议** — `events.py` 定义 7 种 RunEvent（text_delta / reasoning_delta / tool_call_start / tool_call_result / usage / error / done），引擎与传输层共用

## 安装

### 环境要求

- Python 3.11+
- pip

### 获取源码

```bash
git clone https://github.com/kesepain-KE/kemo-agent.git
cd kemo-agent
```

### 安装依赖

```bash
pip install -r requirements.txt
```

核心依赖：

| 包 | 用途 |
|---|---|
| `fastapi` | Web 后端框架 |
| `pydantic` | 数据校验 |
| `uvicorn` | ASGI 服务器 |
| `tavily-python` | 网络搜索 |

### 配置

```bash
cp .env.example .env
```

关键环境变量：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `KEMO_BASE_URL` | Kemo 网关地址 | — |
| `KEMO_API_KEY` | Kemo 网关密钥 | — |
| `KEMO_MODEL` | Kemo 兜底模型名 | — |
| `OPENAI_BASE_URL` | OpenAI 兼容 API 地址 | — |
| `OPENAI_API_KEY` | OpenAI API 密钥 | — |
| `OPENAI_MODEL` | OpenAI 兜底模型名 | — |
| `HTTP_PROXY` / `HTTPS_PROXY` | Provider HTTP/HTTPS 代理 | 直连 |
| `TAVILY_API_KEY` | `web_search` 插件密钥；为空时工具不可用 | — |
| `WEB_HOST` | Web 监听地址 | `127.0.0.1` |
| `WEB_PORT` | Web 监听端口 | `1357` |
| `WEB_ACCESS_TOKEN` | Web URL `?token=` 访问令牌（可选） | — |
| `WEB_USERNAME` / `WEB_PASSWORD` | Web 页面使用者的账号密码 | — |
| `WEB_SESSION_SECRET` | Cookie 签名密钥；为空时自动生成 | 随机值 |
| `WEB_SESSION_COOKIE_NAME` | Session Cookie 名称 | `kemo_agent_session` |

Provider 只提供两种正式模式：`provider.type=chat` 连接最广泛的
`/v1/chat/completions`，保证文本、工具循环和原生图片输入；`provider.type=kemo`
连接完整 Kemo Provider，提供 Asset、音视频、媒体输出和更完整的生命周期能力。
两种模式不会在请求失败后互相回退。

## 用法

### Web 服务

```bash
python start_web.py                  # 默认启动
python start_web.py --port=8080      # 自定义端口
python start_web.py --host=0.0.0.0   # 局域网访问
python start_web.py --no-host        # 仅 Web API
```

启动后在浏览器打开 `http://127.0.0.1:1357`，前端提供聊天、任务、知识、记忆、子代理、技能、感知、拓展、文件、消息等管理页面。

### 命令行

```bash
python cli.py
```

交互命令（24 个）：

- 会话：`/new` `/sessions` `/use` `/clear` `/history` `/status` `/compress`
- 记忆：`/memory` `/remember` `/forget`
- 任务计划：`/plans` `/plan` `/plan-show` `/plan-approve` `/plan-pause` `/plan-resume` `/plan-cancel`
- 定时任务：`/crons` `/cron` `/cron-show` `/cron-pause` `/cron-resume` `/cron-cancel` `/cron-run` `/cron-start` `/cron-stop`
- 退出：`/exit`

### 更新

```bash
python update.py
```

## 架构概览

```
kemo-agent/
├── run/                    # 运行时核心
│   ├── engine.py           # 事件驱动对话引擎
│   ├── prompt.py           # 17 段 PromptBundle 编排
│   ├── context.py          # 上下文选取（双层历史 + 双预算）
│   ├── context_summary.py  # 摘要缓存
│   ├── history.py          # 完整归档 + 有界 temp 工作区双层历史
│   ├── memory.py           # v2 四档文件型记忆引擎
│   ├── memory_pipeline.py  # 异步记忆提取管线
│   ├── memory_migrate.py   # v1→v2 记忆迁移工具
│   ├── tools.py            # 工具发现、执行、去重
│   ├── prompt_sources.py   # PromptSourceRegistry（感知/拓展/技能选择器 + SKILL.md 解析）
│   ├── knowledge.py        # 知识检索（索引全量化）
│   ├── kemo_graph.py       # 外部知识图谱替换边界（六子层，当前占位）
│   ├── source_policy.py    # 主智能体资源准入策略
│   ├── maintenance.py      # 后台维护调度器（记忆审阅 + 上下文压缩 + 记忆生命周期）
│   ├── agents.py           # 子代理清单加载入口
│   ├── agent_runner.py     # 子代理执行器
│   ├── agent_queue.py      # 子代理调度队列
│   ├── agent_service.py    # 子代理注册与服务
│   ├── task_plan_store.py  # 任务计划持久存储
│   ├── task_plan_service.py
│   ├── task_plan_executor.py
│   ├── cron_store.py       # 定时任务存储
│   ├── runtime_host.py     # 后台宿主（Cron + Router + Transport 统一托管）
│   ├── config.py           # 配置加载（双 JSON 深合并 + .env 注入）
│   └── users.py            # 用户列表管理
├── provider/               # LLM 适配层
│   ├── factory.py          # create_provider() 路由
│   ├── kemo_gateway.py     # 原生 Kemo 网关传输
│   ├── adapters/           # chat_bridge（标准兼容）/ gateway / compat（协议转换）/ base（协议） 
│   └── protocol/           # KemoRequest / KemoResponse / ContentBlock / ProviderStreamEvent
├── web/                    # Web 模块
│   ├── app.py              # FastAPI 后端（v1 API 契约）
│   ├── service.py          # WebRunService 适配层
│   ├── auth.py             # Session 认证
│   └── frontend/           # React/TypeScript/Vite 前端（13 页面）
├── plugins/                # 工具插件（12 个，全部 SKILL.md 驱动）
│   ├── file/               # 文件读写/编辑/搜索/列目录
│   ├── get_current_time/   # UTC/本地时间
│   ├── history_search/     # 历史对话搜索
│   ├── memory_manage/      # 记忆 CRUD（搜索/增删改）
│   ├── network/            # HTTP 请求与网页读取
│   ├── shell/              # 系统命令执行
│   ├── subagent_dispatch/  # 子代理发现/调度/创建
│   ├── task_time/          # Cron 定时任务 CRUD
│   ├── web_search/         # Tavily 网络搜索
│   ├── skill_creater/      # 用户技能热创建
│   ├── expand_creater/     # 拓展模块热创建
│   └── sense_creater/      # 感知模块热创建
├── agents/                 # 内置子代理（5 个）
│   ├── _runtime/           # 子代理运行时基础（schema / resources / user_resources）
│   ├── context_manage/     # 上下文压缩与记忆提取编排
│   ├── memory_temporary_important/  # 巡检临时记忆并维护热画像
│   ├── self_improve/       # 提取微记忆碎片 / 增量权重 / 层间晋升 / 手动审阅
│   ├── task_plan/          # 结构化任务计划草案生成
│   └── time_plan/          # 自然语言→定时任务结构化调度参数
├── message/                # 外部消息路由
│   ├── router.py           # MessageRouter（RunEvent 聚合 + 出站发送）
│   ├── identity.py         # IdentityResolver 身份映射
│   ├── transport.py        # Transport 协议 / TransportRegistry
│   ├── plugin.py           # FileMessageTransport 文件夹插件
│   ├── state.py            # ProcessedMessageStore 幂等去重
│   └── schema.py           # MessageEnvelope / OutboundMessage 契约
├── cron/                   # 定时调度
│   ├── scheduler.py        # CronScheduler 守护线程
│   ├── executor.py         # 原子领取 + 执行 + 持久化
│   ├── schedule.py         # 确定性 next_run_at 计算
│   ├── service.py          # time_plan 子代理驱动的生成/编辑
│   └── review_due.py       # 重要临时记忆定期审阅循环
├── config/                 # 全局配置
│   ├── global_config.json  # 全局默认值（tools/history/prompt/kemo_graph/memory/agents/cron/…）
│   ├── global_soul.md      # 全局基座人格
│   └── agents.md           # 智能体运行手册
├── global_knowledge/       # 全局共享知识库
├── global_sense/           # 全局感知（sense.json 元数据驱动）
├── global_expand/          # 全局拓展（expand.json 元数据驱动）
├── shared_knowledge/       # 共享知识库
├── shared_skills/          # 共享技能
├── shared_expand/          # 共享拓展
├── users/                  # 多用户数据（配置/人格/历史/记忆/知识库/技能/任务）
│   └── _template/          # 用户模板
├── events.py               # 统一事件协议（7 种 RunEvent）
├── cli.py                  # 命令行入口（24 个交互命令）
├── start_web.py            # Web 启动入口
├── update.py               # 更新脚本
└── version.json            # 版本清单
```

### 核心链路

```
请求进入（Web/CLI/消息/Cron）
→ 配置加载 → 会话锁（按 source+user+session_id 串行）
→ 记忆到期审核（review_due：达标晋升/不达标删除）
→ PromptBundle 构建（17 段）
→ 上下文选择（双层历史 + Token/轮次双预算 + 多轮摘要循环）
→ 模型→工具循环（去重 + 引导注入）
→ 成功：提交历史 → 加权记忆 → 异步记忆提取
→ 失败/取消：不写历史，不加权记忆
```

### PromptBundle 顺序（17 段）

| # | 段 | 来源 |
|---|-----|------|
| 1 | user_soul | `users/<name>/user_soul.md` |
| 2 | global_soul | `config/global_soul.md` |
| 3 | agents_manual | `config/agents.md` |
| 4 | global_subagent_registry | 内置子代理注册摘要（`agents/*/trigger.md`） |
| 5 | user_subagent_registry | 用户自建子代理注册摘要（`users/<name>/agents/*/trigger.md`） |
| 6 | plugins | `plugins/*/SKILL.md` 描述 |
| 7 | skills | 共享/用户技能 SKILL.md 描述 |
| 8 | knowledge_index | 三层知识库索引（user → shared → global） |
| 9 | kemo_graph | 六子层知识图谱检索（当前占位：已启用但未连接） |
| 10 | permanent_memory | 全部注入，文件名自然排序 |
| 11 | important_memory | `memory_temporary_important.md`，受字符上限控制 |
| 12 | temporary_memory:half_year | 最多 3 条（weight 降序） |
| 13 | temporary_memory:one_month | 最多 4 条 |
| 14 | temporary_memory:seven_days | 最多 3 条 |
| 15 | task_plan | 当前活跃任务计划 |
| 16 | expand_data | 拓展模块数据采集 + 操控能力（global → shared → user） |
| 17 | perception | 全局感知数据（`sense.json` 元数据驱动） |

### 记忆档位（v2 文件型）

| 档位 | 有效期 | 晋升阈值 | 下一档 | Prompt 注入上限 |
|------|--------|----------|--------|----------------|
| seven_days | 7 天 | 3 | one_month | 最多 3 条 |
| one_month | 30 天 | 10 | half_year | 最多 4 条 |
| half_year | 180 天 | 60 | permanent | 最多 3 条 |
| permanent | 无到期 | — | — | 全部注入 |

记忆加权规则：仅在被 Prompt 引用或正文被修改后加权，同一自然日最多 +1。无每日衰减，权重无上限。到期达标晋升后新档位权重归零。

### 工具插件（12 个）

| 插件 | 功能 |
|------|------|
| `file` | 文件读写、编辑、搜索、列目录、复制、移动、删除 |
| `get_current_time` | UTC/本地时间（默认北京时间） |
| `history_search` | 用户历史对话搜索 |
| `memory_manage` | 临时/永久记忆 CRUD，暴力搜索 |
| `network` | HTTP 请求（GET/POST）与网页正文读取 |
| `shell` | 系统命令执行（支持会话、环境变量、超时） |
| `subagent_dispatch` | 子代理发现/同步调用/后台提交/状态查询/取消/四步创建流程 |
| `task_time` | Cron 定时任务 CRUD（daily/once/recurring） |
| `web_search` | Tavily 网络搜索（search/extract/crawl/map/research） |
| `skill_creater` | 用户技能热创建/更新 |
| `expand_creater` | 拓展模块热创建（list/create/validate） |
| `sense_creater` | 感知模块热创建（list/create/validate） |

### 内置子代理（5 个）

| 名称 | 触发方式 | 职责 |
|------|---------|------|
| `context_manage` | 引擎自动 / 主智能体 | 上下文压缩与记忆提取编排 |
| `memory_temporary_important` | Cron 定时 / 主智能体 | 巡检临时记忆并维护热画像 |
| `self_improve` | 压缩/晋升/手动审阅（三模式） | 提取微记忆碎片、增量权重、层间晋升 |
| `task_plan` | 主智能体工具调用 | 结构化任务计划草案生成 |
| `time_plan` | 主智能体工具调用 | 自然语言→定时任务结构化调度参数 |

### 资源层级

| 资源 | 用户级 | 共享级 | 全局级 |
|------|--------|--------|--------|
| 知识库 (knowledge) | ✅ | ✅ | ✅ |
| 技能 (skills) | ✅ | ✅ | — |
| 拓展 (expand) | ✅ | ✅ | ✅ |
| 感知 (perception) | — | — | ✅ |

优先级：用户级 > 共享级 > 全局级。白名单由 `MainAgentSourcePolicy` 统一控制。

## 相关项目

- [votx-agent](https://github.com/kesepain-KE/votx-agent) — 独立 Agent 框架
- [llm-adapter-kemo](https://github.com/kesepain-KE/llm-adapter-kemo) — LLM 适配器

## 主要项目负责人

[@kesepain](https://github.com/kesepain-KE)

## 参与贡献方式

项目处于早期开发阶段。欢迎提交 Issue 或 PR。

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/xxx`)
3. 提交更改 (`git commit -m '...'`)
4. 推送到分支 (`git push origin feature/xxx`)
5. 创建 Pull Request

## 开源协议

[Apache License 2.0](LICENSE)
