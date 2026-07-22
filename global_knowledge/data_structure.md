# global_knowledge/ 目录结构

kemo-agent 全局知识库索引。此目录存放所有用户共享的框架说明；用户私有资料应放入 `users/<name>/knowledge/`。

更新时间：2026-07-22（索引刷新）

## 索引

| 文件 | 用途 | 检索关键词 |
|------|------|------------|
| `data_structure.md` | 本索引文件 | 索引、知识库 |
| `version-and-update-modules.md` | 版本号与更新模块对照 — 5 组版本号分别对应 all/core/agents/plugins/web 的更新范围 | 版本号、更新、模块 |
| `global-config-reference.md` | global_config.json 配置项手册 — 全部 15 个配置组的字段、默认值与说明 | 全局配置、config、参数 |
| `user-config-reference.md` | user_config.json 配置项手册 — 10 个配置组的字段、默认值与说明，含白名单规则和与全局配置的关系 | 用户配置、provider、白名单 |

## 检索规则

1. 索引按用户级、共享级、全局级顺序完整注入；知识正文检索由外部 kemo-graph 能力负责。
2. 用户私有信息默认写入用户级知识库。
3. 全局库只存放共享说明或用户明确要求的内容。
4. 不把大段正文复制到索引。
5. 文件发生增删改移后必须同步本索引。
