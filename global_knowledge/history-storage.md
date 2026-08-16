# 历史对话 SQLite 存储

历史对话以每用户独立的 `users/<user>/history/history.sqlite3` 为唯一权威存储。旧式 `history/data.json`、`history/<window>/*.json` 和 `history/temp/<window>/*.json` 不再参与读取，也不会自动导入。

## 表职责

| 表 | 职责 |
|---|---|
| `history_windows` | archive 完整归档与 runtime 可裁剪上下文窗口；保留 think、tool、items、data 的逻辑边界，runtime 仍内含完整 text 工作区 |
| `history_sessions` | 会话卡片、生命周期、运行态、摘要与记忆任务状态 |
| `history_active_sessions` | Web、CLI 和外部消息入口的活跃会话绑定 |
| `history_messages` | archive 用户/助手正文的唯一权威逐消息存储，用于还原正文、分页和内容搜索；避免与 archive `text_json` 重复保存 |
| `history_rounds` | archive/runtime 的逐轮思考、工具、Item 与用量指标；正常提交只追加新轮，压缩或编辑旧轮次才重建 |
| `history_context_summaries` | runtime 窗口的增量上下文压缩摘要、覆盖轮次与记忆提取元数据 |
| `message_processed_messages` | 外部消息的领取、终态和错误；用于跨重启幂等 |
| `history_meta` | schema 与注册表修订元数据 |

数据库启用 WAL、外键检查、5 秒 busy timeout 和 `synchronous=NORMAL`。schema v3 会把旧 archive `text_json` 正文迁移到 `history_messages`，并把 archive/runtime 的 `round_metrics`、think、tool、items 大分区迁移到 `history_rounds`；窗口 JSON 列只保留小型存储引用。正常追加一轮时只插入新增消息后缀和一个新增 round 行；只有删除、裁剪或编辑旧轮次时才重建对应窗口的增量行。每轮终态的 archive、runtime、上下文摘要、会话索引和活跃绑定在同一个 SQLite 事务中提交，不存在跨表半提交状态；archive 与 runtime 的逻辑路径仍由运行时作为稳定标识使用，但不代表磁盘目录真实存在。

## 性能边界

- Web 历史列表默认跨 `web`、`cli`、`message:<platform>` 等来源读取当前用户的全部会话，并保留来源标签；分页默认每页 50 条、最大 100 条，使用由 `updated_at + session_id + source` 组成的不透明复合游标加载更早记录。
- Web 会话仍可切换和管理；CLI 与外部消息会话在网页中只读展示，避免网页接管仍由其他入口持有的活跃会话。写接口继续强制 `source=web`。
- 会话卡片返回记忆状态、已处理轮次、目标轮次、排队原因和最近失败信息。所有入口绑定到同一内部用户后，共用该用户的 `memory.sqlite3`，记忆页不按渠道过滤。
- 标题、摘要、会话 ID 和消息正文搜索直接查询表，不遍历历史目录。
- 活跃会话通过主键绑定恢复，不在启动阶段扫描全部归档。
- 完整正文只在打开指定会话、上下文构建、摘要或记忆整理时读取。
- 记忆处理状态使用 archive `data_json` 与 `history_sessions` 的小范围事务更新，不再重写 text/think/tool/items 大分区。

## 运维规则

- 不直接编辑数据库或把 JSON 文件放入 history 目录尝试恢复历史。
- 删除会话必须同时清理 session、window、message 和 active 记录，应调用框架 API。
- 运行中备份使用 SQLite backup API 或先完整停止框架；只复制 `history.sqlite3` 而漏掉 `-wal` 可能得到不完整快照。
- schema v3 迁移释放的旧 JSON 页会先进入 SQLite freelist 并被后续写入复用；为避免升级时长时间独占数据库，框架不会自动执行 `VACUUM`。确需立即缩小物理文件时，应停止框架、完成备份后再由运维显式执行。
- 上下文摘要不是历史正文，但与 runtime 窗口裁剪必须在同一事务提交；不要重新创建 `context_summary.json`。
- schema 不兼容时应明确失败或执行专用迁移，禁止静默混读两套历史格式。
