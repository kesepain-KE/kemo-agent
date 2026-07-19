# context_manage

上下文压缩与记忆提取子代理。负责三种触发场景下的对话压缩，以及将裁剪内容移交 self_improve 提取记忆。

所有阈值均从 `config/global_config.json` → `agents` 节点读取，不做硬编码。

---

## 一、轮次上限压缩

### 触发条件

当前对话轮次 ≥ `agents.max_rounds`。

### 流程

1. 读取 `agents.rounds_after_compression`，计算裁剪量 = `max_rounds - rounds_after_compression`
2. 裁剪掉最旧的 N 轮对话
3. **先将裁剪下来的完整轮次传递给 self_improve**，由 self_improve 从中提取记忆候选
4. 等 self_improve 返回后，自身将裁剪的轮次压缩为**一轮对话数据**（事实 + 需求 + 决策 + 未完成事项 + 实体 + 简短叙述），拼接在被保留的最旧轮次之后
5. 处理完毕

### 数据读写规则

- **读取来源**：`users/<name>/history/temp/`（前端临时层）的轮次计数
- **写入目标**：压缩后的摘要轮次写入 `temp/`；archive（`users/<name>/history/YYYY-MM-DD-HH-MM/`）保持完整不动

---

## 二、Token 预算超限压缩

### 触发条件（三种方式，任一满足即触发）

| 方式 | 说明 |
|------|------|
| 方式一 | 当前上下文估算 Token ≥ `agents.token_limit` |
| 方式二 | LLM API 返回 `context_length_exceeded`（上游 400 → 网关映射为 `PROVIDER_BAD_RESPONSE` / 502），由 engine 捕获后触发 |
| 方式三 | 用户主动点击 `/compress` |

### 流程

1. 读取 `agents.token_compression_ratio`
2. 计算压缩目标：`（当前总 Token - 系统提示词 Token）× token_compression_ratio`
3. 以压缩目标为准，从旧到新截取需要裁剪的完整轮次
4. **先将裁剪的轮次传递给 self_improve**，提取记忆候选
5. 等 self_improve 返回后，自身将裁剪的轮次压缩为**一轮对话数据**，拼接在被保留的最旧轮次之后
6. 处理完毕

---

## 三、工具日志与思考过程压缩

### 触发条件

逐轮检查：当某一轮对话与最新一轮的距离超过 `agents.conserved_rounds` 时触发。

即：从最旧轮次开始，只处理刚好超出保护范围的那一轮。每轮对话生成后触发一次检查。

### 流程

1. 找到需要被压缩的那一轮对话
2. 将其思考过程（reasoning/think）和工具调用日志（tool_calls）压缩为一个简短的工具与思考摘要
3. 摘要写入该轮在 temp 层的 thinking 位置
4. 该轮的工具日志标记为空
5. 处理完毕

### 数据读写规则

- **读取**：完整归档 `users/<name>/history/YYYY-MM-DD-HH-MM/`（不修改）
- **写入**：仅写入 `users/<name>/history/temp/`（前端临时层）
- archive 保持每轮完整的思考过程和工具日志不动，前端 UI 展示时从 archive 读取原始工具日志，但 temp 层已被压缩

---

## 四、双存储架构说明

每段新对话同时写入两处：

| 位置 | 用途 | 是否可修改 |
|------|------|------------|
| `users/<name>/history/YYYY-MM-DD-HH-MM/` | 完整归档，保留全部对话文本、思考日志和工具调用，不做任何裁剪 | 不可修改 |
| `users/<name>/history/temp/` | 前端临时运行层，可被压缩修改 | 可修改 |

当用户很久之后切回历史对话时，加载的是 archive 的完整版本。temp 只服务于当前正在运行的对话。

---

## 五、调用 self_improve 的方式

由 `executor.py` 在摘要模型调用前同步调用 self_improve，传入裁剪的完整轮次数据；记忆候选完成持久化后，context_manage 才继续生成摘要。该顺序由运行时强制保证，不依赖模型自行决定是否调用工具。

---

## 六、摘要输出格式

压缩为一轮对话数据时，输出以下结构：

```json
{
  "facts": ["确认的客观事实"],
  "requirements": ["用户提出的需求"],
  "decisions": ["已达成的决定"],
  "unfinished": ["尚未完成的事项"],
  "tool_results": ["关键工具调用结果"],
  "entities": ["涉及的人/项目/文件等"],
  "narrative": "简短叙述摘要"
}
```

此摘要作为一轮 assistant 消息，拼接在被保留的最旧轮次之后。
