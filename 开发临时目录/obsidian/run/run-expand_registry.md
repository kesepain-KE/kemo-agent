---
type: component
project: kemo-agent
domain: run
module: run-prompt_sources
layer: L2
scope: project
status: active
summary: run/prompt_sources.py — PromptSourceRegistry 拓展注入选择器
source: "run/run-expand.md"
updated: 2026-07-18
verified: true
tags: [kemo-agent, run, expand, prompt_sources, 拓展]
corrections: 2026-07-18 原文件名 run-expand_registry.md 暗示 run/expand_registry.py 存在，实际源码位于 run/prompt_sources.py；原内容称读取 registry.json 文件，实际通过 register.py 注册机制（add_expand）
---

# run/prompt_sources.py — PromptSourceRegistry 拓展注入选择器

`E:\code\kemo-agent\run\prompt_sources.py` → `PromptSourceRegistry`

## 概览

`expand_data` 是 prompt 的独立注入段（第 13 段），由 `PromptSourceRegistry.select_expand()` 控制。

## 源码定位

**实际运行模块**：`run/prompt_sources.py`（非 `run/expand_registry.py`）

`PromptSourceRegistry` 通过 `load_prompt_source_registry()` 加载各层的 `register.py`，后者调用 `registry.add_expand(scope, module, inject_file)` 注册。

## 结构

### ExpandSelection

```python
@dataclass
class ExpandSelection:
    text: str
    source_files: tuple[str, ...]
    original_chars: int
    injected_chars: int
    original_items: int
    injected_items: int
    truncated: bool
```

## 选择逻辑

### PromptSourceRegistry.select_expand

```python
def select_expand(self, *, max_chars, mode="full", allow=None) -> ExpandSelection
```

只支持 `full`。

扫描顺序（按 scope 优先级）：

1. `global` — 由 `global_expand/register.py` 注册
2. `shared` — 由 `shared_expand/register.py` 注册  
3. `user` — 由 `users/<user>/expand/` 下自动发现

**注册方式**（非 registry.json）：

每个模块通过 `registry.add_expand(scope, module, inject_file)` 在 `register.py` 中声明。`inject_file` 必须在模块目录内，有目录逃逸检查。

### 安全检查

- `inject_file` 不得跳出模块目录（`selected.relative_to(module_dir)` 校验）
- 重复注册检测（`_expand_entries` 去重）

## 注入格式

每个注册项最终会被包装成：

```text
[scope:module]
内容...
```

## 相关笔记

- [[run-prompt_sources]]
- [[原理-PromptBundle编排]]
- [[原理-感知与拓展]]
