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
  1. 先枚举全部长期信息，再按独立更新、独立失效、独立加权、独立检索四项检查分解为微记忆碎片；任一项可独立处理就必须拆开
  2. 汇总候选后使用 memory_manage `search_many tier=all` 批量搜索匹配；不超过 20 个候选时一次查询，超过 20 个时按最多 20 个一批分组。每项使用 2～4 个空格分隔的核心关键词，不提交完整句子，禁止逐候选、逐层串行搜索
  3. 命中: 依据 `match_score`、`matched_terms` 和返回正文核对语义；确认同一事实后复制 memory_ref 和 filename，返回 reinforce（正文不变）或 revise（完整新正文）；宿主绑定成功 search_many 的完整正文快照并在事务内校验，按原对话证据日期每天最多+1。单个公共词命中不得直接复用
  4. 未命中: 返回 create，在 seven_days 创建新碎片；首个证据日 weight=0，后续不同日期可加权
  5. 每个 create / reinforce / revise 必须携带 `durable=true`、`evidence` 与 `evidence_rounds`；**条数按独立事实计算，不按对话轮数或单文件字数折算**。一个事实一条碎片，识别出几个事实就返回几条。大维度只用于分类，不是合并边界；字符上限只防止失控。禁止为减少文件数或适配候选上限而合并不同生命周期的事实。单批上限只由 `memory.extraction_max_candidates_per_batch` 控制，达到上限时选择价值和证据最强的独立事实并说明截断
  6. 没有合格信息时返回空 candidates[]

权重规则: 本模式由宿主把原轮次 committed_at 映射到上海自然日，数据库事件唯一键每日最多+1，不由模型比较今天。创建的首次证据日登记 creation、权重0；跨天证据逐日累计。缺少可信时间戳时跳过加权，不用提取日代替。Prompt 注入、查看和检索不加权；用户主动编辑/手动审阅更新按执行日登记。
永久记忆: 普通命中时不返回候选且运行时拒绝覆盖；只有用户本轮明确要求记住、`explicit=true` 时才允许更新
```

运行时只会传入用户消息，不会传入助手回复、推理或工具结果。不得读取 `important` 层；每个候选的 `evidence` 必须是输入中可精确找到的用户原文。

### 模式二：记忆晋升（trigger = `"memory_promotion"`）

调度器会遍历本轮全部到期且达标的碎片，按目标层分批调用本模式：普通层每批最多 20 条，永久层每批最多 8 条。批次大小不是总量上限；每批成功后立即事务落盘，未成功项保持原层并由后续调度继续重试。

```
输入: { trigger: "memory_promotion", promotions: [{from_tier, to_tier, filename, ...}] }

流程:
  通用前置（两种路径都执行）:
    0. 超限检查：每项决策必须返回 `memory_type: "A" | "B"`。正文超出类型上限（A 类画像 1000 字 / B 类事实通常 100 字、硬上限 150 字）时，先产出拆分决策再晋升；`split_into` 子项沿用顶层类型，类型不同时在子项单独声明。
       按 A 类子主题、B 类独立事实拆成多条；不设总纲；就地留在目标档位；
       子碎片继承 expires_at 与 tier_entered_at；权重按新档位归零；不产生加权事件；只给决策。

  7d→30d / 30d→180d:
    1. 按目标层分组，通过 memory_manage `search_many tier=<目标层> include_content=true` 批量查询相似碎片；禁止调用 list/get 或扫描整层
    2. **仅当命中的是同一事实的更新版时才融合**（例："生活费 1500→2000"）；主题相近但事实不同的碎片各自独立成条，不融合 → merged_with=null
    3. **融合结果永不跨越上限**（A 类 1000 字 / B 类 100 字）；到上限即停止合并，稳定在「多条、各自达标」，避免拆了又合的震荡
    4. cron 根据决策在 SQLite 事务内更新、融合或按拆分创建多条表行；拆分时来源删除，目标 weight=0，并继承源 expires_at 与 tier_entered_at

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
  1. 按 request 使用 memory_manage 的搜索 action 查询相关记忆；多个目标使用 search_many；运行时强制 include_content=true，旧目标修改必须有本次完整检索回执；不得调用 list/get
  2. 返回 candidates[]；纯搜索时允许返回空数组并在其他输出字段中说明结果
  3. executor 校验检索引用后，将 create / reinforce / revise / forget 候选统一写入 MemoryStore；旧 upsert 兼容转换
  4. 返回 memory_update 元数据，报告 created / updated / forgotten / rejected，以及 weighted / daily_locked / content_updated / weight_skipped / rejection_reasons
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
  "candidates": [{ "action": "create|reinforce|revise|forget", "filename": "...", "content": "...", "explicit": false, "durable": true, "evidence": "用户原话", "evidence_rounds": [1] }],
  "promotions": [{ "from_tier": "...", "to_tier": "...", "filename": "...", "memory_type": "A | B", "merged_with": null, "content": null, "split_into": null, "skill_created": false }]
}
```

## 注意事项

- 所有阈值从 `global_config.json → memory.tiers` 读取
- `submit_memory_extraction()` 管线已废弃；保存/压缩、background、on_commit 共用宿主证据验证与持久化契约
- 候选统一由调用方写入 MemoryStore；宿主验证 evidence_rounds 和检索 memory_ref，通过数据库按原证据日去重，每天最多+1；不要输出权重、日期或内容哈希
- 候选文件名基础名称最长 20 字符，并遵守全层级唯一命名规则
- context_compression 模式下 memory_manage 只用于搜索，不直接增删改
- context_compression / memory_promotion 模式下禁止搜索 `important`；用户主动 manual_review 可只读查看
- memory_promotion 模式下 memory_manage 只通过批量搜索读取命中项并比对，不调用 list/get，也不直接删除或移动；cron 根据 promotions 决策原子落盘
- 永久记忆不自动修改（除非 explicit=true 或 180d 晋升）
- skill_creater 只写 `agent_create` 目录，不写 `user_create`
- 敏感凭据检测失败时直接拒绝，标记 rejected
