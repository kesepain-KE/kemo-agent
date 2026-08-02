# 记忆生命周期

`users/<user>/improve/memory.sqlite3` 使用每用户独立 SQLite schema v1 保存微量记忆：

```text
seven_days → one_month → half_year → permanent
```

四档正文和生命周期统一存入 `memory_fragments`；`memory_weight_events` 以 `(fragment_id, evidence_date)` 唯一约束当日加权，永久层行没有到期时间。

## 逻辑身份

- 文件名是全部层级中的全局唯一身份，基础名称最长 50 个字符。
- 同名 upsert 更新原表行，不创建重复行。
- `filename_key` 在全部层级全局唯一，数据库直接拒绝跨层同名。
- 普通搜索可查询逻辑文件名或正文，不依赖向量或知识图谱。
- 每行只保存一个足够微量化的事实、偏好、关系或项目状态。

## 权重规则

- 不进行每日权重衰减，权重没有上限。
- 临时记忆只在保存、手动压缩、Token 超限压缩等历史整理管线中，被 `self_improve` 依据用户原文命中时加权。
- Prompt 注入、记忆工具查看和用户主动检索都是只读行为，不得加权。
- 正文更新与同内容命中共用 `last_weight_date`，同一记忆在同一用户本地自然日合计最多 `+1`。
- 实际修改更新 `content_updated_at`；`updated_at` 仅是内容更新时间的兼容别名。
- `last_weight_date` 显式按 `Asia/Shanghai` 计算，绝对时间统一保存为 UTC ISO 8601。
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
- 权重未达到阈值：事务内直接删除正文行和关联事件。
- 主动遗忘同样直接删除，不保留 tombstone 或删除历史。

## Prompt 注入

- 永久层全部注入，按逻辑文件名稳定排序。
- `memory_temporary_important.md` 是独立单文件，受字符上限控制，不参与普通权重。
- 临时三层按 `half_year → one_month → seven_days` 注入。
- 层内按 weight 降序、文件名自然排序；使用时间或文件系统修改时间不得打乱同权重稳定顺序。
- `memory.temporary_injection_limits` 只限制单次 Prompt，不限制磁盘存储数量。

## 数据与执行边界

- `memory.extraction_mode` 控制提取边界；默认 `compression_only` 只在保存或上下文压缩时处理延期轮次。
- 后台提取只向 `self_improve` 传入用户消息；助手回复、推理、工具结果和 `important` 层均不可作为提取来源。
- 后台 `context_compression` / `memory_promotion` 禁止搜索 `important`；用户或主智能体主动查看记忆时保留只读权限，且查看不加权。
- 所有保存与压缩入口共用连续 `memory_processed_round` 游标；`context_manage` 只负责摘要，不重复持久化同一轮记忆。
- 成功提交的对话才能产生记忆候选；失败、取消或未提交轮次不得写入。
- 用户明确要求长期记住的有效内容直接进入永久层。
- 密码、API Key、Token、Cookie、私钥、验证码等敏感凭据禁止入库。
- 加权、晋升、融合、删除、幂等结果和热画像来源使用 SQLite 事务原子提交。
- 当前阶段不调用或修改 `E:\code\kemo-graph`。
