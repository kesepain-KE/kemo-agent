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

框架围绕一次完整的智能体请求链路设计：从配置加载、Prompt 编排、上下文选取、Provider 调用、工具循环到历史提交与记忆更新。各模块独立演进，通过 14 段固定顺序的 PromptBundle 管线组装。

### 主要模块

- **对话引擎** — 模型↔工具循环，工具签名去重，运行中引导注入，成功才提交历史，失败/取消不污染数据
- **Prompt 编排** — 14 段固定顺序拼接，每段独立字符上限，顺序偏离即抛异常
- **记忆系统（v2）** — 四档文件型存储：seven_days → one_month → half_year → permanent。独立 `.md` 正文 + `data.json` 轻量索引，引用/修改触发加权，到期自动晋升或删除，无每日衰减
- **工具系统** — 插件 `SKILL.md` 的 `## Tool` JSON 块作为唯一工具声明来源，`discover_plugin_manifests()` 自动发现
- **上下文管理** — 整轮不可拆分，Token + 轮次双预算，多轮摘要循环至预算达标
- **子代理** — 6 个内置子代理，用户可通过热插拔数据包扩展。独立授权、超时、取消信号，不自动继承主会话历史
- **配置系统** — `global_config.json` + `user_config.json` 深合并，`.env` 仅做密钥兜底
- **资源分层** — 用户级 > 共享级 > 全局级，覆盖知识库、技能、拓展与感知
- **Web 界面** — FastAPI + React/TypeScript/Vite，流式聊天、知识浏览、任务管理
- **消息路由** — 平台无关的消息路由核心，幂等去重，按 source+session_id 会话隔离
- **定时调度** — 四层架构（存储→时间计算→原子执行→守护线程），daily/once/recurring

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

## 用法

### Web 服务

```bash
python start_web.py                  # 默认启动
python start_web.py --port=8080      # 自定义端口
python start_web.py --host=0.0.0.0   # 局域网访问
python start_web.py --no-host        # 仅 Web API
```

### 命令行

```bash
python cli.py
```

交互命令：

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
│   ├── prompt.py           # PromptBundle 编排
│   ├── context.py          # 上下文选取
│   ├── context_summary.py  # 摘要缓存
│   ├── history.py          # 四文件历史管理
│   ├── memory.py           # 四档记忆引擎
│   ├── memory_pipeline.py  # 异步记忆提取
│   ├── memory_migrate.py   # 记忆迁移工具
│   ├── tools.py            # 工具发现与执行
│   ├── prompt_sources.py   # 提示词来源
│   ├── knowledge.py        # 知识检索
│   ├── agents.py           # 子代理发现
│   ├── agent_runner.py     # 子代理调用
│   ├── task_plan_store.py  # 任务计划存储
│   ├── task_plan_service.py
│   ├── task_plan_executor.py
│   ├── cron_store.py       # 定时任务存储
│   ├── runtime_host.py     # 后台宿主
│   ├── config.py           # 配置加载
│   ├── source_policy.py    # 资源准入
│   └── users.py            # 用户管理
├── provider/               # LLM 适配层
│   ├── schema.py
│   ├── factory.py
│   ├── kemo_gateway.py
│   └── openai_chat.py
├── web/                    # Web 模块
│   ├── app.py              # FastAPI 后端
│   ├── service.py          # 服务适配
│   ├── auth.py             # 认证
│   └── frontend/           # React 前端
├── plugins/                # 工具插件
│   ├── file/
│   ├── get_current_time/
│   ├── history_search/
│   ├── knowledge_search/
│   ├── network/
│   ├── shell/
│   ├── subagent_dispatch/
│   ├── task_time/
│   └── web_search/
├── agents/                 # 内置子代理
│   ├── context_manage/
│   ├── token_condense/
│   ├── self_improve/
│   ├── memory_temporary_important/
│   ├── task_plan/
│   └── time_plan/
├── message/                # 外部消息路由
├── cron/                   # 定时调度
├── config/                 # 全局配置
├── global_knowledge/       # 全局知识库
├── global_sense/           # 全局感知
├── global_expand/          # 全局拓展
├── shared_knowledge/       # 共享知识库
├── shared_skills/          # 共享技能
├── shared_expand/          # 共享拓展
├── users/                  # 多用户数据
├── events.py               # 统一事件协议
├── cli.py                  # 命令行入口
├── start_web.py            # Web 启动入口
├── update.py               # 更新脚本
└── version.json            # 版本清单
```

### 核心链路

```
请求进入 → 配置加载 → 会话锁 → 记忆审核
→ PromptBundle 构建（14 段）
→ 上下文选择（整轮 + 双预算 + 多轮摘要）
→ 模型→工具循环（去重 + 引导注入）
→ 成功：提交历史 → 加权记忆 → 异步提取
→ 失败/取消：不写历史
```

### PromptBundle 顺序

| # | 段 | 来源 |
|---|-----|------|
| 1 | user_soul | `users/<name>/user_soul.md` |
| 2 | global_soul | `config/global_soul.md` |
| 3 | agents_manual | `agents.md` |
| 4 | plugins | `plugins/*/SKILL.md` |
| 5 | skills | 共享/用户技能 |
| 6 | knowledge_index | 三层知识库索引 |
| 7 | permanent_memory | 全部注入 |
| 8 | important_memory | 独立文件，有字符上限 |
| 9 | half_year | 最多 3 条 |
| 10 | one_month | 最多 4 条 |
| 11 | seven_days | 最多 3 条 |
| 12 | task_plan | 当前活跃计划 |
| 13 | expand_data | 拓展注册表 |
| 14 | perception | 全局感知文件 |

### 记忆档位

| 档位 | 有效期 | 晋升阈值 | 下一档 |
|------|--------|----------|--------|
| seven_days | 7 天 | 3 | one_month |
| one_month | 30 天 | 10 | half_year |
| half_year | 180 天 | 60 | permanent |
| permanent | 无到期 | — | — |

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
