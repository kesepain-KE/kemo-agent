---
type: component
project: kemo-agent
domain: run
module: run-memory_pipeline
layer: L3
scope: project
status: active
summary: run/memory_pipeline.py — 记忆提取管线
source: "run/run-memory_pipeline.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, run, 记忆, 提取, 管线]
---
# run/memory_pipeline.py — 记忆提取管线

`E:\code\kemo-agent\run\memory_pipeline.py`

## 概览

成功对话轮次结束后，通过 self_improve 子代理异步提取新记忆，原子写入存储。

## 类

### MemoryExtractionError

`MemoryExtractionError(RuntimeError)`

## 函数

### submit_memory_extraction

```python
def submit_memory_extraction(
    *,
    root, user, config,
    user_text: str,
    assistant_text: str,
    tool_results: list[dict],
    source: dict,
    provider_factory=create_provider,
) -> str
```

**流程**：
1. 检索已有记忆候选（`_existing_candidates`）
2. 通过 `agent_service.get_agent_scheduler()` 获取全局调度器
3. 提交 self_improve 子代理
4. 回调 `persist()`：从 result.data.candidates 提取 → store.upsert_candidates → store.review_due

**保护**：提取失败不影响主历史（已提交的四文件不动）。

### _existing_candidates

```python
def _existing_candidates(store, text, limit) -> list[dict]
```

检索与当前对话相关的已有记忆，避免重复提取。
