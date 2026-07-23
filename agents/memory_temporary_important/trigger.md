# 注册信息

- **名称**: memory_temporary_important
- **触发**: 三路 — ① cron 模块 `recurring` 每隔 `agents.important_memory_review_hours`（默认 3h）定时巡检 ② cron 模块 `daily` 每天 `agents.daily_memory_review_time`（默认 02:00 北京时间）每日整理 ③ 主智能体通过 `subagent_dispatch` 主动唤起（用户要求手动触发临时重要记忆巡检时）
- **职责**: 操作 A（periodic_scan）：通过 memory_manage 插件自行读取三层全量临时记忆 → 筛选重要碎片 → 去重永久 → 写入热画像 → 删除已提取源碎片；操作 B（daily_consolidate）：读取热画像 → 整合优化 → 超限压缩
- **模型**: cheap
- **工具**: memory_manage（用于自行读取全量记忆、删除碎片、更新 data.json）

# 操作信息

## 调用方式

三路均可调用：

| 调用方 | 路径 | 说明 |
|--------|------|------|
| cron `CronScheduler` | `AgentRunner.run()` 直调，`exec_mode: "subagent"` | 按 schedule 自动触发 periodic_scan / daily_consolidate |
| 主智能体 | `subagent_dispatch` 工具（`allowed_callers: ["main_agent"]`） | 用户手动要求触发巡检或每日整理时主动调用 |

两个 cron 任务通过 `trigger` 字段区分：

| trigger | 任务 | 调度方式 |
|---------|------|----------|
| `periodic_scan` | 定时巡检 | recurring，间隔 N 小时 |
| `daily_consolidate` | 每日整理 | daily，固定时间 |

如果两个任务同时到期，cron 模块显式将 `periodic_scan` 排在 `daily_consolidate` 前执行。

## 操作 A：定时巡检（trigger = `"periodic_scan"`）

### 输入

子代理自行通过 `memory_manage` 插件读取数据，不依赖外部传入：

1. 对 `seven_days`、`one_month`、`half_year` 分别调 `memory_manage(action="list", tier=..., limit=500)`，读取包含 `filename`、`weight`、`expires_at` 的条目摘要
2. 对摘要中的每个条目调 `memory_manage(action="get", tier=..., filename=...)` 读取完整 Markdown 正文，再按重要特征筛选
3. 调 `memory_manage(action="list", tier="permanent", limit=500)` 读取永久记忆摘要；对每个永久条目逐条调 `memory_manage(action="get", tier="permanent", filename=...)` 获取全文做语义去重
4. 任一次 `list` 返回 `truncated=true` 时停止删除操作，保持当前热画像并报告该层超过单次列出上限

### 流程

1. 用 `list` 读取三层临时记忆摘要
2. 用 `get` 逐条读取全文，再按重要记忆特征筛选
3. 用永久记忆的 `list` 摘要和逐条 `get` 全文做语义去重
4. 生成热画像 Markdown
5. 对每个已提取碎片调 `memory_manage(action="delete", tier=..., filename=...)`
6. 检测无更多符合特征的碎片 → 结束

### 优先级

高于操作 B。同时到期时优先执行。

## 操作 B：每日整理（trigger = `"daily_consolidate"`）

### 输入

调用 `memory_manage(action="get", tier="important", filename="memory_temporary_important.md")` 读取现有热画像全文。

### 流程

1. 检查记忆条目是否可整合优化
2. 可整合 → 先整合
3. 检查是否超过 `memory.important_memory_max_chars`
4. 超限 → 压缩；仍超限 → 删最不重要条目
5. 运行完毕

字符限制以配置文件为准，硬编码 2000 仅作兜底。

## 重要记忆特征

满足以下任一条件的微记忆碎片视为重要。此清单应与 self_improve 的「该记」清单保持同步：

| # | 条件 | 示例 |
|---|------|------|
| 1 | explicit=true | 用户明确要求记住 |
| 2 | 用户身份与偏好 | 姓名、年级、习惯 |
| 3 | 项目与资产 | votx-agent、树莓派、J1900-ITX |
| 4 | 架构与设计决策 | "芦荟大卸八块"拆分哲学，设计决策归此类 |
| 5 | 纠正与规则 | 用户纠正的行为规则 |
| 6 | 配置值与配置偏好 | 端口策略、超时默认值等 |
| 7 | 拓展模块（expand） | 全局拓展、共享拓展、用户拓展的用途与配置 |
| 8 | 活跃碎片（权重 ≥ 晋升阈值 50%） | seven_days≥1、one_month≥5、half_year≥30 |
| 9 | 涉及关联项目 | llm-adapter-kemo、kemo-graph、new-api |

## 注意事项

- 子代理通过 memory_manage 插件自行读写，不依赖外部数据
- 列出整层必须调用 `memory_manage(action="list", ...)`；需要正文时调用 `memory_manage(action="get", ...)`，禁止空查询搜索
- 与永久记忆语义重复的不进入热画像
- 提取后必须调用 `memory_manage(action="delete", tier=..., filename=...)` 删除源碎片
- **防丢失**：操作 A 提取时若热画像已接近字符上限且新碎片无法完整保留，则不删除该源碎片；操作 B 压缩时优先精简表述而非删除条目，删除作为最后手段
- 不得记录密码、API Key、Token 等敏感凭据
- 字符限制以 `memory.important_memory_max_chars` 配置为准
