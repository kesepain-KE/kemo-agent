# 存储与持久化功能说明

本文集中说明历史、记忆、运行状态、结构化日志和高频写盘的当前持久化规则。各章节保持独立职责，代码和数据库实现是事实来源。

## 历史对话 SQLite 存储

历史对话以每用户独立的 `users/<user>/history/history.sqlite3` 为唯一权威存储。旧式 `history/data.json`、`history/<window>/*.json` 和 `history/temp/<window>/*.json` 不再参与读取，也不会自动导入。

### 表职责

| 表 | 职责 |
|---|---|
| `history_windows` | archive 完整归档与 runtime 可裁剪上下文窗口；保留 think、tool、items、data 的逻辑边界，runtime 仍内含完整 text 工作区 |
| `history_sessions` | 会话卡片、生命周期、运行态、摘要与记忆任务状态 |
| `history_active_sessions` | Web、CLI 和外部消息入口的活跃会话绑定 |
| `history_deleted_sessions` | 已删除会话的持久删除栅栏，阻止迟到 Run 重新写入 |
| `history_deleted_windows` | 已删除物理窗口的 tombstone，阻止迟到 Run 复用旧窗口名；正常新窗口仍可使用当前会话 generation |
| `history_messages` | archive 用户/助手正文的唯一权威逐消息存储，用于还原正文、分页和内容搜索；避免与 archive `text_json` 重复保存 |
| `history_rounds` | archive/runtime 的逐轮思考、工具、Item 与用量指标；正常提交只追加新轮，压缩或编辑旧轮次才重建 |
| `history_context_summaries` | runtime 窗口的增量上下文压缩摘要、覆盖轮次与记忆提取元数据 |
| `message_processed_messages` | 外部消息的领取、终态和错误；用于跨重启幂等 |
| `history_meta` | schema 与注册表修订元数据 |

数据库启用 WAL、外键检查、5 秒 busy timeout 和 `synchronous=NORMAL`。schema v3 会把旧 archive `text_json` 正文迁移到 `history_messages`，并把 archive/runtime 的 `round_metrics`、think、tool、items 大分区迁移到 `history_rounds`；schema v4 增加 `history_deleted_sessions` 删除栅栏；schema v5 增加 `history_deleted_windows` 物理窗口 tombstone。窗口 JSON 列只保留小型存储引用。正常追加一轮时只插入新增消息后缀和一个新增 round 行；只有删除、裁剪或编辑旧轮次时才重建对应窗口的增量行。每轮终态的 archive、runtime、上下文摘要、会话索引和活跃绑定在同一个 SQLite 事务中提交，不存在跨表半提交状态；archive 与 runtime 的逻辑路径仍由运行时作为稳定标识使用，但不代表磁盘目录真实存在。

### 性能边界

- Web 历史列表默认跨 `web`、`cli`、`message:<platform>` 等来源读取当前用户的全部会话，并保留来源标签；分页默认每页 50 条、最大 100 条，使用由 `updated_at + session_id + source` 组成的不透明复合游标加载更早记录。
- Web 会话仍可切换和管理；CLI 与外部消息会话在网页中只读展示，避免网页接管仍由其他入口持有的活跃会话。写接口继续强制 `source=web`。
- 会话卡片返回记忆状态、已处理轮次、目标轮次、排队原因和最近失败信息。所有入口绑定到同一内部用户后，共用该用户的 `memory.sqlite3`，记忆页不按渠道过滤。
- 标题、摘要、会话 ID 和消息正文搜索直接查询表，不遍历历史目录。
- 活跃会话通过主键绑定恢复，不在启动阶段扫描全部归档。
- 完整正文只在打开指定会话、上下文构建、摘要或记忆整理时读取。
- 记忆处理状态使用 archive `data_json` 与 `history_sessions` 的小范围事务更新，不再重写 text/think/tool/items 大分区。

### 运维规则

- 不直接编辑数据库或把 JSON 文件放入 history 目录尝试恢复历史。
- 删除会话必须同时清理 session、window、message 和 active 记录，应调用框架 API。框架会额外留下短小的 `history_deleted_sessions` 栅栏，防止删除发生时已经在运行的旧 Run 迟到提交；只有显式重新预留同一逻辑 ID 时才会清除栅栏。
- 运行中备份使用 SQLite backup API 或先完整停止框架；只复制 `history.sqlite3` 而漏掉 `-wal` 可能得到不完整快照。
- schema v3/v4/v5 迁移释放的旧 JSON 页会先进入 SQLite freelist 并被后续写入复用；为避免升级时长时间独占数据库，框架不会自动执行 `VACUUM`。确需立即缩小物理文件时，应停止框架、完成备份后再由运维显式执行。
- 上下文摘要不是历史正文，但与 runtime 窗口裁剪必须在同一事务提交；不要重新创建 `context_summary.json`。
- schema 不兼容时应明确失败或执行专用迁移，禁止静默混读两套历史格式。

---

## 记忆 SQLite 存储

四档记忆以每用户独立的 `users/<user>/improve/memory.sqlite3` 为唯一权威存储。旧式 Markdown 碎片、三层 `data.json`、`storage.json`、`.memory_operations.json` 和 `important_view.json` 不再参与读取，也不会自动导入。`memory_temporary_important.md` 仍是位于用户根目录的可重建热视图，不是权威数据库或第五个生命周期层。

### 为什么独立建库

记忆与历史采用两个数据库：`improve/memory.sqlite3` 和 `history/history.sqlite3`。二者的写入频率、保留周期和维护任务不同，分库可避免记忆加权与聊天归档争用同一写锁，也便于单独备份、诊断和重建。

数据库启用 WAL、`synchronous=NORMAL`、外键、5 秒 busy timeout 和显式写事务。连接、Schema 与业务规则统一归入 `run/memory/` 领域，生产代码只通过该领域入口的条目级接口读取或修改记忆，不再提供模拟旧 `data.json` 的 `load_index`、`write_index`、层级目录或正文文件路径接口，外部代码不得直接拼 SQL 修改生命周期状态。

### 表结构

| 表 | 用途 | 关键约束 |
|---|---|---|
| `memory_meta` | schema 与派生视图元数据 | `key` 主键 |
| `memory_fragments` | 四档正文和生命周期 | `filename_key` 全局唯一；tier 枚举；权重非负；永久层无 `expires_at` |
| `memory_weight_events` | 用户历史证据导致的加权记录 | `(fragment_id, evidence_date)` 主键，数据库强制每日最多一次 |
| `memory_operations` | 批量提取幂等结果 | `operation_id` 主键，重复批次只重放结果 |
| `memory_important_sources` | 临时重要热视图引用 | 每个临时源最多一条，并保存内容摘要用于失效判断 |

`filename` 保留 `.md` 后缀只是稳定的逻辑身份和 UI 展示合同，不表示磁盘上存在对应 Markdown。跨层同名由数据库唯一约束拒绝，晋升直接更新同一行的 tier，不复制正文文件。

### 生命周期与事务

- 新的非明确候选进入 `seven_days`；用户明确要求长期记住的候选可直接进入 `permanent`。
- 同名临时候选更新正文后，生命周期到期时间不滑动；用户历史证据在当天第一次命中时写入 `memory_weight_events` 并使权重加一。
- 到期且达到阈值时，在一个事务内更新 tier、重置权重和加权事件、写入新的固定到期时间；未达阈值则事务化删除。
- 语义融合会在同一事务内更新目标正文和生命周期、删除来源；任何一步失败都会回滚。
- 永久层不累计权重且无到期时间，普通非明确候选不能覆盖永久正文。

Prompt 注入、Web 浏览、`memory_manage` 搜索和用户主动查看均为只读操作，不写加权事件。只有保存、手动压缩、Token 超限压缩等用户对话历史整理管线能提供临时记忆加权证据。

### 搜索命中与每日加权

`self_improve` 先把每个候选提炼为 2～4 个核心关键词，再通过一次 `memory_manage search_many tier=all`
搜索整批候选。搜索优先规范化完整短语；模糊查询使用英文词、中文关键词和 bigram 计算覆盖率，
多片段查询至少命中两个有效片段且覆盖率不低于 50%，单个较长中文片段要求至少两个 bigram
且覆盖率不低于 60%。单个公共词不能直接证明两个碎片语义相同。

搜索结果返回 `match_score`、`match_coverage`、`matched_by`、`matched_terms` 和 `exact_match`。
子代理必须结合命中正文确认语义一致，并复制已有 `filename` 返回同名 upsert；数据库随后利用
`memory_weight_events` 保证每日最多加权一次。只有高置信度候选均不相符时才创建新文件。

`search_many` 在一次调用中只加载一次目标记忆层，再在内存中处理最多 20 个查询，不会随候选
数量重复访问四层数据库。该路径面向数百至数千碎片；单项搜索同样使用相同评分规则和全局排序。

### 临时重要热视图

数据流固定为：

```text
用户对话历史 → 临时三层表行 → memory_temporary_important.md
```

后台整理临时三层时禁止把 `memory_temporary_important.md` 作为输入证据。热视图发布时，`memory_important_sources` 记录来源行和内容摘要；该关系只负责热视图失效判断，不过滤普通临时记忆的 Prompt 注入。来源碎片始终在原层按数量上限正常注入，热视图只作为更高优先级的强化概括。来源被修改、删除或晋升后，旧热视图立即视为失效，普通临时层继续正常注入，等待下次巡检重建。主动查看记忆时仍可读取该文件，但查看本身不加权。

热视图巡检不能一次性返回整个大型层级。`memory_temporary_important` 使用 `memory_manage list(limit=500, offset, compact=true, include_content=true, page_char_limit=80000)` 按“条目数 + 序列化字符数”双重边界分页读取三层临时记忆和永久记忆，正文随列表批量返回，不再对全部碎片逐条 `get`。沿 `next_offset` 读取到 `has_more=false` 且累计数量等于 `total` 后才能发布新热视图；否则必须保持旧视图，不得基于局部数据执行永久协调或清理。

热视图文件正文尽量保持在 `important_memory_max_chars`（默认 20000）以内，该值只控制 Prompt
注入预算。`important_memory_output_max_chars` 是语义独立的输出防失控硬上限，当前默认同为
20000；超过输出上限时拒绝本次更新且保留旧视图。

### 模板、升级与旧格式

`template/user/improve/` 只包含 `.gitkeep`，不得提交预生成的二进制数据库。`user_create.py` 在真实用户目录落地后初始化 schema；更新器也只初始化缺失数据库，不导入或删除部署者的旧文件。当前源代码旧记忆已被抛弃；需要保留旧部署数据时，应在升级前自行导出，不能假定框架会自动迁移。

### 备份与维护

- 最稳妥的备份方式是停止写入后复制整个 `improve/`，或在运行中使用 SQLite backup API。
- WAL 模式运行时不能只复制 `memory.sqlite3` 而忽略尚未 checkpoint 的 WAL。
- 不提交 `memory.sqlite3`、`-wal` 或 `-shm` 到模板或 Git。
- 完整性检查使用 `MemoryStore.integrity_issues()`；不要通过手工编辑数据库修复生产数据。

---

## 运行状态 SQLite 存储

框架把高频变化、需要事务一致性或索引查询的状态分配到三个 SQLite 数据库。配置、附件、模块清单和持久消息队列仍保留文件形式，不能为了统一而盲目入表。

### 数据库分工

| 数据库 | 表 | 权威数据 |
|---|---|---|
| `users/<user>/history/history.sqlite3` | `history_context_summaries` | runtime 上下文摘要、绝对覆盖轮次与摘要内存提取元数据 |
| 同上 | `message_processed_messages` | 外部消息幂等键的 processing/completed/failed 状态 |
| `users/<user>/task_plan/task_plans.sqlite3` | `task_plans`、`task_plan_steps`、`task_plan_dependencies` | 任务计划元数据、步骤和有序依赖 |
| `runtime/logs.sqlite3` | `message_route_state` | 外部消息模块健康、计数、延迟和输入线程状态 |

上下文摘要与对应 runtime 窗口裁剪在一个 `history.sqlite3` 写事务中提交。手动压缩只有在窗口轮数、偏移、摘要哈希和覆盖轮次全部回读一致后才报告成功；失败时同一事务恢复旧窗口和摘要。

外部消息领取使用 `BEGIN IMMEDIATE`：批次内任一幂等键已存在时整批拒绝，不留下半批 processing。保留上限只淘汰最旧终态记录，绝不删除处理中记录；宿主启动时把遗留 processing 改为 failed，避免重复执行副作用。

任务计划通过 revision 防止旧副本覆盖新状态。步骤参数、结果和错误允许以表内 JSON 列保存任意结构，但步骤身份、顺序、状态及依赖必须是可索引行。

### 文件边界

- `message.md` 与 `.processing.md` 是插件和核心之间的持久消息队列。
- 附件目录只保存消息附件，不保存结构化状态。
- `template/task_plan/plan_template.json` 只说明任务输入 Schema，不复制到用户目录。
- Cron 定义、用户配置、模块清单与资源文件继续按各自合同存储。

运行时不扫描其他文件格式来迁移、回退读取或双写这些状态。更新器只初始化当前数据库 Schema。

在线备份必须使用 SQLite backup API；离线复制应先完整停止框架，并连同 WAL 相关文件一起处理。不要直接编辑数据库。

---

## 结构化运行日志存储

### 单一事实来源

运行日志统一存放在项目运行目录的 `runtime/logs.sqlite3`：

| 类型 | 表 | 内容 |
|------|----|------|
| Cron 执行记录 | `cron_execution_logs` | 用户、任务、时间、状态、耗时、结果摘要与错误 |
| 外部消息路由记录 | `message_route_logs` | 入站、出站、附件元数据与失败状态 |
| 外部消息模块状态 | `message_route_state` | 健康、收发计数、延迟、输入线程状态与平台扩展字段 |

进程启动和重启等纯诊断输出仍可使用普通文本日志。Cron 任务定义仍是任务配置文件；附件仍在模块附件目录。运行日志数据库不是聊天历史库，也不保存附件二进制。

### 写入与查询规则

数据库使用 Python 标准库 `sqlite3`，启用 WAL、忙等待、事务和时间索引，允许 RuntimeHost、Cron、消息路由与 Web 并发访问。网页只查询表，不扫描模块目录或 Cron 目录寻找日志文件。

每个事件根据稳定业务字段生成幂等键，重复提交不会制造重复行。写入失败属于可观测性故障，不得重放已经执行的任务或已经发送的消息；路由状态事务失败则必须显式报告，不能伪装为状态已更新。

默认保留 90 天，可通过 `KEMO_LOG_RETENTION_DAYS` 修改；设置为 `0` 表示不自动清理。清理只删除事件表中的过期记录，不删除聊天历史、任务定义、消息队列或附件。

### 安全与维护

消息正文和外部平台标识属于敏感运行数据。`runtime/` 必须保持 Git 忽略，部署时应限制数据库文件的本地访问权限，不得把 Token、Cookie 或平台密钥写入日志。

在线备份应使用 SQLite backup API；离线复制必须先完整停止框架，并连同 WAL 相关文件一起处理。不要直接编辑数据库。

---

## Python 持久化写盘策略

框架采用“运行态内存优先、语义变化写入、终态事务提交”的持久化策略。它减少高频 Python 任务对 JSON、Markdown 与 SQLite 的重复覆盖，但不延迟错误、取消、对话终态和用户主动操作。

### 高频系统任务

- 感知刷新、拓展刷新和记忆晋升检查仍按原调度频率执行。
- `latest_run_at`、`next_run_at` 与运行状态先保存在进程内；默认每 300 秒、发生错误或 RuntimeHost 正常停止时写回任务 JSON。
- 高频任务的正常完成和无模块可运行的 `skipped` 结果按用户与任务聚合，默认每 300 秒写一条汇总日志；失败和部分失败立即写入。
- 聚合日志保留运行次数、平均/最大耗时、窗口起止时间与最后一次有界结果摘要，不保存 Prompt 或大正文。

### 感知与拓展采集

- 采集进程仍按设定频率运行，System Prompt 读取的仍是最新已采集结果。
- 数据 Markdown、JSON 和二进制图表只有在语义内容改变时才原子替换。
- 健康状态发生变化时立即写清单；健康状态稳定时最多每 300 秒更新一次检查点。
- 新建感知/拓展模板不再把当前秒级时间写进注入正文，避免“数据没变、文件必变”。

### 对话与记忆

- archive 正文由 `history_messages` 唯一保存；逐轮的思考、工具、Item 与指标由 `history_rounds` 保存。schema v3 会自动迁移旧窗口 JSON，不丢失消息或轮次分区；schema v4 会创建删除会话栅栏，schema v5 会创建物理窗口 tombstone，不需要人工改库。
- 正常提交新一轮只追加新增消息后缀和一个 round 行；编辑、撤回、裁剪旧正文或压缩 runtime 时才显式重建对应窗口的增量行。
- 一轮终态的 archive、runtime、上下文摘要、会话索引与活跃绑定在同一个 SQLite 事务中提交。任一部分失败会整体回滚。
- 记忆完成、失败、排队等状态只更新 archive `data_json` 和会话索引，不重写消息、思考、工具与 Item 大分区。

### 不进入内存延迟的边界

以下内容仍立即持久化：错误与部分失败日志、对话成功/取消/失败终态、用户主动创建或修改任务、文件和媒体产物、外部消息幂等状态、记忆碎片正文，以及需要跨重启继续的任务声明。
