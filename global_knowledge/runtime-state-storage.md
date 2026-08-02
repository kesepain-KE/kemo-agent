# 运行状态 SQLite 存储

框架把高频变化、需要事务一致性或索引查询的状态分配到三个 SQLite 数据库。配置、附件、模块清单和持久消息队列仍保留文件形式，不能为了统一而盲目入表。

## 数据库分工

| 数据库 | 表 | 权威数据 |
|---|---|---|
| `users/<user>/history/history.sqlite3` | `history_context_summaries` | runtime 上下文摘要、绝对覆盖轮次与摘要内存提取元数据 |
| 同上 | `message_processed_messages` | 外部消息幂等键的 processing/completed/failed 状态 |
| `users/<user>/task_plan/task_plans.sqlite3` | `task_plans`、`task_plan_steps`、`task_plan_dependencies` | 任务计划元数据、步骤和有序依赖 |
| `runtime/logs.sqlite3` | `message_route_state` | 外部消息模块健康、计数、延迟和输入线程状态 |

上下文摘要与对应 runtime 窗口裁剪在一个 `history.sqlite3` 写事务中提交。手动压缩只有在窗口轮数、偏移、摘要哈希和覆盖轮次全部回读一致后才报告成功；失败时同一事务恢复旧窗口和摘要。

外部消息领取使用 `BEGIN IMMEDIATE`：批次内任一幂等键已存在时整批拒绝，不留下半批 processing。保留上限只淘汰最旧终态记录，绝不删除处理中记录；宿主启动时把遗留 processing 改为 failed，避免重复执行副作用。

任务计划通过 revision 防止旧副本覆盖新状态。步骤参数、结果和错误允许以表内 JSON 列保存任意结构，但步骤身份、顺序、状态及依赖必须是可索引行。

## 文件边界

- `message.md` 与 `.processing.md` 是插件和核心之间的持久消息队列。
- 附件目录只保存消息附件，不保存结构化状态。
- `template/task_plan/plan_template.json` 只说明任务输入 Schema，不复制到用户目录。
- Cron 定义、用户配置、模块清单与资源文件继续按各自合同存储。

运行时不扫描其他文件格式来迁移、回退读取或双写这些状态。更新器只初始化当前数据库 Schema。

在线备份必须使用 SQLite backup API；离线复制应先完整停止框架，并连同 WAL 相关文件一起处理。不要直接编辑数据库。
