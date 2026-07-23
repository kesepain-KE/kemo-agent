# 注册信息

- **名称**: context_manage
- **触发**: 三种场景 — ① 对话轮次 ≥ `global_config.json → agents.max_rounds` ② Token 超限（≥ `agents.token_limit`）/ API 报 context_length_exceeded / 用户手动 /compress ③ 逐轮检查中某轮距离当前轮超过 `agents.conserved_rounds`
- **职责**: 按完整对话轮执行上下文压缩 — 引擎游标先完成延期记忆提取 → 自身生成结构化摘要写回临时历史；逐轮压缩工具日志与思考过程
- **模型**: cheap
- **编排**: 正式引擎先推进 `memory_processed_round`，再以 `skip_memory_extraction=true` 调用摘要 executor，禁止重复提取

# 操作信息

## 调用方式

仅由引擎 `run/engine.py` 在上下文选择阶段调用。用户手动 `/compress`
也必须进入引擎的会话压缩管线，不能由主智能体通过 `subagent_dispatch` 直调。

`internal_mode: true`，`allowed_callers: ["engine"]`。

直接调用本子代理只会生成摘要数据，不会安全地更新当前会话游标、运行时窗口和记忆状态；
活跃响应中直调还可能与当前会话锁冲突，因此不对主智能体公开。

## 三种触发场景

### 场景一：轮次上限

`对话轮次 ≥ agents.max_rounds`

1. 读取 `agents.rounds_after_compression`，裁剪量 = `max_rounds - rounds_after_compression`
2. **前置**：引擎沿会话游标先完成延期记忆提取（self_improve），传入 `skip_memory_extraction=true`
3. 裁剪最旧 N 轮
4. 自身将裁剪轮次压缩为一轮对话摘要，拼接到保留的最旧轮次之后

### 场景二：Token 超限

三种触发方式：
- Token 估算 ≥ `agents.token_limit`
- API 返回 context_length_exceeded
- 用户手动 /compress

1. 读取 `agents.token_compression_ratio`，目标 = (总 Token - 系统提示词 Token) × 比例
2. **前置**：引擎游标先完成延期记忆提取（self_improve），传入 `skip_memory_extraction=true`
3. 从旧到新裁剪完整轮次直到满足目标
4. 自身生成摘要 → 写回

### 场景三：工具日志/思考压缩

逐轮检查，当某轮距离当前超过 `agents.conserved_rounds` 轮时：

1. 压缩该轮的 think 和 tool_calls 为简短摘要
2. 写入 temp 层该轮的 thinking 位置，工具日志标记为空
3. 仅修改 temp 层，archive 不动

## 双存储架构

| 层 | 路径 | 说明 |
|---|---|---|
| 归档 | `history/YYYY-MM-DD-HH-MM/` | 完整原始数据，不可修改 |
| 临时 | `history/temp/` | 前端运行层，可被压缩修改 |

切回历史对话时加载 archive 完整版本。UI 展示的工具日志来自 archive。

## 输出格式

```json
{
  "facts": ["string"],
  "requirements": ["string"],
  "decisions": ["string"],
  "unfinished": ["string"],
  "tool_results": ["string"],
  "entities": ["string"],
  "narrative": "string"
}
```

## 注意事项

- 所有阈值从 `config/global_config.json → agents` 读取，不硬编码
- 不直接修改 archive，所有压缩产物写入 temp
- 正式引擎必须先持久化游标记忆提取结果，再让 executor 只生成摘要
- 工具/思考压缩是逐轮进行的，不会批量压缩多轮
- 记忆游标逐轮同步推进；失败停在当前轮，后续由恢复机制重试
