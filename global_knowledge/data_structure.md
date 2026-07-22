# global_knowledge/ 目录结构

kemo-agent 全局知识库索引。此目录存放所有用户共享的框架说明；用户私有资料应放入 `users/<name>/knowledge/`。

更新时间：2026-07-22（新增 task-plan-plugin-plan.md）

## 索引

| 文件 | 用途 | 检索关键词 |
|------|------|------------|
| `data_structure.md` | 本索引文件 | 索引、知识库 |
| `version-and-update-modules.md` | 版本号与更新模块对照 — 5 组版本号分别对应 all/core/agents/plugins/web 的更新范围 | 版本号、更新、模块 |
| `update-module-refactor-plan.md` | update 模块板块化重构编程方案 — 4 大板块独立更新脚本 + update.py 调度器改造 | update、板块、模块化、重构 |
| `external-message-plugin-plan.md` | external_message 插件编程方案 — 工具型 send_message/send_file + 指令型 create_platform | 外部消息、插件、平台 |
| `global-config-reference.md` | global_config.json 配置项手册 — 全部 15 个配置组的字段、默认值与说明 | 全局配置、config、参数 |
| `history-index-design.md` | history/data.json 可恢复会话索引实装规范 — 机器 ID、精确 active 引用、三条链路与记忆游标恢复 | 历史、会话、索引、外部消息 |
| `user-config-reference.md` | user_config.json 配置项手册 — 10 个配置组的字段、默认值与说明，含白名单规则和与全局配置的关系 | 用户配置、provider、白名单 |
| `user-create-module-plan.md` | user_create.py 编程方案 — CLI + import 双模，骨架复制 + 后处理 + API 配置引导 | 用户创建、CLI、provider |
| `task-plan-plugin-plan.md` | task_plan 插件编程方案 — 暴露 PlanStore 运行态操作（step_done/step_fail/view/list/abort/approve/pause/resume）为主智能体工具 | task_plan、插件、步骤标记、计划管理 |

## 检索规则

1. 索引按用户级、共享级、全局级顺序完整注入；知识正文检索由外部 kemo-graph 能力负责。
2. 用户私有信息默认写入用户级知识库。
3. 全局库只存放共享说明或用户明确要求的内容。
4. 不把大段正文复制到索引。
5. 文件发生增删改移后必须同步本索引。
