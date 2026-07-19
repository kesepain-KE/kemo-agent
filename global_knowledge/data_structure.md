# global_knowledge/ 目录结构

kemo-agent 全局知识库索引。此目录存放所有用户共享的框架说明；用户私有资料应放入 `users/<name>/knowledge/`。

更新时间：2026-07-21（清理已删除文件的索引条目）

## 文件清单

| 文件 | 用途 | 检索关键词 |
|------|------|------------|
| `data_structure.md` | 本索引文件 | 索引、知识库 |
| `子代理配置规范.md` | 子代理的 agent.json / agent-config.json / trigger.md 字段说明、5 个内置子代理一览、全局超时与生命周期 | 子代理、agent.json、agent-config、trigger、internal_mode、allowed_callers |
| `用户目录结构.md` | 用户目录骨架、子目录职责、初始化机制、user_config.json 字段、记忆分层、历史窗口 | 用户、配置、记忆、历史 |
| `全局配置文件.md` | 全局配置 `global_config.json` 全字段说明、覆盖规则、已移除项目清单 | 全局配置、global_config、provider、tools、memory、prompt |
| `环境变量.md` | 环境变量 `.env` 全字段说明、优先级链、Web 认证方式 | 环境变量、env、Web、认证 |
| `web-README.md` | Web 前端开发说明与构建指南 | web、前端、构建、开发 |

## 设计文档迁移

编程规划和方案文档已迁移至 `开发临时目录/开发文档/`，包括：

| 文件 | 用途 |
|------|------|
| `token_condense废弃-编程规划.md` | 删除 token_condense 子代理，统一压缩由 context_manage 处理 |
| `方案实装缺陷清单.md` | 全量方案核查后确认的 3 个未完成项目 |
| `子代理骨架适配-编程规划.md` | 新旧 agent.json / agent-config.json 字段映射 |
| `context_manage运行时适配-编程规划.md` | context_manage 三种新压缩机制 engine 侧实现方案 |
| `memory_temporary_important运行时适配-编程规划.md` | 新建 memory_manage 插件、cron executor 子代理直调 |
| `self_improve运行时适配-编程规划.md` | self_improve 重构双模式、废弃逐轮提取 |
| `task_plan运行时适配-编程规划.md` | task_plan 注入技能目录和知识库索引 |
| `cron模块精简-编程规划.md` | CronStore JSON 精简为 11 字段 |
| `cron模块精简-补丁.md` | 恢复 exec_mode 和 system_key 字段 |
| `感知模块标准化重构方案.md` | 感知模块 sense.json 标准化重构 |
| `拓展模块标准化重构方案.md` | 拓展模块 expand.json 标准化重构 |
| `知识库重构方案（索引全量化+路径二删除）.md` | 知识索引硬编码限制解除 |
| `全局配置文件-编程适配方案.md` | 全局配置编程适配方案 |
| `用户配置文件-编程适配方案.md` | 用户配置文件编程适配方案 |
| `环境变量-编程适配方案.md` | 环境变量编程适配方案 |
| `Kemo网关-统一Provider协议适配要求.md` | Kemo 网关统一 Provider 协议适配 |
| `子代理骨架适配-编程规划.md` | 子代理骨架适配编程规划 |

## 检索规则

1. 索引按用户级、共享级、全局级顺序完整注入；知识正文检索由外部 kemo-graph 能力负责。
2. 用户私有信息默认写入用户级知识库。
3. 全局库只存放共享说明或用户明确要求的内容。
4. 不把大段正文复制到索引。
5. 文件发生增删改移后必须同步本索引。
