---
type: component
project: kemo-agent
domain: run
module: run-engine
layer: L2
status: active
summary: run/engine.py — 事件驱动对话引擎（双层历史 + round_offset + direct_knowledge_scopes）
source: "run/run-engine.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, run, 入口, 事件驱动, 工具循环, 记忆集成, Kemo协议, 双层历史, extraction_mode, memory_cursor, guidance_applied]
---
# run/engine.py — 事件驱动对话引擎

`E:\code\kemo-agent\run\engine.py`

## 概览

kemo-agent 的**核心入口**。执行一次完整的模型→工具→模型循环，仅在成功完成时提交历史。纯 Kemo 协议路径，无旧版 ChatResponse fallback。

## 核心入口

### iter_request_events / _iter_request_events_impl (生成器)

**完整流程**：
1. 配置加载：`load_config` + `MainAgentSourcePolicy` + **`ContextPolicy.from_config(config)`**（提前加载，供 load_runtime_window 使用）
2. Content Block 解析：`_request_content_blocks()`
3. 记忆审核：`MemoryStore.review_due()`
4. PromptBundle 构建：`build_prompt_bundle()` → 17 段固定顺序（含填充空段）
5. 上下文选择：`select_context()` + `_ensure_fixed_content_fits`
6. 摘要多轮循环
7. 工具循环：
   - 编译 KemoRequest（含 request_id/parent_request_id/attempt/metadata）
   - `_provider_events(provider, protocol_request)` → 纯 Kemo 协议
   - 运行中引导、工具去重
   - knowledge_scopes 使用 `source_policy.direct_knowledge_scopes()`（被图谱替换的 scope 不传给工具）

### context_status

与 iter_request_events 类似，提前加载 `ContextPolicy.from_config(config)` 并传入 `max_rounds` 给 `load_runtime_window`。

## 关键变更（2026-07-22）

### 1. ContextPolicy 提前加载

旧版在 prompt 构建后才加载 ContextPolicy。新版在窗口加载前就加载，因为 `load_runtime_window` 需要 `max_rounds` 参数。

### 2. load_runtime_window 传入 max_rounds

```python
runtime_path, window = load_runtime_window(
    window_path, archive_window, max_rounds=context_policy.max_rounds
)
```

### 3. _copy_committed_round_to_archive 双轮号

```python
def _copy_committed_round_to_archive(
    archive_window, runtime_window,
    runtime_round_number,    # temp 中的局部轮号
    archive_round_number,     # 归档中的绝对轮号
) -> None
```

归档使用绝对轮号，temp 使用局部轮号（可能被 `_trim_to_max_rounds` 裁剪过）。归档不再保存 `context` 诊断。

### 4. _trim_to_max_rounds 提交

```python
commit_window(runtime_path, _trim_to_max_rounds(window, context_policy.max_rounds))
```

提交 temp 工作区时先裁剪到 max_rounds，保持局部轮号连续。

### 5. round_offset / workspace_rounds

```python
window["data"]["context"] = {
    ...,
    "round_offset": max(0, archive_round_number - round_number),
    "workspace_rounds": round_number,
    "summary_cache": ...,
}
```

`round_offset` 记录 temp 局部轮号与归档全局轮号的偏移。

### 6. direct_knowledge_scopes

`knowledge_scopes` 传给工具时使用 `source_policy.direct_knowledge_scopes()`，不包含被 kemo-graph 替换的 scope。

### 7. 流式输出修复（2026-07-22）

旧版：Provider 事件流先用 `list(_provider_events(provider, protocol_request))` 全量缓冲到内存，然后再逐条 yield，导致 SSE 无法实时推送。

新版：直接迭代 Provider 生成器，`text_delta` / `reasoning_delta` / `tool_call_start` / `usage` / `error` / `done` 实时 yield。`all_text` 和 `all_reasoning` 在迭代完成后统一追加。保留 `context_length_exceeded` 拦截。

## 关键变更（2026-07-22 追加）

### 8. Provider 请求槽位（provider_request_slot）

主智能体的 LLM API 调用被 `provider_request_slot(config, cancel_event=cancel_event)` 包裹。这实现了进程级 Provider 并发总闸：

- 获取信号量后才向 Provider 发送请求
- 超时抛 `ProviderCongestionError`，引擎捕获后 yield error_event 并终止
- 取消事件优先于信号量等待
- 工具执行期间不占用槽位（引擎循环中 yield 工具事件时不在 slot 内）

### 9. 记忆提取游标管线（_extract_memory_backlog）— 2026-07-23 重构

新增 `_extract_memory_backlog()` 函数，替代旧的 `_extract_round_memory` 单轮提取，改为**连续游标逐轮提取**：

```python
def _extract_memory_backlog(*, root, user, source, session_id, directory, window, config, agent_runner, cancel_event) -> dict
```

- 沿 `memory_processed_round` 游标从 `processed_round+1` 到 `rounds` 逐轮提取
- 每轮调用 `_extract_round_memory` → `self_improve` 子代理
- 每轮完成后立即持久化游标和状态到 `data.json` 和 `history_index`
- 失败停在当前轮，后续由 Maintenance 恢复机制重试
- 提取时机由 `memory_extraction_mode(config)` 控制：
  - `compression_only`：压缩边界前调用，普通提交只登记 `deferred`
  - `background`：由 Maintenance 领取 `pending` 轮次
  - `on_commit`：提交后同步提取
  - `disabled`：不提取
- 压缩边界调用后，向 `context_manage` 传入 `skip_memory_extraction=True`，避免重复提取

### 10. 空摘要推理保留

`_compress_per_round_tool_think` 中，当 `summary` 为空字符串时，保留原始 reasoning item 原文不替换，避免丢失推理内容。

### 11. transport_registry 透传

request payload 中新增 `_transport_registry` 字段，从 request 透传到工具上下文，使外部消息插件工具能访问 Transport 注册表。

### 12. guidance_applied 事件 — 2026-07-23 新增

新增 `guidance_applied` 事件类型。当引导消息（guidance）被应用到 Provider 请求后，引擎发出此事件确认引导已被消费。使用 `pending_guidance_ack` 机制：引导先加入待确认队列，在下一轮 Provider 事件开始前转为已消费并 yield `guidance_applied` 事件。

### 13. memory_status 多态化 — 2026-07-23 更新

历史提交后的 `memory_status` 不再只有 `processing`/`pending` 两态，而是根据 `extraction_mode` 分配不同初始状态：

| extraction_mode | 初始 memory_status | 说明 |
|---|---|---|
| `on_commit` + 当前轮 | `processing` | 立即同步提取 |
| `on_commit` + 非当前轮 | `pending` | 排队等待 |
| `background` | `pending` | 由 Maintenance 领取 |
| `compression_only` | `deferred` | 延迟到压缩/保存时提取 |
| `disabled` | `disabled` | 不提取 |

## 变更记录

| 旧版 | 新版 |
|------|------|
| ContextPolicy 在 prompt 构建后加载 | 在 load_runtime_window 前加载 |
| `_copy_committed_round_to_archive` 单轮号 | 分为 runtime_round_number 和 archive_round_number |
| commit temp 不裁剪 | `commit_window` 前调用 `_trim_to_max_rounds` |
| context 无 round_offset | 新增 round_offset 和 workspace_rounds |
| knowledge_scopes 直传 | 使用 `direct_knowledge_scopes()` 过滤被替换的 scope |
| Provider 事件 `list()` 全量缓冲 | 直接迭代生成器实时 yield SSE |
| `tools.max_per_round` 软上限+tool_pause 暂停 | 彻底移除 max_per_round；工具仅受 `max_iterations` 硬上限和连续失败移除保护 |
| `tool_calls_this_round` 计数器 + `tool_pause` 状态 | 删除计数器和暂停状态；done 事件不再包含 `awaiting_tool_confirmation` / `tool_pause` |
| `max_iterations` 默认 8 | 默认 80（global_config.json 已更新） |
| 无 Provider 并发控制 | `provider_request_slot` 包裹 LLM 调用，进程级信号量 |
| 无提交后记忆提取 | `_extract_memory_backlog` 按 extraction_mode 提取，连续游标逐轮推进 |
| extraction_mode="context_compression" | extraction_mode 由 `memory_extraction_mode()` 控制，支持四种模式 |
| 空摘要时替换 reasoning 为空 | 保留原始 reasoning item 原文 |
| 无 transport_registry 透传 | request._transport_registry 传给工具上下文 |
| 无 guidance_applied 事件 | guidance 消费时 yield guidance_applied 事件 |
| memory_status 只有 processing/pending | 按 extraction_mode 分配 processing/pending/deferred/disabled |
| 无 task_plan 上下文注入 | request._task_plan_id/step_id/mode 传给工具上下文 |
## 关键变更（2026-07-26）

### 14. 取消轮次持久化（commit_cancelled_round）

新增 `commit_cancelled_round()` 内部闭包函数，当用户紧急停止时完整持久化已部分执行的轮次：

- 将所有 `pending_tool_calls`（尚未完成的工具调用）标记为 `cancelled`
- 在文本末尾追加 `[本轮已由用户紧急停止]` 标记
- 写入归档窗口（含 tool_records、reasoning、provider_responses）
- 写入 `round_metrics` 含 `status="cancelled"`、`cancel_reason="user_emergency_stop"`
- 调用 `_trim_to_max_rounds` 后提交
- 更新 run_state = idle，释放历史注册
- 返回 `type="done"` 的事件，metadata 含 `cancelled=True`

引擎中所有 `cancel_event.is_set()` 检查点都改为先调用 `commit_cancelled_round()` 再 return，确保取消不会丢失数据。

### 15. 新增 ConsecutiveIdenticalToolCallTracker

```python
identical_calls = ConsecutiveIdenticalToolCallTracker(identical_call_limit)
```

- 每次工具调用前记录签名并检查连续次数
- 超过 `consecutive_identical_call_limit`（默认 8）时阻止执行
- 返回状态 `identical_call_blocked`，错误信息包含 `instruction` 指导模型修改参数
- 工具名称或参数变化重置计数

### 16. 新增 reasoning_effort 支持

```python
extra={
    "reasoning_effort": runtime_provider["reasoning_effort"]
}
```

- Provider 配置新增 `reasoning_effort` 字段（`minimal`/`low`/`medium`/`high`/`max`）
- 默认 `medium`，缺失或非法值回退为 `medium`
- 在 ChatRequest.extra 中传递，通过 compat 层写入 KemoRequest 的 reasoning 和 provider_options

### 17. Context Guard（上下文保护）

在工具循环的每次迭代前，估算 provider 输入 token 数：

```python
projected_tokens = max(0, last_provider_input_tokens + incremental_tokens)
if projected_tokens > context_policy.token_limit:
    yield error_event(EngineError(...))
    return
```

- 使用 `last_provider_input_tokens`（真正的 provider 输入 token 数） + local 增量估算
- 首次迭代使用 `current_local_tokens` 本地估算
- 超出上限时发送 `context_guard` 诊断信息（measurement / 各阶段估算值 / latest_tools）

### 18. uploaded_file_context

```python
def _uploaded_file_context(request) -> str
```

- 解析 `request.uploaded_files` 数组（path/name/size）
- 拼装为 `[本轮用户上传文件]` 上下文块
- 通过 `provider_content_blocks` 在 `_content_for_message` 前注入
- 使引擎无需单独调用 file 工具即可知道上传的文件路径

### 19. memory_extraction_policy = "queue"

```python
memory_extraction_policy = str(request.get("memory_extraction_policy") or "sync")
```

- `sync`：同步提取（旧行为）
- `queue`：仅登记后台队列，不阻塞响应
- 当 `compress_only=True` + `memory_extraction_policy="queue"` 时，跳过 `_extract_memory_backlog`，改为调用 `queue_memory_extraction` 登记后台提取任务

### 20. _extract_memory_backlog 取消轮次处理

```python
cancelled_rounds = {
    int(item.get("round") or 0)
    for item in data.get("round_metrics", [])
    if isinstance(item, dict) and item.get("status") == "cancelled"
}
```

- 归档中标记为 `cancelled` 的轮次跳过记忆提取
- 自动更新 `memory_processed_round` 和 `memory_status`

### 21. _extract_round_memory 拆分为分析/持久化

```python
analysis = _analyze_round_memory(...)       # 只分析，不写数据
result = _persist_round_memory_analysis(...)  # 在 locked 外持久化
```

### 22. 工具上下文诊断（_tool_context_diagnostics）

```python
def _tool_context_diagnostics(tool_records, *, iteration) -> list[dict]
```

- 调试工具在触发 context_guard 时附带
- 报告每个工具的 name、argument_chars、result_chars、status、action、path

## 变更记录（追加）

| 旧版 | 新版 |
|------|------|
| 取消时直接 return，丢失本轮状态 | 调用 `commit_cancelled_round()` 完整持久化取消轮次 |
| 无相同调用检测 | `ConsecutiveIdenticalToolCallTracker` 阻止无限重复调用 |
| 无 reasoning_effort | Provider 配置支持五种思考强度 |
| 无 context_guard（仅靠本地估算） | 使用 provider 实际 input_tokens + 增量估算，超上限时停止并输出诊断 |
| 无上传文件上下文 | `uploaded_file_context` 自动注入文件路径块 |
| memory_extraction_policy 固定 sync | 支持 queue 模式，登记后台不阻塞 |
| 取消轮次不跳过记忆提取 | cancelled_rounds 自动跳过 |
| _extract_round_memory 锁内一体 | 分析/持久化分离，减少锁争用 |
