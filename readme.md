# kemo-agent

<p align="center">
  <img src="kemo-agent.jpg" alt="kemo-agent logo" width="200">
</p>

<p align="center">
  <strong>面向新一代个人智能基础设施的本地多用户 Agent Runtime。</strong>
</p>

<p align="center">
  以生命周期记忆为核心，统一编排上下文、子代理、工具、环境感知、外部扩展与跨平台交互，<br>
  使智能体具备长期记忆、持续演化、复杂任务调度与现实世界连接能力。
</p>

<p align="center">
  <a href="https://github.com/kesepain-KE/kemo-agent">
    <img src="https://img.shields.io/badge/version-0.1.0--dev-blue" alt="version">
  </a>
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="python">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="license">
  </a>
</p>

> [!IMPORTANT]
> kemo-agent 当前处于 `0.1.0-dev` 早期开发阶段。核心运行链路已基本闭环，但部分外部集成与用户扩展能力仍在建设中，暂不建议直接用于关键生产环境。

---

## 项目定位

kemo-agent 不是聊天前端，也不是对单一模型 API 的简单封装，而是一套面向长期运行的本地智能体基础设施。

它围绕完整的 Agent 请求生命周期构建：

```text
配置加载
→ Prompt 编排
→ 上下文选取
→ Provider 调用
→ 工具循环
→ 历史提交
→ 记忆更新
```

同一个智能体可以同时通过 Web、CLI、外部消息平台与 Cron 定时任务接收请求。不同入口的会话上下文相互隔离，但共享同一套用户配置、记忆体系、知识资源与能力准入策略。

kemo-agent 当前支持四类运行入口：

- **Web**：基于 SSE 的流式交互与完整管理界面
- **CLI**：本地交互式命令行
- **Message Router**：面向 OneBot、Telegram 与文件夹消息插件的统一路由
- **Cron**：持久化定时任务与后台自动执行

---

## 核心特性

### 生命周期记忆

kemo-agent 采用四档文件型记忆体系：

```text
seven_days → one_month → half_year → permanent
```

每条记忆以独立 Markdown 文件保存，临时层使用轻量 JSON 索引记录权重与到期时间。

记忆不会按固定周期机械衰减。只有在后续对话中被引用或正文被修改时才会加权，同一自然日最多增加一次。达到晋升阈值后进入更长期档位；到期仍未达到阈值的内容会被删除。

这种机制使长期记忆成为经过实际使用与时间筛选后留下的信息，而不是无限累积的对话垃圾。

### 结构化 Prompt 编排

系统提示词通过固定顺序的 17 段 `PromptBundle` 管线构建，而不是无约束地拼接字符串。

每一段均支持：

- 独立启用与禁用
- 独立字符预算
- 独立来源诊断
- 截断状态记录
- 缺失占位
- 顺序一致性校验

任何段落顺序偏离都会直接抛出异常，避免 Prompt 结构在长期迭代中静默漂移。

### 多入口，同一运行时

Web、CLI、消息路由与 Cron 共用同一套运行核心和事件协议。

消息来源按照 `source + user + session_id` 进行会话隔离，避免不同入口的上下文相互污染；记忆、知识、配置与资源准入策略则在用户范围内共享。

### 主智能体统一决策

框架内置上下文压缩、记忆审阅、自我改进、任务规划和定时任务解析等子代理，但子代理不具备无约束的外部执行权限。

主智能体负责最终决策、工具调用与任务边界控制；子代理作为受控的能力扩展参与运行。

### 本地优先与数据可迁移

对话历史、记忆、知识库、配置和用户文件均以开放格式保存在本地文件系统中：

- Markdown：记忆、技能与注册信息
- JSON：配置、索引与模块元数据
- JSONL：对话历史

数据可以直接查看、备份、迁移和版本管理，不依赖封闭数据库或远程托管平台。

### 统一事件协议

框架定义 7 种标准 `RunEvent`：

```text
text_delta
reasoning_delta
tool_call_start
tool_call_result
usage
error
done
```

对话引擎、CLI、Web SSE、消息路由和 Cron 执行共用同一套事件契约，从而避免不同传输层重复实现运行逻辑。

---

## 功能概览

| 子系统 | 能力 |
|---|---|
| 对话引擎 | 模型与工具循环、会话锁、工具签名去重、运行中引导注入、失败回滚 |
| Prompt 编排 | 17 段固定管线、独立预算、来源诊断、缺失占位与顺序校验 |
| 记忆系统 | 四档生命周期、引用加权、到期晋升或删除、v1→v2 迁移 |
| 上下文管理 | 完整归档层、有界 temp 工作区、Token/轮次双预算、多轮摘要 |
| Provider | OpenAI Chat Completions 兼容模式与原生 Kemo 网关模式 |
| 工具系统 | 12 个 `SKILL.md` 驱动插件，自动发现与统一执行 |
| 子代理 | 5 个内置子代理，支持用户热插拔扩展 |
| 知识系统 | 用户级、共享级、全局级三层知识索引 |
| 感知与拓展 | JSON 元数据驱动，支持外部数据采集与能力扩展 |
| 任务系统 | 结构化任务计划、审批、暂停、恢复与取消 |
| Cron | daily / once / recurring，原子领取与持久化结果 |
| 消息路由 | Transport 与文件夹插件双路径、身份映射、幂等去重 |
| Web 管理端 | React + TypeScript + Vite，13 个管理页面 |
| 后台维护 | 记忆审阅、到期审核、活跃会话上下文压缩 |

---

## 快速开始

### 环境要求

- Python 3.11+
- pip
- Git

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
| `pydantic` | 数据模型与参数校验 |
| `uvicorn` | ASGI 服务运行器 |
| `tavily-python` | 网络搜索能力 |

### 初始化配置

```bash
cp .env.example .env
```

关键环境变量：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `KEMO_BASE_URL` | Kemo 网关地址 | — |
| `KEMO_API_KEY` | Kemo 网关密钥 | — |
| `KEMO_MODEL` | Kemo 模式兜底模型 | — |
| `OPENAI_BASE_URL` | OpenAI 兼容 API 地址 | — |
| `OPENAI_API_KEY` | OpenAI 兼容 API 密钥 | — |
| `OPENAI_MODEL` | Chat 模式兜底模型 | — |
| `HTTP_PROXY` / `HTTPS_PROXY` | Provider HTTP/HTTPS 代理 | 直连 |
| `TAVILY_API_KEY` | `web_search` 插件密钥 | — |
| `WEB_HOST` | Web 监听地址 | `127.0.0.1` |
| `WEB_PORT` | Web 监听端口 | `1357` |
| `WEB_ACCESS_TOKEN` | URL `?token=` 访问令牌 | — |
| `WEB_USERNAME` / `WEB_PASSWORD` | Web 登录账号与密码 | — |
| `WEB_SESSION_SECRET` | Cookie 签名密钥 | 自动生成 |
| `WEB_SESSION_COOKIE_NAME` | Session Cookie 名称 | `kemo_agent_session` |

---

## Provider 模式

kemo-agent 提供两种正式 Provider 模式，底层统一映射为 `KemoRequest` / `KemoResponse` 协议模型。

### `chat`

连接标准 OpenAI Chat Completions 兼容接口：

```text
/v1/chat/completions
```

适用于文本生成、工具循环和原生图片输入等通用场景。

### `kemo`

连接原生 Kemo 网关协议，提供：

- Asset 生命周期管理
- 多模态输入与输出
- 音频、视频与媒体能力
- 流式恢复
- 更完整的使用量与资源信息

两种模式保持明确边界，请求失败后不会自动互相回退。

---

## 使用方式

### 启动 Web 服务

```bash
python start_web.py
```

常用参数：

```bash
python start_web.py --port=8080
python start_web.py --host=0.0.0.0
python start_web.py --no-host
```

默认访问地址：

```text
http://127.0.0.1:1357
```

Web 前端提供聊天、任务计划、Cron、知识库、记忆、子代理、技能、感知、拓展、文件、消息模块、运行时状态和用户配置等管理页面。

### 启动 CLI

```bash
python cli.py
```

CLI 当前提供 24 个交互命令。

#### 会话

```text
/new
/sessions
/use
/clear
/history
/status
/compress
```

#### 记忆

```text
/memory
/remember
/forget
```

#### 任务计划

```text
/plans
/plan
/plan-show
/plan-approve
/plan-pause
/plan-resume
/plan-cancel
```

#### 定时任务

```text
/crons
/cron
/cron-show
/cron-pause
/cron-resume
/cron-cancel
/cron-run
/cron-start
/cron-stop
```

#### 退出

```text
/exit
```

### 更新项目

```bash
python update.py
```

---

## 架构概览

```text
kemo-agent/
├── run/                    # Agent Runtime 核心
│   ├── engine.py           # 事件驱动对话引擎
│   ├── prompt.py           # 17 段 PromptBundle 编排
│   ├── context.py          # 双层历史与双预算上下文选取
│   ├── context_summary.py  # 摘要生成与缓存
│   ├── history.py          # 完整归档 + 有界 temp 工作区
│   ├── memory.py           # v2 四档文件型记忆引擎
│   ├── memory_pipeline.py  # 异步记忆提取管线
│   ├── memory_migrate.py   # v1→v2 迁移工具
│   ├── tools.py            # 工具发现、执行与去重
│   ├── prompt_sources.py   # Prompt 资源注册与选择
│   ├── knowledge.py        # 知识检索与索引注入
│   ├── kemo_graph.py       # 外部知识图谱边界
│   ├── source_policy.py    # 主智能体资源准入策略
│   ├── maintenance.py      # 后台维护调度器
│   ├── agents.py           # 子代理清单加载
│   ├── agent_runner.py     # 子代理执行器
│   ├── agent_queue.py      # 子代理调度队列
│   ├── agent_service.py    # 子代理注册与服务
│   ├── task_plan_store.py  # 任务计划持久化
│   ├── task_plan_service.py
│   ├── task_plan_executor.py
│   ├── cron_store.py       # 定时任务存储
│   ├── runtime_host.py     # Cron、Router 与 Transport 宿主
│   ├── config.py           # 配置深合并与环境变量注入
│   └── users.py            # 用户管理
├── provider/               # LLM Provider 适配层
│   ├── factory.py
│   ├── kemo_gateway.py
│   ├── adapters/
│   └── protocol/
├── web/                    # Web 管理端
│   ├── app.py              # FastAPI API
│   ├── service.py          # WebRunService
│   ├── auth.py             # Session 认证
│   └── frontend/           # React + TypeScript + Vite
├── plugins/                # 12 个 SKILL.md 驱动插件
├── agents/                 # 5 个内置子代理
├── message/                # 外部消息路由
├── cron/                   # 定时任务调度
├── config/                 # 全局配置与系统人格
├── global_knowledge/       # 全局知识库
├── global_sense/           # 全局感知
├── global_expand/          # 全局拓展
├── shared_knowledge/       # 共享知识库
├── shared_skills/          # 共享技能
├── shared_expand/          # 共享拓展
├── users/                  # 用户数据与用户资源
├── events.py               # 统一事件协议
├── cli.py                  # CLI 入口
├── start_web.py            # Web 启动入口
├── update.py               # 更新脚本
└── version.json            # 版本清单
```

### 核心运行链路

```text
请求进入（Web / CLI / Message / Cron）
→ 加载全局与用户配置
→ 获取 source + user + session_id 会话锁
→ 执行到期记忆审核
→ 构建 17 段 PromptBundle
→ 选择上下文并执行预算压缩
→ 进入模型与工具循环
→ 成功：提交历史、更新权重、异步提取记忆
→ 失败或取消：不写入历史，不更新记忆
```

该提交策略保证失败请求不会污染长期数据。

---

## PromptBundle 管线

| # | 段 | 来源 |
|---:|---|---|
| 1 | `user_soul` | `users/<name>/user_soul.md` |
| 2 | `global_soul` | `config/global_soul.md` |
| 3 | `agents_manual` | `config/agents.md` |
| 4 | `global_subagent_registry` | `agents/*/trigger.md` |
| 5 | `user_subagent_registry` | `users/<name>/agents/*/trigger.md` |
| 6 | `plugins` | `plugins/*/SKILL.md` |
| 7 | `skills` | 共享与用户技能描述 |
| 8 | `knowledge_index` | 用户级、共享级、全局级知识索引 |
| 9 | `kemo_graph` | 外部知识图谱检索结果 |
| 10 | `permanent_memory` | 永久记忆 |
| 11 | `important_memory` | 临时重要记忆 |
| 12 | `temporary_memory:half_year` | 半年记忆 |
| 13 | `temporary_memory:one_month` | 一月记忆 |
| 14 | `temporary_memory:seven_days` | 七天记忆 |
| 15 | `task_plan` | 当前活跃任务计划 |
| 16 | `expand_data` | 外部拓展数据与操作能力 |
| 17 | `perception` | 环境感知数据 |

---

## 记忆系统

### 档位与晋升

| 档位 | 有效期 | 晋升阈值 | 下一档 | Prompt 注入上限 |
|---|---:|---:|---|---:|
| `seven_days` | 7 天 | 3 | `one_month` | 3 条 |
| `one_month` | 30 天 | 10 | `half_year` | 4 条 |
| `half_year` | 180 天 | 60 | `permanent` | 3 条 |
| `permanent` | 永久 | — | — | 全量 |

### 加权规则

- 仅在记忆被 Prompt 引用或正文被修改后加权
- 同一自然日最多 `+1`
- 不执行每日衰减
- 权重不设固定上限
- 晋升后新档位权重归零
- 到期未达到阈值则删除

### 文件结构

- 临时记忆：独立 `.md` 正文 + `data.json` 轻量索引
- 永久记忆：纯 Markdown 文件
- 临时重要记忆：由后台子代理定期巡检并维护
- 迁移工具：支持 v1→v2 一键转换

---

## 上下文管理

kemo-agent 将历史划分为两个层级：

### 归档层

- 保存完整对话
- 不受最大轮次限制
- 不参与破坏性裁剪
- 作为审计、回溯和摘要来源

### temp 工作区

- 受轮次和 Token 双预算限制
- 只保留当前推理所需上下文
- 裁剪后执行局部重编号
- 保证完整轮次不被拆分

摘要按 `source_hash` 命中缓存，避免对相同历史重复生成。单次压缩不足以满足预算时，引擎会循环压缩直至达到稳定状态。

---

## 工具系统

工具由插件目录中的 `SKILL.md` 统一声明，插件描述和工具定义不再拆分到独立 JSON 文件。

框架通过 `discover_plugin_manifests()` 自动扫描并装配插件。

| 插件 | 功能 |
|---|---|
| `file` | 文件读写、编辑、搜索、复制、移动与删除 |
| `get_current_time` | UTC 与本地时间查询 |
| `history_search` | 用户历史对话搜索 |
| `memory_manage` | 临时与永久记忆 CRUD |
| `network` | HTTP 请求与网页正文读取 |
| `shell` | 系统命令执行、会话、环境变量与超时控制 |
| `subagent_dispatch` | 子代理发现、调用、后台提交、查询、取消与创建 |
| `task_time` | Cron 任务 CRUD |
| `web_search` | Tavily 搜索、提取、爬取、映射与研究 |
| `skill_creater` | 用户技能热创建与更新 |
| `expand_creater` | 拓展模块热创建与校验 |
| `sense_creater` | 感知模块热创建与校验 |

---

## 子代理体系

| 子代理 | 触发方式 | 职责 |
|---|---|---|
| `context_manage` | 引擎自动 / 主智能体 | 上下文压缩与记忆提取编排 |
| `memory_temporary_important` | Cron / 主智能体 | 巡检临时记忆并维护热画像 |
| `self_improve` | 压缩、晋升、手动审阅 | 提取微记忆碎片、增量权重与层间晋升 |
| `task_plan` | 主智能体工具调用 | 生成结构化任务计划草案 |
| `time_plan` | 主智能体工具调用 | 将自然语言转换为定时调度参数 |

子代理采用精简清单模式：

```text
agent.json
trigger.md
```

用户可以通过热插拔数据包扩展自己的子代理。每个子代理均受独立授权、超时与取消信号约束。

---

## 资源分层

| 资源 | 用户级 | 共享级 | 全局级 |
|---|:---:|:---:|:---:|
| Knowledge | ✅ | ✅ | ✅ |
| Skills | ✅ | ✅ | — |
| Expand | ✅ | ✅ | ✅ |
| Perception | — | — | ✅ |

资源解析优先级：

```text
用户级 > 共享级 > 全局级
```

实际可用范围由 `MainAgentSourcePolicy` 白名单统一控制。

---

## 消息路由

消息模块提供两种接入路径。

### Transport

用于具备 SDK 或稳定协议的平台，例如：

- OneBot
- Telegram

### 文件夹插件

用于缺少 SDK 或需要跨进程解耦的平台，通过 Markdown 文件队列交换消息：

```text
message/out/<platform>/
```

消息路由同时提供：

- 用户身份映射
- 会话隔离
- 附件分流
- 多键幂等去重
- 统一 `RunEvent` 聚合
- 出站消息分发

---

## 定时任务与后台维护

### Cron 调度

Cron 采用四层结构：

```text
CronStore
→ schedule
→ executor
→ CronScheduler
```

支持：

- `daily`
- `once`
- `recurring`
- 原子领取
- 执行状态持久化
- 暂停、恢复与取消
- 手动立即运行

### MaintenanceScheduler

后台维护系统独立于 Cron，对智能体运行状态执行周期性维护：

- 临时重要记忆审阅
- 每日记忆到期审核
- 活跃会话上下文压缩

---

## Web 管理端

前端采用：

```text
React + TypeScript + Vite
```

后端采用：

```text
FastAPI
```

当前包含 13 个管理页面：

1. 流式聊天
2. 任务计划
3. Cron 管理
4. 知识库浏览与编辑
5. 四层记忆管理
6. 子代理管理
7. 技能分类管理
8. 感知模块管理
9. 拓展模块管理
10. 文件空间
11. 消息模块状态
12. 运行时状态
13. 用户配置

---

## 当前状态

当前版本：`0.1.0-dev`

### 已完成

- 配置加载与用户配置深合并
- 17 段 PromptBundle 编排
- 双层上下文与多轮摘要
- Provider 双模式
- 模型与工具循环
- 历史提交与失败回滚
- v2 文件型记忆系统
- 5 个内置子代理
- 12 个工具插件
- 感知与拓展 JSON 元数据标准化
- Web 端 13 页面基础管理闭环
- 统一 RunEvent 事件协议

### 建设中

- kemo-graph 外部知识图谱实际接入
- 共享层与用户层的扩展模板完善
- OneBot 与 Telegram 平台客户端
- 用户创建模块的完整实现
- 面向更大规模部署的性能验证

---

## 相关项目

- [votx-agent](https://github.com/kesepain-KE/votx-agent)  
  独立维护的 Agent 框架，与 kemo-agent 不存在继承关系。

- [kemo-adapter-api](https://github.com/kesepain-KE/kemo-adapter-api)  
  Kemo 网关适配器，为 Provider 的 `kemo` 模式提供原生协议支持。

三个项目独立开发，通过 API 与协议进行通信。

---

## 主要维护者

[@kesepain](https://github.com/kesepain-KE)

---

## 参与贡献

项目仍处于早期开发阶段，欢迎通过 Issue 和 Pull Request 参与讨论与改进。

```bash
git checkout -b feature/your-feature
git commit -m "feat: describe your change"
git push origin feature/your-feature
```

推荐流程：

1. Fork 本仓库
2. 创建功能分支
3. 完成代码与文档修改
4. 执行相关测试
5. 提交 Pull Request

---

## 开源协议

本项目基于 [Apache License 2.0](LICENSE) 开源。