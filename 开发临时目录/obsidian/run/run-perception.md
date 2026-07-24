---
type: component
project: kemo-agent
domain: run
module: run-prompt_sources
layer: L3
scope: project
status: active
summary: run/prompt_sources.py — PromptSourceRegistry 感知注入选择器
source: "run/run-perception.md"
updated: 2026-07-18
verified: true
tags: [kemo-agent, run, perception, prompt_sources, 感知]
corrections: 2026-07-18 原文件名 run-perception.md 暗示 run/perception.py 存在，实际源码位于 run/prompt_sources.py；补充 allow_modules 参数说明
---

# run/prompt_sources.py — PromptSourceRegistry 感知注入选择器

`E:\code\kemo-agent\run\prompt_sources.py` → `PromptSourceRegistry`

## 概览

`perception` 是 prompt 的最后一段（第 14 段），由 `PromptSourceRegistry.select_perception()` 扫描 `global_sense/` 下的 `.md` 文件。

## 源码定位

**实际运行模块**：`run/prompt_sources.py`（非 `run/perception.py`）

感知根目录由 `global_sense/register.py` 调用 `registry.add_perception(Path)` 注册。

## 结构

### PerceptionSelection

```python
@dataclass
class PerceptionSelection:
    text: str
    source_files: tuple[str, ...]
    original_chars: int
    injected_chars: int
    original_items: int
    injected_items: int
    truncated: bool
```

## 选择逻辑

### PromptSourceRegistry.select_perception

```python
def select_perception(self, *, max_chars, mode="full",
                      allow_modules: tuple[str, ...] | None = None) -> PerceptionSelection
```

规则：

- 只支持 `full`
- `max_chars == 0` 时直接返回空
- 只读已注册根目录下的 `.md` 文件（递归至模块子目录）
- 跳过隐藏文件/目录
- 结果按自然排序
- `allow_modules` 支持按模块名过滤（白名单模式）
- 可选择列出 `perception_inventory()` 查看所有模块元数据

## 注入格式

每个文件会被包装成：

```text
[相对路径]
内容...
```

## 相关笔记

- [[run-prompt_sources]]
- [[原理-PromptBundle编排]]
- [[原理-感知与拓展]]
