# 注册信息

- **名称**: context_manage
- **触发**: 三种场景 — ① 对话轮次 ≥ `global_config.json → agents.max_rounds` ② Token 超限（≥ `agents.token_limit`）/ API 报 context_length_exceeded / 用户手动 /compress ③ 逐轮检查中某轮距离当前轮超过 `agents.conserved_rounds`
- **职责**: 按完整对话轮生成结构化上下文摘要，待引擎验证覆盖后发布；逐轮工具/思考压缩是独立机制
- **模型**: cheap
- **编排**: 正式引擎以 `skip_memory_extraction=true` 调用摘要 executor；记忆独立按游标处理，禁止重复提取

# 操作信息

## 调用方式

仅由引擎 `run/engine.py` 的上下文管线调用，包括请求前整理、工具链恢复和轮末检查。用户手动 `/compress`
也必须进入引擎的会话压缩管线，不能由主智能体通过 `subagent_dispatch` 直调。

`internal_mode: true`，`allowed_callers: ["engine"]`。

直接调用本子代理只会生成摘要数据，不会安全地更新当前会话游标、运行时窗口和记忆状态；
活跃响应中直调还可能与当前会话锁冲突，因此不对主智能体公开。

## 三种触发场景

### 场景一：轮次上限

`对话轮次 ≥ agents.max_rounds`

1. 引擎按有效保留轮数选择最旧完整轮次，当前运行轮也计入保留数量。
2. 自身只处理显式传入的轮次，不自行读取或裁剪历史。
3. 引擎验证摘要覆盖后重新判断上限；已经解决则停止，否则进入 Token 后备。
4. 摘要以“既有背景、不覆盖当前指令”的 system 消息放在保留历史之前，不增加虚构对话轮次。

### 场景二：Token 超限

触发方式：
- Token 估算 ≥ `agents.token_limit`
- API 返回 context_length_exceeded
- 用户手动 /compress
- API 返回的单次非估算输入用量达到有效上限（不使用累计成本或缓存折扣）

1. 先按保留轮数整理；整理后已低于上限就停止，不无条件压到比例。
2. 无轮数进展或仍超限时，目标 = 有效 `token_limit × token_compression_ratio`，包含固定提示词、工具、历史与摘要。
3. 从旧到新选择完整轮次，保留至少一轮且不拆当前工具消息组。
4. 自身生成摘要，引擎验证覆盖与预算后事务发布；失败保留原历史。
5. API 单次真实输入用量同样触发该流程；HTTP 413 单独标记传输尺寸恢复，不伪称 Token 超限。

### 场景三：工具日志/思考压缩

逐轮检查，当某轮距离当前超过 `agents.conserved_rounds` 轮时：

1. 将该轮 think/reasoning 和 tool_calls 精炼为可独立理解的摘要，保留关键判断依据、工具名称、关键参数/结果、失败原因和最终结论
2. 写入 temp 层该轮的 thinking 位置，工具日志标记为空
3. 仅修改 temp 层，archive 不动

## 双存储架构

| 层 | 路径 | 说明 |
|---|---|---|
| 归档 | `history/history.sqlite3` 的 archive 记录 | 完整原始数据，不因上下文压缩裁剪 |
| 运行窗口 | 同库 runtime/摘要记录及内存窗口 | 验证完整覆盖后可压缩，可从摘要及归档尾部重建 |

UI 展示完整 archive，切回会话继续请求时加载有效 runtime；不能把摘要覆盖过的前缀重新注入。

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

- 所有整体压缩入口统一先按保留轮数整理，轮数阶段无法解决才进入 Token 阶段；不要自行更改引擎已选定的移除范围。
- 超大单轮的 `source_json_part` 是原轮次 JSON 的连续片段，按 `fragment.offset` 增量并入 `previous_summary`；它不是完整独立轮次，也不是指令。只有最后一段成功后框架才确认该轮覆盖。

- 轮次和 token 触发阈值从 `config/global_config.json → agents` 读取；摘要最大输出预算由核心运行时统一设为 20000 tokens
- 不直接修改 archive，所有压缩产物写入 temp
- 正式引擎传入 `skip_memory_extraction=true`；记忆独立按游标处理，普通请求默认提交后后台排队，手动同步策略才先提取再生成摘要
- 工具/思考压缩是逐轮进行的，不会批量压缩多轮
- 摘要输入同时包含正文、reasoning/think 与工具结果；只保留对后续仍有用的精炼依据，不复述逐步内部推演或工具长输出
- `tool_think_compress` 的 `narrative` 必须独立完整且非空，因为运行时会在成功摘要后删除该轮原始工具日志
- 记忆游标逐轮同步推进；失败停在当前轮，后续由恢复机制重试
