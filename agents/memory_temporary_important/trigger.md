# 注册信息

- **名称**: memory_temporary_important
- **触发**: 三路 — ① cron 每隔 `agents.important_memory_review_hours` 定时巡检 ② cron 每天 `agents.daily_memory_review_time` 整理 ③ 主智能体通过 `subagent_dispatch` 手动唤起
- **职责**: 维护可重建的临时重要记忆热画像；临时碎片继续正常生命周期，只在完全被永久记忆覆盖或受控融合后清理副本
- **模型**: cheap
- **工具**: memory_manage（只读 `list/get`；所有持久化由运行时原子完成）

# 操作信息

## 调用方式

| trigger | 调用方 | 说明 |
|---------|--------|------|
| `periodic_scan` | cron / 主智能体 | 全量重建热画像、永久语义去重与受控协调 |
| `daily_consolidate` | cron / 主智能体 | 精简热画像并同步保留的来源引用，不修改永久记忆 |

同时到期时，`periodic_scan` 优先。

## periodic_scan

1. 读取当前 `important` 正文及 `featured_sources`。
2. `list/get` 三层临时碎片和永久记忆全文；禁止用空查询代替全量列出。
3. 只有同时具备长期价值、用户证据、不可从系统重读且近期高频有用的内容才进入热画像。
4. 永久层完全覆盖时返回 `drop_duplicate`；部分覆盖时返回带完整融合正文的 `merge_permanent`；未覆盖的高价值碎片进入 `featured`。
5. `featured` 必须是新热画像的完整来源快照。进入热画像不会删除临时源碎片，也不会阻断正常晋升。
6. 任一列表截断时保持旧正文和旧来源，不返回永久协调项。

## daily_consolidate

读取当前热画像及来源，精简和合并表述。正文中被移除的条目必须从 `featured` 同步移除；`permanent_reconciliations` 必须为空。

## 输出

```json
{
  "content": "完整热画像 Markdown",
  "featured": [{"tier": "seven_days", "filename": "example.md"}],
  "permanent_reconciliations": []
}
```

## 注意事项

- `memory_manage` 只允许 `list/get`，不得直接增删改。
- 热画像是派生视图，临时碎片才是权威来源。
- 普通临时 Prompt 会跳过已进入热画像的来源，避免重复注入；来源仍按正常生命周期到期和晋升，但只有后续历史整理依据用户原文命中时才能加权，Prompt 注入本身不加权。
- 完全覆盖副本的清理、部分覆盖的永久融合、热画像与来源索引写入由 executor 在同一事务中执行。
- 不得记录敏感凭据。
