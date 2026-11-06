# 记忆生命周期

`users/<user>/improve/memory.sqlite3` 使用每用户独立 SQLite schema v4（兼容迁移 v1/v2/v3）保存按主题组织的个人记忆：

```text
seven_days → one_month → half_year → permanent
```

四档正文和生命周期统一存入 `memory_fragments`；`memory_weight_events` 以 `(fragment_id, evidence_date)` 唯一约束当日加权，永久层行没有到期时间。

## 记忆域与个人模式

`memory.profile` 默认 `companion`，面向日常、学习、工作与持续关系，开放 fact/relation/state。
用户明确选择 `work` 时只提取 fact。
用户配置可覆盖全局 profile；非法 profile、域规则名称或类型在配置加载时明确报错。

| 域 | 内容与价值标准 | 生命周期 |
|---|---|---|
| fact | 长期稳定、跨会话有用的用户事实 | 原 memory.tiers 规则不变 |
| relation | 用户表达的称呼、玩笑、口癖、互动方式、好恶和共同经历；出现且可辨认即可 | companion 下默认阈值 1，创建日权重 0，第二个自然日证实后达到阈值，到期晋升 |
| state | 用户明确表达的近期计划、生活近况、情绪与关注点，允许过期 | 仅 seven_days，不晋升；默认正文更新后刷新档位进入时间和到期时间 |

候选用 `memory_domain`，数据库列用 `domain`，缺省均为 fact。旧行迁移后也是 fact。
fact 的公开条目保留原字段集合；非 fact 条目和搜索回执增加 domain，供后续更新绑定。
更新不得改变既有行的域，融合限同域，拆分继承来源域。非法或未开放域按候选拒绝并计入 rejected，
不妨碍同批合法候选。

state 一个滚动主题一个文件名，同主题 revise，不同主题分开；保持 B 类 150 字硬上限。
state 的 revise 和直接正文编辑均按配置刷新 tier_entered_at/expires_at，保留 created_at；
同正文命中、reinforce、查看、检索或改名不延长寿命。关闭刷新配置后仍保持原到期时间。
state 即使 explicit=true 也留在 seven_days，切换回 work 也不会使既有 state 晋升。
fact/relation 正文更新保持原固定时效。state 到期成功归档后移出活跃库，不自动召回。

三域 create/revise/forget 都要求用户原话；助手扮演内容、推理与工具结果不能成为新记忆来源。
companion 不以 durable=true 或长期稳定门槛拒绝 relation/state，fact 的 durable 门槛继续保留。
关系和情绪只记录原话支持的内容，不做性格推断或心理诊断。

## 逻辑身份

- 文件名是全部层级中的全局唯一身份，基础名称最长 50 个字符。
- 同名 upsert 更新原表行，不创建重复行。
- `filename_key` 在全部层级全局唯一，数据库直接拒绝跨层同名。
- 普通搜索可查询逻辑文件名或正文，不依赖向量或知识图谱。
- 记忆按对用户有意义的主题组织，不按句子、属性标签或字段拆成微片；同一主题随证据逐步补充和修订。
- A 类承载连贯的个人画像、关系背景或持续项目主题（上限 1000 字）；身份、表达、偏好及其明确原因不必按维度拆开。
- B 类承载需要独立检索或修订的具体约定、参数与事件（上限 150 字）；只有主体、有效期或语义真正独立时才另建。
- state 是短期情境快照，同一生活情境的计划、顾虑和进展合并更新；不同时间边界或相互冲突的状态分别保存。

## 权重规则

- 不进行每日权重衰减，权重没有上限。
- 历史整理管线由 `self_improve` 依据用户原文或具同整合明确用户背书的助手正文提出候选，宿主按 RAW `integration_id` 验证关联后加权；助手只可 reinforce 已检索的既有事实，用户主动修改正文/手动审阅更新使用执行日，查看检索保持只读。
- Prompt 注入、记忆工具查看和用户主动检索都是只读行为，不得加权。
- 正文更新与同内容命中共用 `(fragment_id, evidence_date)` 唯一日锁，同一档位每个上海自然日最多 `+1`。
- 实际修改更新 `content_updated_at`；`updated_at` 仅是内容更新时间的兼容别名。
- 历史证据日期由 RAW 卡片的 `created_at` 映射到 `Asia/Shanghai`。提取 Schema 沿用 `committed_at` 字段传递该事件时间；绝对时间统一保存为 UTC ISO 8601，不使用提取运行日或会话创建时间。
- 新片首个可信证据日登记 `reason=creation`，权重为 0；随后不同日期逐日 +1。同一天跨批次不重复加分。
- 缺少时间、无时区、非法或未来时间的证据只保存有效正文，不补造当天权重，返回 `missing_evidence_date`；不得用会话创建时间回填。
- 可接纳证据下界为本档位 creation 日期，没有该登记时用进入档位日；更早的乱序证据保守跳过。生命周期仍以实际入库/晋升时间计算。
- `last_weight_date` 只表示最大已加权证据日期；乱序证据不使其倒退。普通晋升清除旧档位日锁，早于新档位进入日的证据不能再次加分；拆分子片维持继承的 tier_entered_at 下界。
- 旧目标必须绑定本次完整 `search_many` 回执的 memory_ref 与正文哈希；reinforce 不改正文，revise 才写新正文；旧 upsert 兼容转换并同样校验。
- on_commit 和后台恢复统一按用户证据及 RAW seq 范围计算批次幂等 ID；replayed=true 仅为回执重放。返回 weighted / daily_locked / content_updated / weight_skipped，区分加分、锁定与跳过。
- Web 日锁由宿主返回 Shanghai 日期和 weight_locked_today；weighted_today 排除 creation。维护列表按事件处理时间纳入纯加权，不把补提取证据日误当作处理日。
- 进入新档位后 weight 归零，重新计算该档位的固定到期时间。
- 永久记忆不记录权重。

## 档位审核

| 当前档位 | 固定持续时间 | 晋升阈值 | 下一档位 |
|---|---:|---:|---|
| `seven_days` | 7 天 | 3 | `one_month` |
| `one_month` | 30 天 | 10 | `half_year` |
| `half_year` | 180 天 | 60 | `permanent` |
| `permanent` | 无到期 | 无 | 无 |

到达 `expires_at` 后：

- 权重达到阈值：事务内更新同一行到下一层，权重清零并设置新层固定到期时间。
- 权重未达到阈值：先成功归档至 `improve/expired_memory/YYYY-MM/` 普通 Markdown，再条件事务移出活跃库；失败不删，重试复用同一文件。
- 主动遗忘同样直接删除，不保留 tombstone 或删除历史。

## Prompt 注入

全局 `memory.prompt_injection` 默认 true，用户配置不能覆盖。设为 false 时，永久记忆、热画像和三层临时记忆均不自动注入，仅保留“当前记忆没有开启注入，可以通过工具进行记忆检索”。此开关不关闭存储、提取、后台整理或 `memory_manage`；下列排序与预算在开启注入时适用。

- 永久层全部注入，按逻辑文件名稳定排序。
- `memory_temporary_important.md` 是独立单文件，受字符上限控制，不参与普通权重。
- 临时三层按 `half_year → one_month → seven_days` 注入。
- work 层内按 weight 降序、文件名自然排序；companion 临时层先按 state → relation → fact，
  再按 weight 降序和文件名稳定排序，排序在数量截取前完成。永久层排序保持原状。
- `memory.temporary_injection_limits` 只限制单次 Prompt，不限制磁盘存储数量。

`memory_manage list(tier=all)` 按七天、月、半年、永久四层统一分页，每项保留实际层级与稳定引用；热画像仍单独读取。

记忆检索默认使用 `match_mode="keywords"` 的多关键词评分；可选 `substring`、`word`、`exact` 以及 `regex=true`，与历史检索共用 `run/infra/text_search.py` 的文本匹配。显式 `fulltext` 使用每用户 SQLite FTS5/simple 索引与 BM25，批量查询共享读取快照；不含独立热画像，不输出关键词覆盖率，也不替代后台整理的同一事实判定。返回摘要仍受上限约束，完整正文按需获取，搜索不加权。

## 数据与执行边界

- `memory.extraction_mode` 控制可认领的 RAW 范围；默认 `compression_only` 在会话关闭或 VIEW 压缩成功后排队，`background` 也允许 Maintenance 领取 pending RAW。兼容值 `on_commit` 当前与 `background` 一样只允许后台领取，不会在请求线程同步提取。
- 后台向 `self_improve` 只传安全过滤的用户正文、引导正文与助手最终正文（排除临时/重试残片与已撤回整合）；create/revise/forget 只收用户原话，助手只可 reinforce 检索绑定的已有事实，且需宿主关联到同整合的明确用户背书；推理、工具结果和 important 不作来源。
- 权重口径是“不同日期出现次数”，事件保存 user/assistant/manual/historical_migration 来源，用户/助手共用日锁，单纯复述注入记忆不得加权。
- 输入按 `memory.extraction_input_tokens` 的 DeepSeek token 预算组织同一整合；超长正文无损分页保留 seq/card_id/偏移，后续答案页附带有界 context_only 用户背景；满页继续整理未返回主题；实际候选批次上限不变，超限进入共用 memory_work_items 待办。源提取和候选全部完成后才推进历史游标；饱和无进展时失败关闭。
- 后台 `context_compression` / `memory_promotion` 禁止搜索 `important`；用户或主智能体主动查看记忆时保留只读权限，且查看不加权。
- 临时重要热画像的 `important_memory_max_chars` 仅是 Prompt 注入预算；文件正文不在该值处截断。`important_memory_output_max_chars` 是独立输出硬上限，防止模型失控重复，超限时保留旧热画像。
- 临时重要热画像巡检通过 `memory_manage list(limit=500, compact=true, include_content=true, page_char_limit=80000)` 按条目数与字符预算双重边界分页批量读取三层临时记忆和永久记忆，不得对全量条目逐条 `get`；`self_improve` 的候选匹配和晋升融合使用 `search_many` 批量查询，运行时为该子代理强制 `include_content=true`，只有完整命中回执才能绑定旧目标。
- RAW 工具卡片不占正文批次配额；超过 500 张工具事件仍可归同一正文批次，工具-only 区间可在零模型调用下推进覆盖。已处理的用户背景 context_only 不产生新证据或重复权重。分页预算固定在持久待办中，重启或配置变化不改变既有页游标含义。
- 记忆覆盖使用连续 `memory_processed_seq`，与 VIEW 摘要覆盖独立。`rounds` / `evidence_rounds` 是提取 Schema 的证据编号字段，在 CardStream 入口对应原始 RAW seq，不表示对话轮次。
- 认领绑定会话代际、seq 范围、claim_id 和到期时间；候选待办全部完成后才推进游标。已接受候选分批落库期间复用持久化候选，不重复调用分析模型；失效认领和已删除会话拒绝迟到结果。
- 证据只取已提交 RAW 中允许的用户/助手正文；工具、推理和 VIEW 摘要不作来源，候选仍须通过原话与检索回执验证。
- 用户明确要求长期记住的有效 fact/relation 直接进入永久层；state 保持 seven_days。
- 密码、API Key、Token、Cookie、私钥、验证码等敏感凭据禁止入库。
- 加权、晋升、融合、删除、幂等结果和热画像来源使用 SQLite 事务原子提交。
- 到期晋升不设置总数量上限：调度器按目标层遍历全部达标碎片，普通层每批最多 20 条、永久层每批最多 8 条；每批成功后立即落盘，失败或缺少决策的条目保持原层并在后续 30 秒扫描中重试。
- 当前阶段不调用或修改 `E:\code\kemo-graph`。

自动技能先查同名/近似并校验适用场景、至少两步、失败处理；source_memories 绑定稳定行身份和正文摘要。
正常晋升/融合/拆分只传递内容摘要仍匹配的来源，不得使此前因修正而 stale 的技能重新激活；来源修正、删除、归档使技能 stale，网页可见且停止自动注入，不自动删除技能。
技能转化失败独立保留待办，由 30 秒晋升维护周期重试。归档没有自动清理期限和专用恢复入口，也不自动注入。
