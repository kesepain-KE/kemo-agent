# 历史对话 SQLite 存储

历史对话以每用户独立的 `users/<user>/history/history.sqlite3` 为唯一权威存储。旧式 `history/data.json`、`history/<window>/*.json` 和 `history/temp/<window>/*.json` 不再参与读取，也不会自动导入。

## 表职责

| 表 | 职责 |
|---|---|
| `history_windows` | archive 完整归档与 runtime 可裁剪上下文窗口；五个 JSON 列保留 text、think、tool、items、data 的逻辑边界 |
| `history_sessions` | 会话卡片、生命周期、运行态、摘要与记忆任务状态 |
| `history_active_sessions` | Web、CLI 和外部消息入口的活跃会话绑定 |
| `history_messages` | 用户/助手正文的逐消息索引，用于分页和内容搜索 |
| `history_context_summaries` | runtime 窗口的增量上下文压缩摘要、覆盖轮次与记忆提取元数据 |
| `message_processed_messages` | 外部消息的领取、终态和错误；用于跨重启幂等 |
| `history_meta` | schema 与注册表修订元数据 |

数据库启用 WAL、外键检查、5 秒 busy timeout 和 `synchronous=NORMAL`。窗口提交在单个事务中完成，不存在旧五文件部分写入状态；archive 与 runtime 的逻辑路径仍由运行时作为稳定标识使用，但不代表磁盘目录真实存在。

## 性能边界

- Web 会话列表默认每页 50 条，最大 100 条，使用由 `updated_at + session_id` 组成的不透明复合游标加载更早记录；时间戳相同的会话不会漏项或重复。
- 标题、摘要、会话 ID 和消息正文搜索直接查询表，不遍历历史目录。
- 活跃会话通过主键绑定恢复，不在启动阶段扫描全部归档。
- 完整正文只在打开指定会话、上下文构建、摘要或记忆整理时读取。

## 运维规则

- 不直接编辑数据库或把 JSON 文件放入 history 目录尝试恢复历史。
- 删除会话必须同时清理 session、window、message 和 active 记录，应调用框架 API。
- 运行中备份使用 SQLite backup API 或先完整停止框架；只复制 `history.sqlite3` 而漏掉 `-wal` 可能得到不完整快照。
- 上下文摘要不是历史正文，但与 runtime 窗口裁剪必须在同一事务提交；不要重新创建 `context_summary.json`。
- schema 不兼容时应明确失败或执行专用迁移，禁止静默混读两套历史格式。
