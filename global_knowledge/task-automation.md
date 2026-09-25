# 任务与定时调度功能说明

本文集中说明一次性多步骤任务计划和按北京时间运行的 Cron 任务。两类任务共用用户隔离、持久化和运行安全边界，但状态机与创建字段仍按各自章节执行。

## 定时任务创建规则

定时任务存储在 `users/<user>/task_cron/cron_<8hex>.json`，由 `CronStore` 校验并由 RuntimeHost 调度。所有用户任务都以北京时间（Asia/Shanghai，`+08:00`）保存和计算。

### 正确创建管线

自然语言时间不能由主智能体直接猜测：

```text
用户描述“每天早上九点……”
  → 调用 time_plan 子智能体解析
  → 得到自包含 prompt、type 和调度字段
  → task_time(action="create")
  → CronStore 原子写入
```

修改自然语言任务时先 `task_time get`，再由 `time_plan` 解析修改，最后 `task_time update`。删除前也必须先读取并确认目标。只有调用方已经拥有完整、确定的结构化时间时，才可跳过 `time_plan`。

### 任务类型

| 类型 | 字段 | 规则 |
|------|------|------|
| `once` | `next_run_at` | 单次北京时间 ISO 8601；活动状态必须非空，执行完成后终态为 `completed` 且终态可清空 |
| `daily` | `time` 或 `times` | 每天一个或多个固定 `HH:MM`；`next_run_at` 保存最近计算出的下次时间 |
| `weekly` | `weekdays` + `time`/`times` | ISO 星期 1=周一、7=周日；“工作日”表示周一至周五 |
| `monthly` | `month_days` + `time`/`times` | 每月指定日期；当月不存在 29/30/31 时跳过，不向月末回退 |
| `recurring` | `interval_seconds` | 用户工具要求至少 60 秒；系统内部任务可以使用更短受控间隔 |

日历任务可用 `start_date`、`end_date` 设置首尾包含的北京时间生效日期，并用 `max_runs` 限制成功执行次数；框架维护 `successful_runs`。到达结束日期、没有下一次合法时刻或达到次数上限后，任务自动进入 `completed`。`once` 不能包含重复调度字段；`recurring` 不能包含日历调度字段。

### 当前扁平 Schema

```json
{
  "task_id": "cron_a1b2c3d4",
  "title": "每日状态汇总",
  "prompt": "读取当前运行状态并向用户汇总异常；没有异常也要明确报告正常。",
  "user": "alice",
  "type": "weekly",
  "weekdays": [1, 3, 5],
  "times": ["09:00", "20:00"],
  "start_date": "2026-12-02",
  "end_date": "2026-12-12",
  "max_runs": 10,
  "successful_runs": 0,
  "next_run_at": "2026-07-24T09:00:00+08:00",
  "latest_run_at": "",
  "status": "enabled",
  "created_at": "2026-07-23T13:00:00+08:00",
  "exec_mode": "agent"
}
```

| 字段 | 说明 |
|------|------|
| `task_id` | 用户任务必须为 `cron_` + 8 位小写十六进制 |
| `title` | 非空标题 |
| `prompt` | 非空、自包含的执行提示词，不能依赖创建时对话上下文 |
| `user` | 现有内部用户名 |
| `type` | `once` / `daily` / `weekly` / `monthly` / `recurring` |
| `next_run_at` | 北京时间 ISO 8601；终态可为空 |
| `latest_run_at` | 最近运行的北京时间 ISO，未运行时为空 |
| `status` | 当前状态 |
| `created_at` | 北京时间 ISO 8601 |
| `exec_mode` | 普通用户任务使用 `agent`；其他模式属于框架内部编排 |
| `start_date` / `end_date` | 可选的北京时间生效日期，首尾包含 |
| `max_runs` / `successful_runs` | 可选成功次数上限与框架维护的成功次数 |

Schema 拒绝未知或废弃字段。不要重新引入旧式嵌套 `schedule` 对象；旧文件由存储层读取时迁移。

### 状态机

| 状态 | 含义 | 用户可直接设置 |
|------|------|----------------|
| `enabled` | 等待调度 | 是 |
| `paused` | 暂停 | 是 |
| `running` | 正在执行 | 否，调度器维护 |
| `completed` | 单次任务完成 | 否 |
| `failed` | 最近执行失败；不会被轮询器自动无限重跑，需显式重新启用 | 否 |
| `cancelled` | 已取消的终态 | 否 |

RuntimeHost 重启时会把被中断的 `running` 用户任务恢复为 `enabled` 并重新安排。`cron.enabled=false` 或 `runtime_host.enable_background_scheduler=false` 时不会后台执行。

用户任务和系统任务的执行记录都只写入结构化运行日志数据库 `runtime/logs.sqlite3`。用户任务失败会保存受限异常类型与错误摘要，不保存完整提示词。高频 `success` 以及内容完全相同的连续 `partial` 使用时间窗口聚合；新的 partial 指纹、失败和恢复仍即时记录，避免重复异常刷爆数据库。

### 定时任务历史对话生命周期

`exec_mode=agent` 的任务使用 `background:cron:<task_id>` 作为独立历史来源。每次执行结束时，历史会话登记 `cron_finished_at`；全局 `cron.history_retention_days` 控制从这次结束时间开始的保留期，默认 7 天，范围 0–3650，`0` 表示永久保留。该字段只能写在 `config/global_config.json`，旧用户配置中的同名值不会覆盖全局策略。

RuntimeHost 的 Maintenance 每 5 分钟读取一次全局配置，并按用户最多清理 100 个已到期会话。到期边界按 UTC 实时时长计算，恰好达到保留期即可清理；旧会话没有 `cron_finished_at` 时，保守使用会话最近更新时间。无效或无法解析的时间不会触发删除。关闭 `runtime_host.enable_background_scheduler` 时，定时任务和此维护清理都会暂停。

清理只匹配大小写准确且任务 ID 非空的 `background:cron:<task_id>`：普通 Web/CLI 对话、外部消息、任务计划、其他后台来源以及别的用户均不受影响。每次 agent 定时执行都创建新的逻辑会话，并把任务标题写入历史索引；网页历史抽屉以“定时任务”来源只读打开。运行中的会话、正在执行记忆提取或历史摘要的会话会跳过；候选在会话锁和 SQLite 写事务中再次校验，删除 session、active binding、archive/runtime window、消息、轮次和上下文摘要，并保留删除栅栏防止旧 Run 迟到复写。

网页入口位于“配置 → 运行限制 → 调度与超时 → 定时任务历史保留天数”，使用现有全局配置读取和修改 API。缩短天数会在后续维护扫描中清理已经越过新期限的历史，删除不可撤销；不会删除 Cron 任务定义、结构化执行日志、附件、下载文件或消息队列。

### 对话生命周期兜底巡检

RuntimeHost 启动时会在 `cron/task_cron_system/` 对账创建系统任务
`session_lifecycle_sweep`，默认每小时执行一次。任务按用户扫描超过 24 小时未更新的未关闭会话，并补偿 closed 但记忆入队尚未登记成功的会话，
单轮最多处理 50 个；`config/global_config.json` 的 `cron.session_idle_close_seconds` 可覆盖闲置阈值，
但不会低于 3600 秒，非法值回退为 24 小时。正在运行、排队、停止中的会话、持有未过期 Web/App 持久 lease
的会话以及无法确认租约状态的会话都会跳过。未关闭候选先登记记忆提取队列，再关闭逻辑会话；closed 补偿候选只补登记队列。
两者都不删除历史窗口或消息，单会话失败会记录并继续处理后续候选。

CLI 使用独立 `source=cli` 会话，在正常结束、交互退出或异常收束时关闭；Cron 主智能体每次任务使用新的独立会话并在结束时执行同样的“记忆提取登记 →
关闭会话”收尾；收尾失败不改变入口或 Cron 任务本身的退出状态。`subagent`、`function` 系统分支不
会伪造主智能体历史会话。

### 创建质量要求

- `prompt` 必须包含目标、输入来源、输出去向和失败时行为。
- 涉及外部发送时明确平台、目标身份和内容，不能依赖模糊的“发给他”。
- 一次任务的时间必须包含 `+08:00`；不能写无时区字符串。
- 重复任务避免过短周期和重复副作用，必要时设计幂等检查。
- 创建后用 `task_time get/list` 核对，不直接编辑磁盘 JSON。
- 每个用户最多保存 100 个定时任务；达到上限后先删除不再需要的定义。
- `task_time history` 或 `get(history_limit>0)` 按用户与任务 ID 的联合索引直接读取最近执行状态和安全错误摘要，不先扫描该用户的其他任务记录。

### 网页管理与执行历史

网页“任务”中的任务计划、定时任务和执行记录三栏都使用独立列表容器，容器头展示栏目说明和总数，卡片区与右侧详情区保持明确分界；三栏均固定每页最多 6 个，并分别维护自己的页码和选中项。任务计划保留后端返回顺序；定时任务按 `next_run_at` 由近到远排列，未排期任务置后；执行记录按最近执行时间由新到旧排列。网页“任务 → 定时任务”支持直接创建、编辑、暂停、恢复、失败后重新启用和删除用户任务，系统任务不进入用户任务中枢。创建与编辑仍调用同一 Web API、`normalize_task` 和 `CronStore` 校验，不能绕过核心 Schema。总览接口仍不回显执行 prompt；用户明确选中自有任务后，详情接口才按需返回经凭据扫描和脱敏的执行内容，系统内置任务的内部 prompt 永不回显。右侧详情同时展示调度规则、生效区间、成功次数、最近/下次运行时间与执行模式。任务计划的右侧详情展示描述、提醒、来源会话、revision，以及每个步骤的说明、依赖、工具参数和已脱敏的结果/错误；执行记录右侧详情保持只读。

“执行记录”使用 `cron_execution_logs` 的真实逐次/聚合记录，不再只把终态任务定义伪装成执行历史；记录可查看状态、耗时、受限结果摘要和错误，但删除任务定义不会删除执行日志。用户任务页只展示任务计划终态和用户自建 Cron 的执行历史；`cron/task_cron_system/` 中的系统维护任务及其执行结果不进入用户层任务中枢。过滤同时覆盖写在 `__system__` 名下的全局记录，以及系统任务按具体用户运行时写入该用户名下的记录。

### 系统任务边界

`cron/task_cron_system/` 属于框架维护，允许可读的系统任务 ID、`exec_mode=system` 和 `action` 字段。感知刷新、全局拓展刷新、记忆巡检等由 RuntimeHost 对账创建。用户任务不得伪装成系统任务；这些系统任务仍保留结构化日志供运行监控与诊断使用，但不会出现在网页用户任务页的定时任务或执行记录中。

---

## 任务计划创建规则

任务计划用于需要多个可验证步骤、依赖关系和中途控制的复杂任务。权威数据位于 `users/<user>/task_plan/task_plans.sqlite3`，由 `PlanStore` 管理；模板 JSON 只用于说明输入结构。

简单的一次性操作不应创建计划。计划创建和复杂重编排必须走 `task_plan` 子智能体；运行中的
`task_plan` 工具还可以查看、列出、修正计划、重试或重置失败步骤、记录步骤结果、批准、暂停、
恢复和取消。所有修改都经过 `PlanStore` 的 revision 校验，不能直接改 SQLite。

### 创建流程

```text
复杂用户需求
  → task_plan 子智能体读取真实工具/技能/知识索引
  → 生成或编辑结构化计划
  → PlanStore 校验并保存 pending/approved
  → 创建计划的当前主智能体 Run 在本会话边界强制收束
  → 用户批准（或 auto_accept）
  → 前台连续执行或后台逐步执行
  → 每步写回结果，最终 completed/failed/paused/cancelled
```

全局 `task_plan.max_steps` 默认限制为 20。用户配置 `task_plan.auto_accept=false` 时，计划必须等待明确批准。
`auto_accept=true` 时，新计划直接保存为 `approved` 并由正式计划执行链路领取；无论开关状态如何，创建计划的原始主智能体 Run 都不得继续自由执行普通工具，以避免绕过计划状态机或重复执行。

创建成功后的强制收束只作用于当前 `user + source + session_id + run_id`：同一 Provider 响应中位于计划创建之后的工具统一记录为 `not_executed`，不再发起下一次 Provider 请求。本机制不写入用户级暂停标志，也不会停止同一用户的其他对话空间。

任务计划数据库按用户集中保存，便于任务页统一管理；系统提示词注入则按 `source + session_id` 过滤。A 对话只能看到 A 对话所属的未完成计划，B 对话不会因 A 创建计划而获得其内容或被迫停止。聊天页顶部的活动计划卡同样只允许展示当前有效会话所属的计划：没有选中会话、会话归档已清理或会话已删除时，`overview.active_plan` 必须为 `null`，不能从用户全部计划中挑选一条塞进开始页。计划数据本身仍保留在任务中枢，供用户统一查看、取消或整理，不随会话归档清理而静默删除。
主智能体调用 `task_plan` 工具时同样执行会话归属校验；B 对话即使显式提交 A 的 `plan_id`，也不能查看、批准、暂停、恢复或取消 A 的计划。统一任务页、CLI 管理命令和后台调度器不经过这一模型工具边界，仍可在用户明确操作或系统调度下管理集中存储的计划。
Web/App 发起计划执行时，后端会在把计划迁移到 `running` 之前原子校验请求的 `source + session_id` 与计划归属完全一致；错误客户端不能把 A 的计划挂到 B 对话执行。任务页可以统一查看计划，但执行流仍回到计划原始对话空间。

### 完整 Schema 示例

```json
{
  "schema_version": 1,
  "plan_id": "plan_a1b2c3d4",
  "title": "检查并修复服务启动问题",
  "description": "先收集状态，再定位原因，最后进行受控修复与验证。",
  "user": "alice",
  "source": "web",
  "session_id": "conv_example",
  "status": "pending",
  "auto_accept": false,
  "reminder": "修复涉及重启时先提醒用户",
  "revision": 1,
  "created_at": "2026-07-23T05:00:00+00:00",
  "updated_at": "2026-07-23T05:00:00+00:00",
  "current_step": "step_1",
  "steps": [
    {
      "step_id": "step_1",
      "title": "收集状态",
      "description": "读取服务状态和最近错误，不修改系统。",
      "status": "pending",
      "depends_on": [],
      "tool_name": null,
      "tool_arguments": {},
      "critical": true,
      "result": null,
      "error": null,
      "started_at": "",
      "finished_at": ""
    },
    {
      "step_id": "step_2",
      "title": "执行修复并验证",
      "description": "根据第一步证据实施最小修复并重新验证。",
      "status": "pending",
      "depends_on": ["step_1"],
      "tool_name": null,
      "tool_arguments": {},
      "critical": true,
      "result": null,
      "error": null,
      "started_at": "",
      "finished_at": ""
    }
  ]
}
```

### 字段约束

- `plan_id` 必须为 `plan_` + 8 位小写十六进制。
- `step_id` 必须为 `step_<数字>`，在计划内唯一。
- `title` 与 `description` 必须非空。
- `depends_on` 只能引用同一计划中已存在的步骤，禁止循环依赖。
- `critical` 必须是布尔值。
- `tool_name` 可为空；非空时必须是实际可用工具，且不能是 `task_plan` 或 `task_plan_*` 管理工具。
- `tool_arguments` 必须是对象。
- `tool_arguments` 不得包含密码、Cookie、Authorization、API Key、访问/刷新 Token、私钥或其他 `_token`/`_secret` 字段；计划是持久化、可回溯数据，凭据必须改为环境变量名或受控安全引用。`token_limit` 等非凭据配置不会被误判。
- `revision`、`updated_at` 和运行时间由存储层维护，不应由 UI 用旧副本覆盖。
- 已完成步骤在编辑时受保护，不能通过重写计划抹掉结果。

### 状态

计划状态：`pending`、`approved`、`running`、`paused`、`completed`、`failed`、`cancelled`。

步骤状态：`pending`、`running`、`completed`、`failed`、`skipped`、`cancelled`。

计划批准后有两种执行形态：

1. Web/App 前台执行：在当前对话中连续运行；每步完成后主智能体调用 `task_plan step_done`，读取返回的 `progress` 和 `next_step` 继续下一步，用户可以看到工具与文本输出。当前会话显式开启长任务模式时，仍处于 `running` 的计划可在单 Run 工具次数上限后跨 Run 继续，保持同一计划与已完成步骤，不重新领取或批准；中间 Run 不触发计划暂停。完整续跑、取消与交接边界见 `long-task-runtime.md`。
2. 后台执行器：一次只运行一个步骤，步骤状态由框架维护；控制提示会明确禁止再次调用 `step_done/step_fail`。

两种形态不能混用步骤写入责任。运行中暂停表示在安全边界停止；取消则进入不可继续的 `cancelled` 终态。

### 步骤设计原则

- 每一步只产生一个可验证结果，避免“分析、修改、发布、通知”混在一步。
- 依赖关系表达真实前置条件，不仅是视觉排序。
- `tool_name` 只是建议，执行时仍可根据实际环境修正；不要把不存在的工具写入计划。
- 高风险操作单独成步，并在描述中注明确认点和回滚方式。
- 结果摘要应说明做了什么、证据是什么、还剩几步，不只写“完成”。

### 编辑与并发

只允许编辑 `pending`、`approved`、`paused` 或等待修正的 `failed` 计划；`running`、`completed` 和
`cancelled` 计划不能编辑。`retry_step`/`reset_step` 只能处理 `failed` 或 `cancelled` 步骤，已完成
步骤永远不能修改、删除或重置。每次更新都会增加 `revision`；界面保存前必须基于最新版本，收到
“计划版本已变化”时重新读取并合并，而不是强制覆盖。`retry_step` 可以把暂停计划恢复为
`approved`；`reset_step` 只清理步骤，不改变计划状态。若 `auto_accept=true` 或用户配置
`task_plan.auto_retry_on_fix=true`，修正后才会自动等待执行器领取，否则仍需用户批准。运行结束或启动恢复时，
未完成的运行步骤会回收到安全状态。

网页任务计划的 edit/retry 请求必须带当前计划的 `session_id`。后端会拒绝用其他对话空间的
`session_id` 修改或重试计划；任务总览可以跨来源只读查看，但不能借此改变计划归属。

任务总览、执行记录和 revision 列表属于浏览器输出边界。服务层会对旧数据库中的工具参数、结果、
错误和修订备注再次递归脱敏；Authorization、API Key、访问 Token、Bearer、`sk-` 密钥和私钥块
不能通过只读接口返回。这个输出脱敏是持久化前校验的第二道保护，不能用来允许新计划保存凭据。

数据库把计划元数据、步骤和依赖分别保存在 `task_plans`、`task_plan_steps`、`task_plan_dependencies`，并用 `task_plan_revisions` 保存不可改写的修订历史。大型参数、结果和错误通过 `task_plan_revision_blobs` 按计划内 SHA-256 去重，读取时透明还原；旧版明文 JSON 和压缩快照继续兼容。创建、修改、revision 与大型字段引用在同一事务提交，任一步失败都会整体回滚。启动恢复只把 `running` 步骤退回 `pending` 并暂停对应计划。计划运行时没有文件式旁路。
