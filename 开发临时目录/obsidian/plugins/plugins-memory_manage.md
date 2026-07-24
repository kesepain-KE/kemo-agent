---
type: component
project: kemo-agent
domain: plugins
module: plugins-memory_manage
layer: L2
scope: project
status: active
summary: plugins/memory_manage/ — 记忆管理插件（新增 list/get action、search_by_content 返回 snippet、limit 上限保护）
source: "plugins/memory_manage/"
updated: 2026-07-23
verified: true
tags: [kemo-agent, plugins, memory_manage, 记忆管理, list, get, snippet, limit, memory_ref, locate_in_tier, 孤儿修复, v1.2, important_protection, v3_metadata]
---
# plugins/memory_manage/ — 记忆管理插件

`E:\code\kemo-agent\plugins\memory_manage/`

## 定位

按当前用户隔离查询、删除、编辑和新增永久记忆、三层临时记忆（seven_days / one_month / half_year）及临时重要记忆热画像。

## 结构

| 文件 | 说明 |
|------|------|
| `SKILL.md` | 工具注册描述 |
| `memory_ops.py` | 记忆操作核心实现：搜索/删除/编辑/新增 |
| `tool.py` | 工具入口，调用 memory_ops 操作 |

## 工具定义

- name: `memory_manage`
- entrypoint: `tool.py:run`
- version: `1.2.0`（2026-07-22 升级）

### action 说明（新增 list/get）

| action | 功能 |
|--------|------|
| `search_by_title` | 按标题（记忆键/文件名）搜索记忆 |
| `search_by_content` | 按内容关键词搜索记忆（新增始终返回 snippet 摘要） |
| `list` | 列出指定层级的所有记忆（新增） |
| `get` | 获取指定记忆文件完整内容（新增） |
| `delete` | 安全删除指定记忆文件 |
| `edit` | 编辑记忆文件内容 |
| `add` | 新增一条记忆碎片 |

### tier 层级

| tier | 对应存储层 |
|------|-----------|
| `seven_days` | 临时记忆（7天） |
| `one_month` | 临时记忆（1个月） |
| `half_year` | 临时记忆（6个月） |
| `permanent` | 永久记忆（长期保留） |
| `important` | 临时重要记忆热画像 |

## 优化详情（2026-07-22 升级 v1.2.0）

### 精确层级寻址

- **`locate_in_tier(tier, filename)`** — 替代 `locate` 用于所有 CRUD 操作。即使多个层级存在同名文件，也只操作指定层级，不误伤其他层。
- `get`、`edit`、`delete` 均改用 `locate_in_tier`，校验 `location.tier == tier` 后才执行。

### memory_ref 稳定引用

- 所有 CRUD 返回值和列表/搜索结果统一包含 `memory_ref` 字段，格式 `tier:filename`。
- 前端传递目标时优先使用 `memory_ref`，避免跨层歧义。

### 孤儿索引修复

- `delete` 操作返回 `index_removed`、`file_removed`、`repaired_orphan` 三个字段。
- 当索引存在但正文文件已缺失时，删除索引条目并返回 `repaired_orphan: true`。

### 跨层同名文件管理

- 跨层同名文件不再是存储异常（对管理操作而言），各层副本可独立读取、编辑、删除。
- `edit` 的新文件名冲突检查也改用 `source_name` 而非 `normalized`，避免大小写不一致问题。

## 优化详情（2026-07-21）

- **新增 list action** — 按层级列出全部记忆，无需关键词
- **新增 get action** — 直接获取记忆文件完整内容
- **search_by_content 始终返回 snippet** — 搜索结果携带匹配摘要片段
- **context_chars 可调** — snippet 上下文字符数可配置
- **limit 上限保护** — 搜索返回数量有上限保护，防止超量返回

## 重要记忆保护（2026-07-23 新增）

- **`important` 层级文件不可删除**：`delete_fragment` 对 `important` 层级直接抛出 `MemoryError("临时重要记忆文件不可删除")`。
- **空内容写入占位文本**：`write_important_memory` 接收到空内容时自动写入占位文本 `IMPORTANT_MEMORY_PLACEHOLDER`，确保文件永远存在。
- **主智能体只允许读取 `important` 层级**：`add`、`edit`、`delete` 对 `important` 层级不可用。只允许 `get`、`search_by_title`、`search_by_content`。
- **Web API 移除 DELETE 端点**：`web/app.py` 的 `DELETE /api/users/{user}/memory/important` 已移除。
- **Web API 拒绝空内容更新**：`web/service.py` 的 `update_important_memory` 拒绝空字符串。

## v3 元数据字段（2026-07-23 新增）

`memory_ops.py` 的所有返回值新增 v3 元数据字段：
- `created_at`：记忆创建时间
- `content_updated_at`：内容最后修改时间
- `last_used_at`：最近被引用时间
- `timezone`：统一标注 `"UTC"`

`edit_fragment` 使用 `_touch_temporary(content_changed=True)` 正确更新内容时间。

## 依赖

- `run/memory.py` — MemoryStore 提供读写能力
- 临时层操作会同步维护对应的 `data.json`

## 相关笔记

- [[plugins-manifest]]（插件清单注册）
- [[run-memory]]（记忆存储系统）
- [[improve-总览]]（记忆系统架构）