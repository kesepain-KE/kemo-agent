# global_config.json 配置项手册

> 文档版本：v2.1
> 最后核对：2026-07-23
> 事实来源：`config/global_config.json`、`run/config.py`、`run/engine.py`、`run/prompt.py`、`run/runtime_host.py`

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
| `timeout` | int | `240` | 单次工具调用的超时秒数（秒） |
| `max_iterations` | int | `80` | 单轮对话中 Provider 迭代的最大次数。该值统计 Provider 迭代次数，不等同于工具卡片数量。达到上限后抛出 `EngineError` |
| `consecutive_identical_call_limit` | int | `8` | 同一工具使用完全相同参数连续请求的允许次数；工具或参数变化后重新计数。超过后阻止继续执行 |

> 用户配置 `tools` 为对象深合并，可覆盖其中任意字段。

---

## history — 对话历史

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | int | `1` | 历史数据格式版本号 |
| `recent_full_rounds` | int | `3` | 保留完整工具日志和思考过程的最近轮数。超过此数的旧轮次会被 `context_manage` 逐轮压缩 |
| `consecutive_tool_fail_limit` | int | `5` | 同名工具连续失败的容忍上限。达到后本轮从 Provider 工具 schema 中临时移除该工具；其他工具穿插执行会重置连续失败计数 |

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
| `important_memory_max_chars` | int | `5000` | 临时重要记忆文件最大字符数。超出后需 `memory_temporary_important` 子代理压缩 |
| `history_read_enabled` | bool | `true` | 是否允许智能体使用 `history_search` 工具读取历史对话 |

### temporary_injection_limits — 临时记忆注入上限

按**文件数量**截断，优先保留高权重层级。仅限制单次 Prompt 注入，不限制磁盘存储数量。

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
- 仅在记忆被 `self_improve` 命中、正文实际修改或被 Prompt 实际引用时可加权
- 同一记忆每天合计最多 `+1`（每日锁）
- 晋升后新层级权重从 0 重新累计
- 到期未达阈值的直接删除，不降级保留
- 永久记忆没有索引、权重或到期时间

---

## agent_runtime — 智能体运行时

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `queue_maxsize` | int | `50` | 用户级 `AgentScheduler` 的有界队列最大长度；0 表示无界 |
| `default_timeout` | int | `600` | 子代理执行默认超时秒数 |

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
| `important_memory_review_hours` | int | `3` | 临时重要记忆定时巡检间隔（小时）。由 cron 模块的 recurring 任务执行 |
| `daily_memory_review_time` | str | `"02:00"` | 每日记忆审阅执行时间（北京时间 HH:MM）。由 cron 模块的 daily 任务执行 |

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
