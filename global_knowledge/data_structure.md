# global_knowledge/ 目录结构

kemo-agent 全局知识库索引。此目录存放所有用户共享的框架说明；用户私有资料应放入 `users/<name>/knowledge/`。

更新时间：2026-07-19（知识库索引全量化与正文检索路径删除）

## 文件清单

| 文件 | 用途 | 检索关键词 |
|------|------|------------|
| `data_structure.md` | 本索引文件 | 索引、知识库 |
| `子代理配置规范.md` | 子代理的 agent.json / agent-config.json / trigger.md 字段说明、6 个内置子代理一览、全局超时与生命周期 | 子代理、agent.json、agent-config、trigger、internal_mode、allowed_callers |
| `子代理骨架适配-编程规划.md` | 新旧 agent.json / agent-config.json 字段映射表、6 个受影响文件的修改方案、6 步详细规划 | 适配、映射、schema、AgentDefinition、AgentCapabilities、编程 |
| `context_manage运行时适配-编程规划.md` | context_manage 三种新压缩机制的 engine 侧实现方案 | context_manage、engine、压缩、工具日志、self_improve |
| `memory_temporary_important运行时适配-编程规划.md` | 新建 memory_manage 插件、cron executor 子代理直调、两个 cron 任务注册、maintenance.py 瘦身 | memory_temporary_important、cron、memory_manage、maintenance、巡检 |
| `self_improve运行时适配-编程规划.md` | self_improve 重构为双模式（context_manage 批处理 + cron 晋升检查）、废弃逐轮提取、新建 skill_creater 插件、review_due 迁移到 cron | self_improve、晋升、skill_creater、context_manage、review_due |
| `task_plan运行时适配-编程规划.md` | task_plan 注入技能目录和知识库索引、编辑模式状态限制、auto_accept 提醒 | task_plan、技能注入、知识库、auto_accept、编辑限制 |
| `cron模块精简-编程规划.md` | CronStore JSON 从 17 字段精简为 11 字段、全链路北京时间、三种 exec_mode 路径共存 | cron、精简、北京时间、normalize_task、compute_next_run |
| `cron模块精简-补丁.md` | 补丁：恢复 exec_mode 和 system_key 字段，系统任务保留 subagent/function 路径 | cron、补丁、exec_mode、system_key |
| `知识库重构方案（索引全量化+路径二删除）.md` | 知识索引硬编码限制解除、正文关键词检索与 knowledge_search 插件整条删除 | 知识库、索引全量、路径二删除、knowledge_search、重构 |
| `用户目录结构.md` | 用户目录骨架、子目录职责、初始化机制、user_config.json 字段、记忆分层、历史窗口 | 用户、配置、记忆、历史 |
| `全局配置文件.md` | 全局配置 `global_config.json` 全字段说明、覆盖规则、已移除项目清单 | 全局配置、global_config、provider、tools、memory、prompt |
| `环境变量.md` | 环境变量 `.env` 全字段说明、优先级链、Web 认证方式 | 环境变量、env、Web、认证 |


## 检索规则

1. 索引按用户级、共享级、全局级顺序完整注入；知识正文检索由外部 kemo-graph 能力负责。
2. 用户私有信息默认写入用户级知识库。
3. 全局库只存放共享说明或用户明确要求的内容。
4. 不把大段正文复制到索引。
5. 文件发生增删改移后必须同步本索引。
