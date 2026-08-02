# global_config.json 配置项手册

> 文档版本：v2.2
> 最后核对：2026-07-25
> 事实来源：`config/global_config.json`、`run/config.py`、`run/engine.py`、`run/conversation_runtime.py`、`run/context_service.py`、`run/prompt.py`、`run/runtime_host.py`

kemo-agent 全局配置文件，位于 `config/global_config.json`。所有用户共享这些默认值，用户级 `user_config.json` 可覆盖其中非 `USER_ONLY_SECTIONS` 的字段。

修改配置前先查本手册；部署级密钥和 Web 启动参数见 `env-reference.md`，用户覆盖规则见 `user-config-reference.md`。未知字段是否生效以当前加载器和使用方代码为准，不应仅因 JSON 能保存就视为受支持。

---

## schema_version

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | int | `1` | 配置文件结构版本号，用于格式升级时的兼容判断 |

---

## provider_runtime — Provider 运行时并发控制

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_concurrent_requests` | int | `10` | 进程级总闸，Web、外部消息、Cron、维护任务和子代理共享；每一次真实 LLM API 请求独立占槽，工具执行期间释放槽位 |
| `request_semaphore_timeout` | float | `300.0` | 获取并发槽位的超时秒数；超时后抛出 `ProviderCongestionError` |

> 用户 `user_config.json` 中独立配置的 `provider_runtime` 会覆盖此全局值。

---

## tools — 工具调用

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 全局工具开关。`false` 时所有 Provider 工具 schema 均不注册，智能体只能纯文本对话 |
| `timeout` | int | `240` | 单次工具调用未提供 `timeout` 参数时的默认秒数；工具 Schema 声明且调用方显式提供有效 `timeout` 时，以显式值为准，插件内部期限与框架看门狗使用同一有效值 |
| `max_iterations` | int | `80` | 单轮对话中 Provider 迭代的最大次数。该值统计 Provider 迭代次数，不等同于工具卡片数量。达到上限后抛出 `EngineError` |
| `consecutive_identical_call_limit` | int | `8` | 同一工具使用完全相同参数连续请求的允许次数；工具或参数变化后重新计数。超过后阻止继续执行 |

> 用户配置 `tools` 为对象深合并，可覆盖其中任意字段。

单次工具内联 JSON 结果有不可配置的 20,000 字符核心硬限制。超限时框架丢弃正文，只向智能体、事件和历史写入 `ToolResultTooLargeError`、原始字符数与缩小范围提示；文件内容应改用 `file.stat` 和 `file.read_range` 分段读取。该受控拒绝不计入 `consecutive_tool_fail_limit`。

---

## history — 对话历史

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | int | `1` | 历史数据格式版本号 |
| `recent_full_rounds` | int | `3` | 保留完整工具日志和思考过程的最近轮数。超过此数的旧轮次会被 `context_manage` 逐轮压缩 |
| `consecutive_tool_fail_limit` | int | `5` | 同名工具连续失败的容忍上限。达到后本轮从 Provider 工具 schema 中临时移除该工具；其他工具穿插执行会重置连续失败计数 |

---

## history_summary — 历史摘要后台调度

历史摘要使用独立持久调度线程，不占用 Web 请求线程，也不进入有容量上限的子代理内存队列。会话关闭并排队后，即使用户关闭网页，任务仍会由运行中的 RuntimeHost 继续处理。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `poll_interval` | number | `5` | 持久摘要任务扫描间隔（秒），最小为 1 |
| `max_jobs_per_cycle` | int | `1` | 每个扫描周期最多领取的摘要任务数；多用户之间轮转领取，避免单用户长期独占 |
| `max_attempts` | int | `5` | 单个摘要任务的自动尝试上限；达到上限后进入 `exhausted`，等待用户手动重试 |
| `retry_delays_seconds` | int[] | `[30, 120, 600, 1800]` | 各次失败后的退避时间；超出数组长度时沿用最后一个值。Provider 拥塞和服务停止只推迟任务，不消耗失败次数 |

长对话按保守 Token 预算分块生成摘要；即使单轮正文超过预算，也会无损拆成多个片段。每块完成后写入持久断点，进程重启或中途失败后从最近断点继续，不重复处理已经成功的分块。

摘要结果优先通过受 Schema 约束的内部提交工具返回，不依赖模型自行拼写 JSON。若 Provider 不支持或模型没有遵循工具调用，系统会依次兼容文本 JSON、带“标题/摘要”标签的普通文本，并在连续格式失败时生成不含敏感凭据的本地保底卡片；Provider 网络、鉴权或服务错误仍进入持久重试，不会被格式保底掩盖。修改本段配置后需要重启运行时宿主才能应用到调度线程。

---

## prompt — System Prompt 注入控制

### char_limits — 各 Prompt 段字符上限

所有上限均为注入时的截断保护，超过上限的内容会被截断。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `task_plan` | `6000` | 任务计划段注入上限 |
| `perception` | `8000` | 感知模块数据注入上限 |
| `expand_data` | `10000` | 拓展模块数据注入上限 |
| `skill_prompts` | `8000` | 技能提示词（共享技能 + 用户技能）注入上限 |
| `plugin_prompts` | `10000` | 插件提示词注入上限 |

> 以下 Prompt 段不使用 char_limits 字段控制：用户人格、全局人格、运行手册、子代理注册、知识库索引、四层记忆（永久 + 重要 + 三层临时）分别由各自独立的字符控制逻辑管理。

### injection_mode — 各模块注入模式

全部默认 `"full"`（全文注入）。可选值：`"full"`（全文）/ `"truncated"`（截断）/ `"off"`（关闭）。

| 字段 | 说明 |
|------|------|
| `permanent_memory` | 永久记忆注入模式 |
| `important_memory` | 临时重要记忆注入模式 |
| `temporary_seven_days` | 7 天层临时记忆注入模式 |
| `temporary_one_month` | 30 天层临时记忆注入模式 |
| `temporary_half_year` | 180 天层临时记忆注入模式 |
| `knowledge_index` | 知识库索引注入模式 |
| `task_plan` | 任务计划注入模式 |
| `expand_data` | 拓展数据注入模式 |
| `perception` | 感知数据注入模式 |

> 用户 `user_config.json` 可独立配置 `prompt.char_limits` 和 `prompt.injection_mode`，覆盖全局默认值。

---

## kemo_graph — 知识图谱检索开关

全部默认 `false`。只有当 `global_expand/` 中接入 kemo-graph 模块后才实际生效。

| 字段 | 说明 |
|------|------|
| `kemo_graph_global_knowledge` | 全局知识库是否切换为图谱检索 |
| `kemo_graph_shared_knowledge` | 共享知识库是否切换为图谱检索 |
| `kemo_graph_user_knowledge` | 用户知识库是否切换为图谱检索 |
| `kemo_graph_temporary_memory` | 三层临时记忆（half_year、one_month、seven_days）是否切换为图谱检索 |

> 注意：全局配置的开关决定"图谱数据是否存在"，用户配置（`user_config.json → kemo_graph`）的开关决定"该用户是否启用"。两者都开才生效。任一开关为 true 但未建立连接时返回 `not_connected`，不自动回退原始内容。

---

## memory — 记忆系统

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `storage_schema_version` | int | `3` | 记忆存储格式版本号 |
| `extraction_mode` | string | `compression_only` | 记忆提取模式：`disabled` 完全关闭；`compression_only` 仅上下文压缩/保存时提取；`background` 允许 Maintenance 每轮后台提取；`on_commit` 每轮同步提取 |
| `recovery_max_rounds_per_scan` | int | `10` | Maintenance 每轮扫描最多补提取的总轮数。运行时限制为 1–20 |
| `extraction_batch_rounds` | int | `5` | 一次 `self_improve` 模型运行最多分析的连续轮数。运行时限制为 1–20 |
| `extraction_max_candidates_per_batch` | int | `10` | 每批最多保留的记忆候选；同时受“每轮最多 2 条”限制，运行时硬上限为 40 |
| `important_memory_max_chars` | int | `5000` | 临时重要热画像最大字符数。热画像是临时碎片的可重建派生视图，不改变源碎片生命周期；超出后由 `memory_temporary_important` 精简 |
| `history_read_enabled` | bool | `true` | 是否允许智能体使用 `history_search` 工具读取历史对话 |

### temporary_injection_limits — 临时记忆注入上限

按**文件数量**截断，优先保留高权重层级。仅限制单次 Prompt 注入，不限制磁盘存储数量。

已被 `improve/important_view.json` 有效引用的临时碎片由热画像段承载，普通临时记忆段会跳过这些来源，避免同一事实重复注入。源文件仍保留，但只能在保存/压缩的用户对话历史整理中加权，并正常到期和晋升；任一来源发生变化时旧热画像暂停注入，直到下次巡检重建。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `half_year` | `100` | 半年层最多注入 100 个记忆文件 |
| `one_month` | `200` | 月层最多注入 200 个记忆文件 |
| `seven_days` | `300` | 周层最多注入 300 个记忆文件 |

### tiers — 记忆层级与晋升规则

| 层级 | `days` | `upgrade_threshold` | `next` | 说明 |
|------|-------|---------------------|--------|------|
| `seven_days` | 7 | 3 | `one_month` | 7 天到期，权重 ≥ 3 晋升到月层 |
| `one_month` | 30 | 10 | `half_year` | 30 天到期，权重 ≥ 10 晋升到半年层 |
| `half_year` | 180 | 60 | null | 180 天到期，权重 ≥ 60 晋升到永久记忆 |

权重规则：
- 仅在保存、手动压缩、Token 超限压缩等历史整理管线中，被 `self_improve` 依据用户原文命中时可加权
- Prompt 注入、用户查看和工具检索不加权；后台整理临时三层时不得读取 `important`
- 同一记忆每天合计最多 `+1`（每日锁）
- 晋升后新层级权重从 0 重新累计
- 到期未达阈值的直接删除，不降级保留
- 永久记忆没有索引、权重或到期时间

---

## agent_runtime — 智能体运行时

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `queue_maxsize` | int | `50` | 用户级 `AgentScheduler` 的有界队列最大长度；0 表示无界 |
| `default_timeout` | int | `600` | 子代理整体执行默认超时秒数；达到期限后自动发送协作式取消并等待清理，同步调度不会被普通工具默认超时提前截断 |

> 注意：每个用户的 `AgentScheduler` 使用实例级串行锁和独立有界队列，不再由一个进程级锁串行所有用户。

---

## task_plan — 任务计划

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_steps` | int | `20` | 单个任务计划的最大步骤数 |

> 用户配置的 `task_plan.auto_accept` 控制是否自动执行计划；全局配置不包含此字段。

---

## cron — 定时调度

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用 cron 调度器 |
| `poll_interval` | int | `30` | 任务轮询间隔（秒）。运行时会自动取它与 `sense_update_rate`、`expand_update_rate` 的最小值，保证短周期任务按时被扫描 |
| `avoid_congestion` | bool | `true` | 是否启用 Provider 拥塞避免 |
| `congestion_threshold_ratio` | float | `0.2` | 拥塞阈值比例。当 Provider 可用槽位低于此比例时，推迟普通用户任务和重型系统任务；全局感知/拓展采集不退避 |

---

## task_cron_system — 系统定时任务

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `sense_update_rate` | int | `5` | 全局感知模块数据刷新间隔（秒）。缺失或非法值回退到 5 |
| `expand_update_rate` | int | `5` | 全局、共享和各用户拓展模块的统一刷新间隔（秒）。缺失或非法值回退到 5 |
| `module_update_timeout` | number | `120` | 每个感知/拓展采集脚本的独立子进程超时（秒）；非法值回退到 120，最大 3600 |

> 两个刷新间隔也影响 `cron.poll_interval` 的最小轮询粒度；单模块超时不改变轮询频率。

---

## runtime_host — 运行时宿主

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_background_scheduler` | bool | `true` | 是否在启动时自动开启统一后台调度器。启用时宿主自动管理 Cron 调度、上下文整理、记忆到期晋升检查及感知/拓展数据刷新 |

---

## message — 外部消息路由

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_workers` | int | `8` | 消息处理线程池大小 |
| `max_queued_messages` | int | `20` | 消息等待队列上限。为 0 时保持无界兼容模式；队列满抛出 `MessageQueueFullError` |

> 外部消息总容量 = `max_workers + max_queued_messages`。

---

## web — Web 服务

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_concurrent_chats` | int | `3` | 单用户最大并发聊天数。空闲闸门会自动采用新保存的 Web 限制 |
| `max_pending_chats` | int | `5` | 单用户聊天等待队列上限。队列满或等待超时返回 HTTP 503 |
| `pending_chat_timeout` | float | `30.0` | 排队等待超时秒数 |

---

## agents — 智能体上下文与压缩

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `conserved_rounds` | int | `3` | 保留完整工具日志和思考过程的最近轮数。超出此数的旧轮次由 `context_manage` 逐轮压缩 |
| `max_rounds` | int | `80` | 上下文最大对话轮数。达到后触发压缩。此值限制 temp 工作区及 Provider 上下文的轮次上限，不限制用户可见归档 |
| `rounds_after_compression` | int | `20` | 压缩后保留的轮数 |
| `token_limit` | int | `1000000` | 上下文 Token 上限。预估总 token 超过此值时触发压缩 |
| `token_compression_ratio` | float | `0.3` | 输入预算比例。`input_budget = token_limit × ratio`，超过此比例时触发压缩 |
| `important_memory_review_hours` | int | `3` | 宿主级临时重要记忆巡检间隔（小时）。Cron 据此创建唯一系统 recurring 任务，到期后按用户分别执行；不支持用户级时间表 |
| `daily_memory_review_time` | str | `"02:00"` | 宿主级每日记忆整理时间（北京时间 HH:MM）。Cron 据此创建唯一系统 daily 任务，到期后按用户分别执行；不支持用户级时间表 |

`context_manage` 的摘要输入会合并正文、reasoning/think 和工具结论；核心运行时为每次摘要请求提供最多 20000 tokens 的输出预算，并要求非空、完整的结构化 JSON。网页端手动压缩只有在摘要缓存、temp 工作区轮数、绝对轮次偏移和摘要覆盖范围重新读取校验通过后才报告成功；用户可见归档仍保留完整轮次。

---

## 与 user_config.json 的关系

`global_config.json` 提供全局默认值。用户可在 `user_config.json` 中覆盖部分字段。

| 维度 | global_config.json | user_config.json |
|------|-------------------|-----------------|
| 作用域 | 所有用户 | 单个用户 |
| 覆盖性 | 默认值 | 可覆盖全局值 |
| 用户独有字段 | 无 | `provider`、`agent_models`、`multimodal_models`、`multimodal_routing`、`knowledge`、`skills`、`expand`、`perception`、`plugins`、`agent_runtime`（部分） |
| 修改方式 | 直接编辑文件 | Web 配置面板或直接编辑文件 |

### 白名单规则

用户配置中的白名单字段（`skills.shared_whitelist`、`expand.global_whitelist`、`expand.shared_whitelist`、`perception.global_whitelist`、`plugins.whitelist`）遵循：
- 空数组 `[]` = 全部启用
- 有值 = 只启用列出的项

> 注意：全局配置中不含白名单字段。白名单仅存在于用户配置中。

### 覆盖行为

- 用户配置中的 `provider`、`knowledge`、`skills`、`expand`、`perception`、`plugins` 等段是用户专属，不从全局配置兜底
- 其他框架段按对象深合并：用户配置中的字段覆盖全局默认值，未提供的字段继承全局值
- 修改全局配置后无需重启，下次请求自动生效
