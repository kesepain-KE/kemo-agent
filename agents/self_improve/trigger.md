# 注册信息

- **名称**: self_improve
- **触发**: 三种模式 — ① context_manage 裁剪旧轮次后传入批量对话（`trigger: "context_compression"`） ② cron 模块 `review_due` 任务发现达标碎片后唤起（`trigger: "memory_promotion"`） ③ 主智能体通过 `subagent_dispatch` 主动唤起（`trigger: "manual_review"`）
- **职责**: 提取新微记忆碎片 → 增量权重（每天最多+1） → 碎片融合 → 层间晋升 → 工作记忆创建技能
- **模型**: reasoning
- **工具**: memory_manage（只读搜索记忆）、skill_creater（180d→permanent 工作记忆时创建技能）

# 操作信息

## 调用方式

| trigger | 调用方 | 说明 |
|---------|--------|------|
| `context_compression` | context_manage（内部直调） | 上下文压缩前传入即将裁剪的完整批量轮次 |
| `memory_promotion` | cron `review_due` 任务 | 发现到期且权重达标的碎片后唤起 |
| `manual_review` | 主智能体（通过 `subagent_dispatch`） | 用户主动要求审阅/整理/搜索记忆时，手动唤起记忆提取与审阅 |

## 三种模式

### 模式一：碎片提取与更新（trigger = `"context_compression"`）

```
输入: { rounds: [...], trigger: "context_compression", source: {...} }

来源可以是历史提交后的单轮 `round_commit`，也可以是 context_manage 压缩前的多轮批次。

流程:
  1. 分解批量对话为微记忆碎片
  2. 逐条通过 memory_manage 搜索匹配
  3. 命中: 返回同名 upsert 候选，由 MemoryStore 依据 last_weight_date 每天最多+1
  4. 未命中: seven_days 创建新碎片，weight=0
  5. 返回 candidates[]

权重规则: 每天最多+1，通过 last_weight_date（日期字符串）比较
永久记忆: 不自动修改，除非用户 explicit=true
```

### 模式二：记忆晋升（trigger = `"memory_promotion"`）

```
输入: { trigger: "memory_promotion", promotions: [{from_tier, to_tier, filename, ...}] }

流程:
  7d→30d / 30d→180d:
    1. 查下一层全部碎片
    2. 相似 → 返回 merged_with + 完整融合 content；无相似 → merged_with=null
    3. cron 根据决策原子移动，旧层删除，新层 weight=0，重设 expires_at
    4. cron 更新两层 data.json

  180d→permanent:
    1. 判断是否为工作记忆
    2. 是 → skill_creater 创建技能到 agent_create/
    3. 升级到 permanent
    4. 不是 → 直接升到 permanent
```

### 模式三：主智能体手动审阅（trigger = `"manual_review"`）

```text
输入: { trigger: "manual_review", request: "用户的具体审阅/整理/搜索要求" }

流程:
  1. 按 request 使用 memory_manage 搜索和读取相关记忆
  2. 返回 candidates[]；纯搜索时允许返回空数组并在其他输出字段中说明结果
  3. executor 将 upsert / forget 候选统一写入 MemoryStore
  4. 返回 memory_update 元数据，报告 created / updated / forgotten / rejected
```

`request` 必须是非空字符串。手动模式不执行层间晋升；晋升仍只走 `memory_promotion`。

## 晋升阈值（来自 global_config → memory.tiers）

| 路径 | 配置项 | 默认 |
|------|--------|------|
| 7d→30d | seven_days.upgrade_threshold | 3 |
| 30d→180d | one_month.upgrade_threshold | 10 |
| 180d→永久 | half_year.upgrade_threshold | 60 |

## 工作记忆特征

满足任一条件：
- 涉及项目开发/代码/部署
- 涉及用户技能/工作流
- 涉及架构决策/技术偏好
- 涉及硬件/服务器管理
- 包含工具/命令/路径引用

## 输出格式

```json
{
  "candidates": [{ "action": "upsert|forget", "filename": "...", "content": "...", "explicit": false }],
  "promotions": [{ "from_tier": "...", "to_tier": "...", "filename": "...", "merged_with": null, "content": null, "skill_created": false }]
}
```

## 注意事项

- 所有阈值从 `global_config.json → memory.tiers` 读取
- `submit_memory_extraction()` 管线已废弃，只走 context_manage 路径
- 候选统一由调用方写入 MemoryStore；权重通过 `last_weight_date` 日期比较，每天最多+1
- 候选文件名基础名称最长 20 字符，并遵守全层级唯一命名规则
- context_compression 模式下 memory_manage 只用于搜索，不直接增删改
- memory_promotion 模式下 memory_manage 只用于读取和比对，不直接删除或移动；cron 根据 promotions 决策原子落盘
- 永久记忆不自动修改（除非 explicit=true 或 180d 晋升）
- skill_creater 只写 `agent_create` 目录，不写 `user_create`
- 敏感凭据检测失败时直接拒绝，标记 rejected
