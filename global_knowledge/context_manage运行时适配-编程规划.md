# context_manage 运行时适配 — 编程规划

> 目标：将 context_manage 子代理的三种新压缩机制落地到引擎代码中。
>
> 输出给编程 agent。不修改代码，只输出结构化规划。

---

## 一、问题

当前 `run/engine.py` 的压缩流程是单一分支：轮次或 Token 触发后，调 context_manage（或 token_condense）生成摘要。新设计引入三个独立场景，且 context_manage 需要编排 self_improve。以下 4 项未实现：

| # | 问题 | 现状 |
|---|------|------|
| 1 | 压缩前须调 self_improve 提取记忆，当前 engine 未编排此步骤 | 直接调 context_manage 生成摘要，跳过 self_improve |
| 2 | 工具日志/思考过程逐轮压缩（`conserved_rounds` 机制）完全不存在 | `build_round_groups` 中旧轮次的 tool_result 只做字符截断，不生成思考摘要、不修改 temp 层 |
| 3 | API 返回 context_length_exceeded 时自动触发 Token 压缩未实现 | engine 的 provider 错误直接暴露给用户，不触发压缩重试 |
| 4 | 压缩产物写入 temp 但不动 archive 的规则未落地 | 历史提交 `append_round_items` / `commit_window` 不区分 temp 和 archive |

---

## 二、方案

### 总体策略

**在 engine.py 中新增三个独立处理函数**，不修改 `select_context` 和 `build_round_groups` 的核心裁剪逻辑（它们继续负责"裁剪哪些轮次"的计算）。新增函数负责"裁剪后做什么"。

### 架构变化

```
当前流程:
  select_context → 裁剪轮次 → context_manage/token_condense → 摘要写回

新流程:
  ┌─ 场景一（轮次上限）: select_context → 裁剪轮次 → self_improve → context_manage → 摘要写回
  ├─ 场景二（Token超限）: select_context → 裁剪轮次 → self_improve → context_manage → 摘要写回
  ├─ 场景三（工具日志）: 逐轮检查 → 压缩 think+tool_calls → 写 temp
  └─ 场景二-方式二（API报错）: catch context_length_exceeded → 触发场景二
```

### 关键设计决策

| 决策 | 说明 |
|------|------|
| context_manage 内部编排 self_improve | 不需要 engine 额外调用 self_improve，由 context_manage 通过 subagent_dispatch 工具自己调用。engine 只需确保 context_manage 的 prompt 中明确指示"先调 self_improve，再生成摘要" |
| 工具日志压缩只写 temp | 新增 `_compress_tool_think_round()` 函数，修改 `users/<name>/history/temp/` 中指定轮次的 think 和 tool 数据，不动 archive |
| API 错误捕获位置 | 在 engine 的主对话循环的 provider 调用周围（`_provider_events` 的调用方），捕获 `PROVIDER_BAD_RESPONSE`（HTTP 502），设置 `force_compress=True` 并重试本轮请求 |

---

## 三、详细规划

### 步骤 1：修改 context_manage 的 AGENT.md prompt（已完成）

✅ context_manage 的 `AGENT.md` 已更新为包含三种场景和 self_improve 编排指令。在步骤 3 的 engine 侧 prompt 修正时确认注入即可。

---

### 步骤 2：新增工具日志/思考压缩函数

**文件**：`E:\code\kemo-agent\run\engine.py`

在 engine 模块中新增 `_compress_per_round_tool_think()`：

```python
def _compress_per_round_tool_think(
    window_path: Path,
    window: dict[str, Any],
    conserved_rounds: int,
    agent_runner: AgentRunner,
) -> None:
    """
    逐轮检查：对距离当前超过 conserved_rounds 的轮次，
    压缩其思考过程和工具调用日志，写入 temp 层。
    """
    think_rounds = (window.get("think") or {}).get("rounds", [])
    tool_rounds = (window.get("tool") or {}).get("rounds", [])
    
    # 以 think 或 tool 中最大的轮次号为准
    all_numbers = set()
    for item in think_rounds:
        if isinstance(item, dict):
            all_numbers.add(item.get("round", 0))
    for item in tool_rounds:
        if isinstance(item, dict):
            all_numbers.add(item.get("round", 0))
    
    if not all_numbers:
        return
    
    latest_round = max(all_numbers)
    
    for round_number in sorted(all_numbers):
        if latest_round - round_number <= conserved_rounds:
            continue  # 在保护范围内，跳过
        
        # 收集该轮的 think 和 tool 数据
        think_data = next(
            (item for item in think_rounds if isinstance(item, dict) and item.get("round") == round_number),
            None,
        )
        tool_data = next(
            (item for item in tool_rounds if isinstance(item, dict) and item.get("round") == round_number),
            None,
        )
        
        if not think_data and not tool_data:
            continue
        
        # 调 context_manage（或专用轻量模式）压缩
        # 传入该轮的 think 和 tool 数据
        result = agent_runner.run(
            "context_manage",
            {
                "previous_summary": None,
                "rounds": [{
                    "round": round_number,
                    "think": think_data,
                    "tool": tool_data,
                }],
                "trigger": "tool_think_compress",
            },
        )
        
        # 将摘要写回 temp 层该轮的 thinking 位置
        if think_data:
            think_data["summary"] = result.data.get("narrative", "")
            think_data["compressed"] = True
        if tool_data:
            tool_data["calls"] = []  # 清空工具日志
            tool_data["compressed"] = True
        
        # 写入 temp 层
        temp_think_path = window_path / "think.json"
        temp_tool_path = window_path / "tool.json"
        if temp_think_path.parent.exists():
            temp_think_path.write_text(json.dumps({"rounds": think_rounds}, ensure_ascii=False), "utf-8")
        if temp_tool_path.parent.exists():
            temp_tool_path.write_text(json.dumps({"rounds": tool_rounds}, ensure_ascii=False), "utf-8")
```

**注意**：此处 `window_path` 指向 `users/<name>/history/temp/`，而非 archive。函数的调用位置应在每轮对话完成后、commit_window 之前。

---

### 步骤 3：修改引擎的上下文压缩流程

**文件**：`E:\code\kemo-agent\run\engine.py`

当前第 708-749 行的压缩循环需修改：

**3.1 统一使用 context_manage**

新设计中不再区分 context_manage 和 token_condense，所有压缩场景统一使用 context_manage（它内部根据 trigger 值决定执行哪种压缩逻辑）：

```python
# 第 712-721 行改为：
summary_agent = "context_manage"  # 统一使用 context_manage
summary_trigger = (
    "token_limit"
    if context_selection.token_limit_triggered
    else ("manual" if force_compress else "round_limit")
)
```

**3.2 在 engine 中注入"先调 self_improve"的指令**

context_manage 的 system prompt（AGENT.md）已经包含 self_improve 编排指令。但 engine 在 `get_or_create_summary` 中调用 `agent_runner.run` 时，agent_runner 会使用 `build_agent_prompt_bundle` 构建 prompt。需确认 `build_agent_prompt_bundle` 能正确读取更新后的 AGENT.md 内容。

**无需额外修改** — `build_agent_prompt_bundle` 通过 `definition.instruction` 读取 AGENT.md，文件更新后自动生效。

---

### 步骤 4：工具日志压缩的触发位置

**文件**：`E:\code\kemo-agent\run\engine.py`

在 `commit_window` 之前（或每一轮对话的 assistant 回复生成后），新增工具日志压缩调用：

```python
# 在对话主循环中，每轮 tool_calls 执行完毕后、下一轮请求前：
conserved_rounds = int(config.get("agents", {}).get("conserved_rounds", 3))
_compress_per_round_tool_think(
    window_path=window_path,
    window=window,
    conserved_rounds=conserved_rounds,
    agent_runner=agent_runner,
)
```

精确触发时间：在 `append_round_items` 写入本轮数据后，下一轮 `select_context` 前。

---

### 步骤 5：捕获 context_length_exceeded 错误

**文件**：`E:\code\kemo-agent\run\engine.py`

在主对话循环的 provider 调用处，捕获网关返回的 context_length_exceeded 错误并触发 Token 压缩重试。

#### 5.1 识别 context_length_exceeded

网关协议中，上游 400（context_length_exceeded）映射为 HTTP 502 `PROVIDER_BAD_RESPONSE`。在 `_provider_events` 或 `_run_events_for_protocol_event` 中，当遇到以下错误事件时标记为"上下文超限"：

```python
# 在 _run_events_for_protocol_event 中，处理 RESPONSE_FAILED 时：
elif event.type == StreamEventType.RESPONSE_FAILED:
    error = event.response.error if event.response else None
    error_type = error.type if error else ""
    error_code = error.code if error else ""
    
    # 判断是否为 context_length_exceeded
    if error_code == "PROVIDER_BAD_RESPONSE" and error.provider_status == 400:
        raise ContextLengthExceededError(
            f"上下文超限（上游 400），需 Token 压缩后重试"
        )
```

#### 5.2 新增异常类

```python
class ContextLengthExceededError(EngineError):
    """LLM API 返回 context_length_exceeded，需触发 Token 压缩"""
```

#### 5.3 在引擎主循环中捕获并重试

```python
# 在 provider 调用周围：
max_compress_retries = 2
for retry in range(max_compress_retries + 1):
    try:
        for event in _provider_events(provider, chat_request, protocol_request):
            yield event
        break  # 成功，退出重试循环
    except ContextLengthExceededError:
        if retry >= max_compress_retries:
            raise  # 重试耗尽
        # 强制 Token 压缩
        force_compress = True
        context_selection = select_context(
            window=window, policy=context_policy,
            system_message=system_message,
            current_user_message=current_user_message,
            tools=tool_schemas, force_compress=True,
        )
        # 执行压缩（含 self_improve）
        summary_cache, _ = get_or_create_summary(...)
        # 重建消息
        messages = context_selection.messages
        # 重新构建 chat_request，继续循环
        chat_request = _build_chat_request(...)
```

**注意**：流式响应（SSE）场景下，`RESPONSE_FAILED` 事件可能在已产生部分文本后才到达。此时已产生的文本应丢弃，重新发送压缩后的请求。

---

### 步骤 6：确认双存储写入规则

**文件**：`E:\code\kemo-agent\run\history.py`

当前 `commit_window` 将对话数据写入 archive。需确认以下规则已实现：

| 操作 | 写入位置 | 说明 |
|------|----------|------|
| 每轮对话完成 | temp + archive | 正常双写 |
| 工具日志/思考压缩 | 仅 temp | archive 不动 |
| 轮次/Token 摘要 | temp | 摘要轮次注入 temp，archive 仍保留原始完整轮次 |
| 用户切回历史对话 | 读 archive | 加载完整未压缩版本 |

`commit_window` 当前可能是单写 archive。需修改为：
- 先写 temp（始终同步）
- 再写 archive（仅在非压缩操作时）

或更简单的方案：**工具日志压缩函数直接修改 temp 文件**（如步骤 2 所示），commit_window 保持写 archive 不变。这样天然实现"temp 可改、archive 不动"。

---

## 四、应达到的效果

1. **轮次上限触发** — 对话达 80 轮后，engine 调 context_manage → context_manage 先调 subagent_dispatch(self_improve) 提取记忆 → 再生成摘要写回 temp
2. **Token 超限触发** — 估算超 1,000,000 Token 或 API 返回 context_length_exceeded 或用户 /compress → 同上流程
3. **工具日志逐轮压缩** — 对话超过 3 轮后，每轮 oldest 的工具调用和思考过程被压缩为摘要写入 temp 的 thinking 位置，工具日志清空，archive 保持完整
4. **API 错误自动恢复** — LLM 返回 context_length_exceeded 时不暴露给用户，自动触发 Token 压缩并重试（最多 2 次）
5. **历史对话完整性** — 用户切回任意历史对话时，加载 archive 完整版本，压缩不会丢失信息
6. **context_manage 具备 subagent_dispatch 能力** — agent-config.json 的 `tools.plugins.allow` 已含 `subagent_dispatch`，可调用 self_improve
7. **不再需要 token_condense** — 所有压缩统一由 context_manage 处理，token_condense 可标记为废弃（保留代码但不再被 engine 调用）
