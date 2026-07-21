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

1. 调 `memory_manage.search_by_content(tier="seven_days", query="")` — 读取全量 7 天碎片
2. 同上读取 `one_month` 和 `half_year` 全量碎片
3. 调 `memory_manage.search_by_content(tier="permanent", query="")` — 读取永久记忆全文做语义去重

### 流程

1. 全量读取三层临时记忆
2. 按重要记忆特征筛选
3. 与永久记忆去重
4. 生成热画像 Markdown
5. 对每个已提取碎片调 `memory_manage.delete(tier=..., filename=...)`
6. 检测无更多符合特征的碎片 → 结束

### 优先级

高于操作 B。同时到期时优先执行。

## 操作 B：每日整理（trigger = `"daily_consolidate"`）

### 输入

调用 `memory_manage` 的 `search_by_content(tier="important", query="")` 读取现有热画像全文。

### 流程

1. 检查记忆条目是否可整合优化
2. 可整合 → 先整合
3. 检查是否超过 `memory.important_memory_max_chars`
4. 超限 → 压缩；仍超限 → 删最不重要条目
5. 运行完毕

字符限制以配置文件为准，硬编码 2000 仅作兜底。

## 重要记忆特征

满足以下任一条件的微记忆碎片视为重要：

| # | 条件 | 示例 |
|---|------|------|
| 1 | explicit=true | 用户明确要求记住 |
| 2 | 用户身份与偏好 | 姓名、年级、习惯 |
| 3 | 项目与资产 | votx-agent、树莓派、J1900-ITX |
| 4 | 架构与设计决策 | "芦荟大卸八块"拆分哲学 |
| 5 | 纠正与规则 | 用户纠正的行为规则 |
| 6 | 活跃碎片（权重 ≥ 晋升阈值 50%） | seven_days≥1、one_month≥5、half_year≥30 |
| 7 | 涉及关联项目 | llm-adapter-kemo、kemo-graph、new-api |

## 注意事项

- 子代理通过 memory_manage 插件自行读写，不依赖外部数据
- 全量读取三层临时记忆，不设数量限制
- 与永久记忆语义重复的不进入热画像
- 提取后必须调 memory_manage.delete 删除源碎片
- 不得记录密码、API Key、Token 等敏感凭据
- 字符限制以 `memory.important_memory_max_chars` 配置为准
