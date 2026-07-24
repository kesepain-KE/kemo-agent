---
type: component
project: kemo-agent
domain: run
module: run-context_summary
layer: L2
scope: project
status: active
summary: run/context_summary.py — 上下文摘要缓存（skip_memory_extraction）
source: "run/run-context_summary.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, run, 上下文, 摘要, 缓存, 子代理, skip_memory_extraction]
---
# run/context_summary.py — 上下文摘要缓存

`E:\code\kemo-agent\run\context_summary.py`

## 概览

为被移除的历史轮生成结构化摘要，通过 **context_manage** 子代理执行（`token_condense` 已于 2026-07-21 废弃删除，所有压缩统一由 context_manage 处理）。SHA-256 缓存命中直接复用，生成失败时保留旧缓存。

## 类

### SummaryError

`SummaryError(RuntimeError)` — 摘要错误。

## 函数

### summary_source

```python
def summary_source(groups: list[RoundGroup]) -> list[dict]
```

将 RoundGroup 列表转换为子代理可接受的 rounds 格式。

### source_hash

```python
def source_hash(groups: list[RoundGroup]) -> str
```

SHA-256 哈希，用于缓存命中判断。

### _read_cache

```python
def _read_cache(path: Path) -> dict | None
```

读取缓存文件，校验 schema_version，normalise summary。

### _atomic_write

```python
def _atomic_write(path: Path, value: dict) -> None
```

tmp + os.replace 原子写入。

### build_summary_message

```python
def build_summary_message(cache: dict | None) -> dict | None
```

将缓存构建为 system 消息注入上下文。

### get_or_create_summary

```python
def get_or_create_summary(
    *,
    cache_path: Path,
    groups: list[RoundGroup],
    agent_runner: AgentRunner,
    agent_name: str,           # "context_manage"（token_condense 已废弃）
    trigger: str,              # "round" / "token" / "manual"
    cancel_event=None,
    chunk_token_budget=24000,
    max_tokens=2048,
    response_hook=None,
    event_callback=None,
    skip_memory_extraction=False,  # 2026-07-23 新增
) -> tuple[dict | None, dict]
```

**流程**：
1. 计算 source_hash → 查缓存
2. 命中 → 返回（cache_hit=True）
3. 未命中 → 构建 `model_input`，`skip_memory_extraction=True` 时注入到输入数据
4. 分块调用子代理生成摘要
5. 失败 → 返回 None + diagnostics，保留旧缓存

**skip_memory_extraction 说明**（2026-07-23 新增）：
- 引擎在压缩边界先调用 `_extract_memory_backlog` 完成记忆提取，再调用 `get_or_create_summary` 并传入 `skip_memory_extraction=True`
- context_manage executor 收到此标记后跳过记忆提取调用，只负责生成摘要
- 避免引擎游标与 context_manage 重复提取同一批轮次

### _chunks

```python
def _chunks(groups, token_budget) -> list[list[RoundGroup]]
```

按 token 预算分块，确保每块不拆轮。

### _normalise_summary

```python
def _normalise_summary(value) -> dict
```

标准化摘要 7 字段（facts/requirements/decisions/unfinished/tool_results/entities/narrative）。
