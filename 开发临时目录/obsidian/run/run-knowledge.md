---
type: component
project: kemo-agent
domain: run
module: run-knowledge
layer: L2
scope: project
status: active
summary: run/knowledge.py — 确定性轻量知识索引注入
source: "run/run-knowledge.md"
updated: 2026-07-21
verified: false
tags: [kemo-agent, run, 知识库, 索引, KnowledgeIndexSelection, KnowledgeDocument]
---
# run/knowledge.py — 确定性轻量知识索引注入

## 模块定位

全量、确定性地注入轻量知识索引文件。不做评分/排序/截断，扫描指定层级的命名索引文件（index.md / data_structure.md / 索引.md / 目录.md）并按 scope 顺序拼装。

## 所属领域

run

## 核心数据模型

### KnowledgeDocument

```python
@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    scope: str           # "user" | "shared" | "global"
    path: Path           # 索引文件路径
    relative_path: str   # 相对于 scope 根目录的路径
    title: str           # 从文件内容中提取的一级标题（# 标题），无标题则用文件名
    content: str         # 文件正文全文
```

### KnowledgeIndexSelection

```python
@dataclass(frozen=True, slots=True)
class KnowledgeIndexSelection:
    documents: tuple[KnowledgeDocument, ...]  # 匹配的所有文档
    text: str                                   # 拼装后的完整文本
    original_chars: int                         # 拼装全文长度
    injected_chars: int                         # 实际注入长度（= original_chars，不做截断）
    original_items: int                         # 文档数量
    injected_items: int                         # 注入文档数量（= original_items）
    truncated: bool                             # 是否截断（当前始终 false）
```

## 主要入口

```python
def select_knowledge_index(
    root: Path,
    user: str,
    *,
    scopes: tuple[str, ...] = ("user", "shared", "global"),
) -> KnowledgeIndexSelection
```

### 扫描顺序（按 scope 优先级）

1. **user** — `users/<user>/knowledge/`
2. **shared** — `shared_knowledge/`
3. **global** — `global_knowledge/`

### 文件匹配

仅匹配命名索引文件（`_INDEX_NAMES`）：
- `index.md`
- `data_structure.md`
- `索引.md`
- `目录.md`

### 拼装格式

每个匹配文件被格式化为：
```text
[scope:relative_path]
文件全文...
```

不同 scope 的文件用空行分隔。

## 与旧版的区别

- ❌ 移除了关键词评分系统（`KnowledgeSelection` / `select_knowledge`）
- ❌ 移除了 `max_items` / `minimum_score` / `max_file_chars` 配置
- ❌ 移除了评分截断和排序逻辑
- ✅ 改为全量确定性注入：遍历所有层级的命名索引文件，按 scope 优先级拼装
- ✅ 新增 `KnowledgeDocument` / `KnowledgeIndexSelection` 数据模型
- ✅ 纯函数式，无状态

## 调用者

- `run/prompt.py` — `build_prompt_bundle` 中注入 `knowledge_index` 段
- `web/service.py` — 知识页面加载索引

## 依赖

- `run/prompt_sources.py` — `iter_files` / `read_required_text`

## 配置

- `knowledge.enabled` — 全局开关
- `knowledge.max_chars` — 字符上限（当前仅用于兼容，全量注入不受限）
- `knowledge.scopes` — 启用的层级

## 相关笔记

- [[run-总览]]
- [[run-prompt_sources]]（iter_files / read_required_text）
- [[run-prompt]]（build_prompt_bundle 调用方）
- [[web-service]]
- [[frontend-knowledge-page]]
