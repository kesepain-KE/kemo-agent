# global_knowledge/ 目录结构

kemo-agent 全局知识库索引。此目录存放所有用户共享的框架说明；用户私有资料应放入 `users/<name>/knowledge/`。

更新时间：2026-07-21

## 文件清单

| 文件 | 用途 | 检索关键词 |
|------|------|------------|
| `data_structure.md` | 本索引文件 | 索引、知识库 |
| `子代理配置规范.md` | 子代理的 agent.json / agent-config.json / trigger.md 字段说明、5 个内置子代理一览、全局超时与生命周期 | 子代理、agent.json、agent-config、trigger、internal_mode、allowed_callers |
| `用户目录结构.md` | 用户目录骨架、子目录职责、初始化机制、user_config.json 字段、记忆分层、历史窗口 | 用户、配置、记忆、历史 |
| `全局配置文件.md` | 全局配置 `global_config.json` 全字段说明、覆盖规则、已移除项目清单 | 全局配置、global_config、provider、tools、memory、prompt |
| `环境变量.md` | 环境变量 `.env` 全字段说明、优先级链、Web 认证方式 | 环境变量、env、Web、认证 |
| `web-README.md` | Web 前端开发说明与构建指南 | web、前端、构建、开发 |
| `history_search工具优化-编程方案.md` | history_search 工具优化：since/until 时间过滤、role 角色过滤、match_mode 匹配精度、snippet 片段截断、context 上下文窗口、指令型 SKILL.md | history_search、优化、时间过滤、角色、上下文、snippet |
| `memory_manage工具优化-编程方案.md` | memory_manage 工具优化：新增 list/get action、search_by_content 始终返回 snippet、context_chars 可调、limit 上限保护、指令型 SKILL.md 含层级和权限说明 | memory_manage、优化、list、get、snippet、limit、层级 |
| `expand_creater工具创建-编程方案.md` | expand_creater 工具从零创建：3 个 action（list/create/validate）、四步创建流程、原子写入 5 文件（expand.json/expand_control.md/start_expand.py/data_update.py/input_data.md）、模板骨架、指令型 SKILL.md | expand_creater、创建、四步流程、拓展模块、expand |
| `skill_creater工具优化-编程方案.md` | skill_creater 工具优化：新增 list/get/validate、结构化参数创建（title+description+tool_schema/instruction）、四步创建流程、指令型 SKILL.md | skill_creater、优化、list、get、validate、结构化、四步流程 |
| `task_time工具优化-编程方案.md` | task_time 工具优化：新增 get action、list 加 query 过滤、SKILL.md 写入 time_plan 管道硬性调用规则（自然语言必须走 time_plan → task_time）、同步 time_plan trigger.md | task_time、优化、get、query、time_plan、管道、调用规则 |
| `sense_creater工具创建-编程方案.md` | sense_creater 工具从零创建：3 个 action（list/create/validate）、四步创建流程、原子写入 3 文件（sense.json/sense.md/data_update.py）、模板骨架、指令型 SKILL.md、与 expand_creater 对比 | sense_creater、创建、四步流程、感知模块、sense |
| `web_search工具优化-编程方案.md` | web_search 工具 SKILL.md 优化（不改 tool.py）：使用决策表、参数速查、5 个典型示例、返回字段解读（content_truncated/truncated）、指令型引导 | web_search、优化、SKILL.md、决策表、示例、Tavily |


## 设计文档迁移

编程规划和方案文档已迁移至 `开发临时目录/开发文档/`：

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
| `kemo-graph粒度化替换-编程规划.md` | kemo_graph 拆分为 4 个独立开关（全局/共享/用户知识 + 临时记忆），粒度化替换控制 |
| `外部消息模块插件化-编程规划.md` | 外部消息模块 out/<platform>/ 文件夹级插件化设计、message.md 消息格式、接口契约 |
| `历史存储双层架构微重构-编程规划.md` | 历史存储双层架构微重构：从 data.json 移除 context 字段、归档/temp 职责区分 |
| `系统提示词拼接重构-编程规划.md` | 系统提示词拼接重构：subagent 拆为 global/user、knowledge_index 替换标注、kemo_graph 细分 6 子层 |
| `Web前端补全API-编程规划.md` | 新增 15 条 API 编程规划：头像、文件浏览下载删除、tmp、子智能体、消息路由、人格、Logo、拓展（已落地至 web/app.py + web/service.py） |
| `Web API 实际落地参考.md` | 36 条 API 实际落地参考文档，含全量请求/响应格式、安全约束、前端调用注意事项（已落地至 web/app.py + web/service.py） |
| `流式输出缓冲修复-编程方案.md` | 修复 chat API 流式输出被 `list()` 全量缓冲的问题（已落地） |
| `用户子代理解除executor限制-编程方案.md` | 删除 `_runtime/schema.py` 中对用户子代理的 3 处硬限制（已落地） |
| `子代理审查修复-编程方案.md` | 修复 context_manage trigger.md 过时声明 + time_plan executor.py 加输入输出校验（已落地） |
| `修复会话历史404-前端方案.md` | 前端 ChatPage 从 sessions 缓存判断会话是否已 commit，消除 uvicorn 404 日志（已落地） |
| `系统定时任务独立目录-编程方案.md` | 将记忆晋升/巡检/每日整理三个系统定时任务从 `users/{user}/task_cron/` 迁移到 `cron/task_cron_system/` |
| `子代理调用机制审查-编程方案.md` | 子代理调用机制全面审查：allowed_callers 与 trigger.md 一致性修复、compact manifest 设计说明 |
| `subagent_dispatch创建流程四步引导-编程方案.md` | 修改 SKILL.md 在 create 前插入四步判断流程：批判性判断 → 确认工具 → 确认触发词 → 冲突检查 |
| `shell工具六项优化-编程方案.md` | shell 工具六项优化：指令型 SKILL.md、去掉 action 冗余、新增内置命令、shell_type、locale 编码、chain_timeout_mode |
| `network工具优化-编程方案.md` | network 工具优化：新增 put/delete/patch、统一 truncated、失败返 error、max_bytes 可配、指令型 SKILL.md |
| `file工具优化-编程方案.md` | file 工具优化：新增 exists/hash、read max_bytes 保护、tail 反向扫描、search 黑名单、copy/move 目录、指令型 SKILL.md |
| `get_current_time工具优化-编程方案.md` | get_current_time 工具优化：默认北京时间、target_timezone 时区转换、format 多格式输出、指令型 SKILL.md |

## 检索规则

1. 索引按用户级、共享级、全局级顺序完整注入；知识正文检索由外部 kemo-graph 能力负责。
2. 用户私有信息默认写入用户级知识库。
3. 全局库只存放共享说明或用户明确要求的内容。
4. 不把大段正文复制到索引。
5. 文件发生增删改移后必须同步本索引。
6. 索引中不列出 `.bak` 备份文件。
