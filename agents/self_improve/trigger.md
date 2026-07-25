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
| `context_compression` | context_manage / 记忆提取管线 | 保存、压缩或配置允许的逐轮入口传入完整轮次 |
| `memory_promotion` | cron `review_due` 任务 | 发现到期且权重达标的碎片后唤起 |
| `manual_review` | 主智能体（通过 `subagent_dispatch`） | 用户主动要求审阅/整理/搜索记忆时，手动唤起记忆提取与审阅 |

## 三种模式

### 模式一：碎片提取与更新（trigger = `"context_compression"`）

采用失败关闭策略：默认不创建记忆。只提取跨会话仍有价值、无法从系统配置重新读取、
并且有用户原话证明的长期用户事实。

**该记**：用户偏好/身份/长期目标、设备稳定事实（已确认）、架构决策与技术偏好（视为工作记忆）、
配置值与配置偏好（视为工作记忆）、拓展模块的用途与配置（视为工作记忆）、
行为纠正规则（正常走临时层，用户主动提及时 explicit=true 直接落 permanent）。

**不该记**：工具/插件清单、一次性测试、任务运行状态、报错诊断、
未确认的过程状态（含"待验证""尚未确认"等措辞）、用户问题本身、敏感凭据。

来源标记为"对话摘要"不构成拒绝理由，以内容本身价值为准。

```
输入: { rounds: [...], trigger: "context_compression", source: {...} }

来源可以是历史提交后的单轮 `round_commit`，也可以是 context_manage 压缩前的多轮批次。

流程:
  1. 分解批量对话为微记忆碎片
  2. 逐条通过 memory_manage 搜索匹配
  3. 命中: 返回同名 upsert 候选，由 MemoryStore 依据 last_weight_date 每天最多+1
  4. 未命中: seven_days 创建新碎片，weight=0
  5. 每个 upsert 必须携带 `durable=true` 与 `evidence`；单轮最多 2 条，批量最多 5 条
  6. 没有合格信息时返回空 candidates[]

权重规则: 每天最多+1，通过 last_weight_date（日期字符串）比较
永久记忆: 普通命中时不返回候选且运行时拒绝覆盖；只有用户本轮明确要求记住、`explicit=true` 时才允许更新
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

满足任一条件（180d→permanent 晋升时触发技能生成）：
- 涉及项目开发/代码/部署（如 votx-agent、kemo-agent 的配置步骤）
- 涉及用户技能/工作流（如"每次启动前检查 xxx"、"部署流程是 yyy"）
- 涉及架构决策/技术偏好（如"拆分哲学"、"不使用 xxx 方案"）—设计决策归此类
- 涉及硬件/服务器管理（如树莓派配置、J1900-ITX 设置）—仅限已确认的稳定事实
- 文件名或内容包含明显的工具/命令/路径

## 输出格式

```json
{
  "candidates": [{ "action": "upsert|forget", "filename": "...", "content": "...", "explicit": false, "durable": true, "evidence": "用户原话" }],
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
