# 注册信息

- **名称**: context_manage
- **触发**: 三种场景 — ① 对话轮次 ≥ `global_config.json → agents.max_rounds` ② Token 超限（≥ `agents.token_limit`）/ API 报 context_length_exceeded / 用户手动 /compress ③ 逐轮检查中某轮距离当前轮超过 `agents.conserved_rounds`
- **职责**: 按完整对话轮执行上下文压缩与记忆提取编排 — 裁剪旧轮次 → 调 self_improve 提取记忆 → 自身生成结构化摘要写回历史；逐轮压缩工具日志与思考过程
- **模型**: cheap
- **编排**: executor 在摘要生成前同步调用 self_improve，并等待记忆候选持久化完成

# 操作信息

## 调用方式

由引擎 `run/engine.py` 在上下文选择阶段自动调用，也可由主智能体通过 `subagent_dispatch` 主动调用。

`allowed_callers: ["main_agent", "engine"]`。

## 三种触发场景

### 场景一：轮次上限

`对话轮次 ≥ agents.max_rounds`

1. 读取 `agents.rounds_after_compression`，裁剪量 = `max_rounds - rounds_after_compression`
2. 裁剪最旧 N 轮 → 调用 self_improve 提取记忆
3. 自身将裁剪轮次压缩为一轮对话摘要，拼接到保留的最旧轮次之后

### 场景二：Token 超限

三种触发方式：
- Token 估算 ≥ `agents.token_limit`
- API 返回 context_length_exceeded
- 用户手动 /compress

1. 读取 `agents.token_compression_ratio`，目标 = (总 Token - 系统提示词 Token) × 比例
2. 从旧到新裁剪完整轮次直到满足目标
3. 调用 self_improve → 自身生成摘要 → 写回

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
- executor 必须先同步调 self_improve 并持久化记忆候选，再生成摘要（确保记忆不丢失）
- 工具/思考压缩是逐轮进行的，不会批量压缩多轮
- self_improve 调用是同步的，等其返回后才继续
