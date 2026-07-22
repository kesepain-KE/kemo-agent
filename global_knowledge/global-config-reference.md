# global_config.json 配置项手册

kemo-agent 全局配置文件，位于 `config/global_config.json`。所有用户共享这些默认值，用户级 `user_config.json` 可覆盖其中非 `USER_ONLY_SECTIONS` 的字段。

---

## schema_version

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | int | `1` | 配置文件结构版本号，用于格式升级时的兼容判断 |

---

## provider_runtime — Provider 运行时

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_concurrent_requests` | int | `10` | Provider 最大并发请求数 |
| `request_semaphore_timeout` | float | `300.0` | 获取并发槽位的超时秒数 |

---

## tools — 工具调用

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用工具调用。关闭后智能体只能纯文本对话 |
| `timeout` | int | `240` | 单次工具调用的超时秒数 |
| `max_iterations` | int | `80` | 单轮对话中工具调用的最大迭代次数（防止死循环） |

---

## history — 对话历史

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | int | `1` | 历史数据格式版本号 |
| `recent_full_rounds` | int | `3` | 保留完整工具日志和思考过程的最近轮数。超过此数的旧轮次会被截短 |
| `consecutive_tool_fail_limit` | int | `5` | 连续工具调用失败的容忍上限。超过后本轮终止 |

---

## prompt — System Prompt 注入

### char_limits — 各模块字符上限

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `task_plan` | int | `6000` | 任务计划注入上限 |
| `perception` | int | `8000` | 感知数据注入上限 |
| `expand_data` | int | `10000` | 拓展模块数据注入上限 |
| `skill_prompts` | int | `8000` | 技能提示词注入上限 |
| `plugin_prompts` | int | `10000` | 插件提示词注入上限 |

### injection_mode — 各模块注入模式

全部默认 `"full"`（全文注入）。可选值：`"full"` / `"truncated"` / `"off"`。

| 字段 | 说明 |
|------|------|
| `permanent_memory` | 永久记忆 |
| `important_memory` | 临时重要记忆 |
| `temporary_seven_days` | 7 天层记忆 |
| `temporary_one_month` | 30 天层记忆 |
| `temporary_half_year` | 180 天层记忆 |
| `knowledge_index` | 知识库索引 |
| `task_plan` | 任务计划 |
| `expand_data` | 拓展数据 |
| `perception` | 感知数据 |

---

## kemo_graph — 知识图谱检索开关

全部默认 `false`。只有当 global_expand 中接入 kemo-graph 模块后才生效。

| 字段 | 说明 |
|------|------|
| `kemo_graph_global_knowledge` | 全局知识库是否切换为图谱检索 |
| `kemo_graph_shared_knowledge` | 共享知识库是否切换为图谱检索 |
| `kemo_graph_user_knowledge` | 用户知识库是否切换为图谱检索 |
| `kemo_graph_temporary_memory` | 三层临时记忆是否切换为图谱检索 |

---

## memory — 记忆系统

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `storage_schema_version` | int | `3` | 记忆存储格式版本 |
| `extraction_mode` | string | `compression_only` | `disabled` 完全关闭；`compression_only` 仅保存/压缩时提取；`background` 每轮后台提取；`on_commit` 每轮同步提取 |
| `recovery_max_rounds_per_scan` | int | `2` | Maintenance 每轮扫描最多补提取的会话轮数，运行时限制为 1–20 |
| `history_read_enabled` | bool | `true` | 是否允许智能体读取历史对话 |
| `important_memory_max_chars` | int | `5000` | 临时重要记忆文件最大字符数 |

### temporary_injection_limits — 临时记忆注入上限

按**文件数量**截断，优先保留高权重层级。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `half_year` | `100` | 半年层最多注入 100 个文件 |
| `one_month` | `200` | 月层最多注入 200 个文件 |
| `seven_days` | `300` | 周层最多注入 300 个文件 |

### tiers — 记忆层级与晋升规则

| 层级 | `days` | `upgrade_threshold` | `next` | 说明 |
|------|-------|---------------------|--------|------|
| `seven_days` | 7 | 3 | `one_month` | 7 天到期，权重 ≥ 3 晋升到月层 |
| `one_month` | 30 | 10 | `half_year` | 30 天到期，权重 ≥ 10 晋升到半年层 |
| `half_year` | 180 | 60 | null | 180 天到期，权重 ≥ 60 晋升到永久记忆 |

权重规则：
- 仅在记忆被相关内容触发时 +1，每天最多加权一次
- 晋升后新层级权重从 0 重新累计
- 到期未达阈值的直接删除

---

## agent_runtime — 智能体运行时

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `queue_maxsize` | int | `50` | 智能体内部任务队列最大长度 |
| `default_timeout` | int | `600` | 智能体默认超时秒数 |

---

## task_plan — 任务计划

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_steps` | int | `20` | 单计划最大步骤数 |

---

## cron — 定时调度

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用 cron 调度器 |
| `poll_interval` | int | `30` | 任务轮询间隔（秒） |
| `avoid_congestion` | bool | `true` | 是否启用拥塞避免 |
| `congestion_threshold_ratio` | float | `0.2` | 拥塞阈值比例。当 Provider 信号量占用超过此比例时，跳过本轮系统任务 |

---

## task_cron_system — 系统定时任务

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `sense_update_rate` | int | `5` | 感知数据刷新间隔（秒） |
| `expand_update_rate` | int | `5` | 拓展数据刷新间隔（秒） |

---

## runtime_host — 运行时宿主

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_background_scheduler` | bool | `true` | 是否在启动时自动开启后台调度器（cron + 消息路由） |

---

## message — 消息路由

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_workers` | int | `8` | 消息处理线程池大小 |
| `max_queued_messages` | int | `20` | 消息等待队列上限 |

---

## web — Web 服务

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_concurrent_chats` | int | `3` | 单用户最大并发聊天数 |
| `max_pending_chats` | int | `5` | 单用户聊天等待队列上限 |
| `pending_chat_timeout` | float | `30.0` | 排队等待超时秒数 |

---

## agents — 智能体上下文与压缩

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `conserved_rounds` | int | `3` | 保留完整工具日志的最近轮数 |
| `max_rounds` | int | `80` | 上下文最大对话轮数。达到后触发压缩 |
| `rounds_after_compression` | int | `20` | 压缩后保留的轮数 |
| `token_limit` | int | `1000000` | 上下文 Token 上限 |
| `token_compression_ratio` | float | `0.3` | 压缩触发比例。当前 Token 超过 `token_limit × ratio` 时触发压缩 |
| `important_memory_review_hours` | int | `3` | 临时重要记忆审阅间隔（小时） |
| `daily_memory_review_time` | str | `"02:00"` | 每日记忆审阅执行时间（北京时间 HH:MM） |

---

## 与 user_config.json 的关系

`global_config.json` 提供全局默认值。用户在 `user_config.json` 中可覆盖部分字段。具体哪些字段属于用户域由 `run/config.py` 的 `USER_ONLY_SECTIONS` 定义。

修改全局配置后无需重启，下次请求自动生效。
