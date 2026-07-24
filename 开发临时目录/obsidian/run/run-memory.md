---
type: component
project: kemo-agent
domain: run
module: run-memory
layer: L2
scope: project
status: active
summary: run/memory.py — 文件型四档记忆引擎（schema v3）
source: "run/run-memory.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, run, 记忆, prompt, 四档, v3, extraction_mode, 完整性, locate_in_tier]
---
# run/memory.py — 文件型四档记忆引擎（schema v3）

## 定位

围绕四档文件型记忆存储、检索与 prompt 注入的专题模块。

## 架构变更

### v1 → v2

| 方面 | v1（已废弃） | v2 |
|------|-------------|-----------|
| 存储模型 | 全体 JSON 数组 `data.json` | 索引 `data.json` + 独立 `.md` 正文文件 |
| 条目身份 | UUID `id` | 文件名（最长 50 字符，全局唯一） |
| 数据字段 | content, type, keywords, entities, source, confidence, importance, status, tier, tier_weight, tier_entered_at, review_at, last_weight_date, created_at, updated_at, explicit, version | 正文存 `.md` 文件，索引只存 weight, updated_at, last_weight_date, expires_at |
| 永久层 | 同 JSON 数组 | 纯 `.md` 文件，无 `data.json` |
| 搜索依据 | content + keywords + entities 全文 | 仅文件名 |
| 加权粒度 | UUID 条目 | .md 文件 |

### v2 → v3（2026-07-23）

| 方面 | v2 | v3（当前） |
|------|-----------|-----------|
| schema_version | 2 | 3 |
| 索引字段 | weight, updated_at, last_weight_date, expires_at | weight, created_at, content_updated_at, updated_at(别名), last_used_at, last_weight_date, tier_entered_at, expires_at |
| updated_at 语义 | 记录最近活动时间（修改或引用均覆盖） | 仅是 content_updated_at 的兼容别名，引用不再覆盖 |
| 引用时间 | 无独立字段 | `last_used_at` 独立记录 |
| 创建时间 | 无 | `created_at` 独立记录 |
| 层级进入时间 | 无 | `tier_entered_at` 独立记录 |
| 时区 | 本地系统时区 | `local_day()` 统一使用 `Asia/Shanghai`；绝对时间统一存 UTC ISO 8601 |
| 注入排序 | weight 降序 + updated_at 降序 + 文件名 | weight 降序 + 文件名（去除 updated_at 次要排序，稳定排序） |
| 提取模式 | `auto_extract_on_commit` 布尔值 | `extraction_mode` 四模式枚举（见下） |
| 向后兼容 | — | 读取 v2 索引时自动补全新字段并就地升级写入 v3 |

## 类

### MemoryLocation

```python
@dataclass(frozen=True)
class MemoryLocation:
    tier: str
    filename: str
    path: Path
    indexed: bool  # True=临时层有索引, False=永久层无索引
```

替代 v1 的 `MemorySelection`。表示一个记忆文件在存储中的位置。

### TierPromptSelection

```python
@dataclass(frozen=True)
class TierPromptSelection:
    tier: str
    items: tuple[dict, ...]
    text: str
    selected_ids: tuple[str, ...]   # v2 中是文件名列表
    original_chars: int             # 原为字符串长度，v2 为文件大小之和
    injected_chars: int
    original_items: int
    injected_items: int
    truncated: bool
    source_files: tuple[Path, ...]  # v2 改为元组，支持多文件
    integrity_warnings: tuple[str, ...] = ()  # 2026-07-22: 缺失正文的警告
```

### MemoryIntegrityError (2026-07-22 新增)

```python
class MemoryIntegrityError(MemoryError):
    def __init__(self, issue: str, message: str) -> None:
        super().__init__(message)
        self.issue = issue
```

当索引指向的 `.md` 正文文件不存在时抛出。区别于普通 `MemoryError`，用于上层捕获后跳过而非中断。

### memory_extraction_mode(config) — 2026-07-23 新增

```python
def memory_extraction_mode(config: dict[str, Any]) -> str:
```

解析 `memory.extraction_mode` 配置，返回四种模式之一：

| 模式 | 说明 |
|------|------|
| `compression_only`（默认） | 普通提交只登记 `deferred` 游标，保存会话或上下文压缩时才顺序提取 |
| `background` | Maintenance 领取普通 `pending` 轮次 |
| `on_commit` | 历史提交后同步提取 |
| `disabled` | 不进行自动提取，Maintenance 也不会领取 |

兼容旧 `auto_extract_on_commit` 布尔值：`true` → `on_commit`，`false/缺失` → `compression_only`。

### _normalise_temporary_meta(tier, filename, raw_meta) — 2026-07-23 新增

```python
def _normalise_temporary_meta(self, tier, filename, raw_meta) -> dict[str, Any]:
```

v3 索引字段规范化核心。从旧 v2 或新 v3 元数据中提取并补全所有字段：
- `created_at`：缺失时取 `min(tier_entered_at, file_modified_at, legacy_updated_at)`
- `content_updated_at`：缺失时取文件系统修改时间
- `last_used_at`：缺失时回退到 `legacy_updated_at`（仅当有 `last_weight_date` 时）
- `tier_entered_at`：缺失时从 `expires_at - tier_days` 反推
- `updated_at`：保持为 `content_updated_at` 的兼容别名

## 关键函数

### normalize_memory_filename(value: Any) -> str

规范化记忆文件名：
- 移除无效路径字符 `<>:"/\\|?*` 和控制字符
- 截断到 50 字符
- 处理 Windows 保留名（con, prn 等）
- 自动追加 `.md` 扩展名

### _atomic_text(path, value)

原子写入 `.md` 文件：临时文件 + `os.replace()` 模式，与 `_atomic_json` 同一安全级别。

### MemoryStore 核心方法

| 方法 | 说明 |
|------|------|
| `load_index(tier)` | 读取临时层的 `data.json` 索引（v3 格式，兼容 v2 自动升级） |
| `write_index(tier, files)` | 原子写入索引 |
| `locate(filename)` | 跨全部层级查找文件，返回 `MemoryLocation \| None` |
| `locate_in_tier(tier, filename)` | **2026-07-22 新增**：仅在指定层级内查找，不扫描其他层。用于跨层同名文件的精确 CRUD 和孤儿索引修复 |
| `_entry(location, meta)` | 从磁盘读取 `.md` 正文 + 索引元数据；正文不存在时抛 `MemoryIntegrityError` |
| `_entry_or_warning(location, meta)` | **2026-07-22 新增**：捕获 `MemoryIntegrityError` 的安全包装，返回 `(entry, warning)`；缺失正文的索引跳过而非中断 |
| `load_tier(tier)` | 加载整层记忆；缺失正文的索引被跳过并记入 WARNING 日志（2026-07-22 增强） |
| `load_all()` | 加载全部四层，按 TIERS 顺序 |
| `list_file_references()` | 仅返回文件名和元数据，不读正文 |
| `search(query)` | 按文件名检索，返回匹配条目的完整内容 |
| `select_tier_for_prompt(tier, max_files, mode)` | 选择整层注入 prompt；临时层按 weight 降序 + 文件名稳定排序（v3 去除 updated_at 次要排序）。可限制 max_files。缺失正文的索引被跳过并产出 integrity_warnings |
| `mark_used(filenames)` | 文件实际被引用后加权（同自然日最多 +1） |
| `upsert_candidates(candidates)` | 新增/更新/遗忘记忆（文件名匹配） |
| `forget(query)` | 按文件名删除记忆（删除 .md + 索引项） |
| `review_due()` | 扫描到期记忆：权重达标则晋升、否则删除 |
| `integrity_issues()` | 校验存储完整性，返回所有异常列表 |

### _touch_temporary(location, current, *, content_changed=False) — 2026-07-23 更新

临时记忆加权核心：读取当前索引 → 检查当日是否已加权 → 未加权则 weight+1 → 写入索引。

v3 变更：
- `content_changed=True` 时更新 `content_updated_at` 和 `updated_at`（别名）
- 始终更新 `last_used_at`
- 引用（`content_changed=False`）不再覆盖内容更新时间

### _promote_location(location, target_tier, current)

晋升原子操作：移动 `.md` 文件到目标层目录 → 更新源层索引（移除条目）→ 写入目标层索引（新增条目）→ 失败时回滚。

### _delete_location(location)

删除原子操作：从索引移除 → 删除 `.md` 文件。

## 存储结构

```
users/<user>/improve/
├── storage.json              # v3 标记（schema_version: 3）
├── seven_days/
│   ├── data.json             # v3 索引：{schema_version, files: {filename -> {weight, created_at, content_updated_at, updated_at, last_used_at, last_weight_date, tier_entered_at, expires_at}}}
│   ├── 文件名1.md            # 正文
│   └── 文件名2.md
├── one_month/
│   ├── data.json
│   └── ...
├── half_year/
│   ├── data.json
│   └── ...
└── permanent/
    ├── 文件名1.md            # 无 data.json，纯文件
    └── 文件名2.md
```

## 配置

```json
{
  "memory": {
    "storage_schema_version": 3,
    "extraction_mode": "compression_only",
    "temporary_injection_limits": {
      "half_year": 3,
      "one_month": 4,
      "seven_days": 3
    },
    "important_memory_max_chars": 1500,
    "extraction_enabled": true,
    "injection_enabled": true,
    "existing_candidates_for_extraction": 12,
    "tiers": { ... }
  }
}
```

## 输入

- query（搜索用文件名）
- tier（档位名）
- memory config
- candidates（upsert 候选列表）

## 输出

- TierPromptSelection
- 记忆权重更新结果
- 搜索匹配列表

## 主要入口

- select_tier_for_prompt
- mark_used
- search
- load_tier
- upsert_candidates
- review_due

## 调用者

- run/engine.py（通过 prompt.py）
- run/prompt.py

## 被调用对象

- run/memory_pipeline.py

## 依赖

- 文件系统磁盘存储
- 配置中 memory 相关字段

## 使用前提

- 存储目录存在且已初始化（storage.json 标记 v3，兼容 v2 自动升级）

## 不参与情况

- memory.injection_enabled = false 时
- 记忆文件缺失或损坏时

## 修改影响

影响 prompt 中的 permanent/temporary/important memory 注入顺序与文件选择。

## 代码证据

| 关系 | 目标 | 源码路径 | 源码符号 | 条件 | 不发生条件 | 置信度 | 核验日期 |
|---|---|---|---|---|---|---|---|
| calls | [[run-memory_pipeline]] | run/memory.py | MemoryStore.mark_used / select_tier_for_prompt | 记忆加权与注入时 | 记忆关闭 | high | 2026-07-19 |
| documented_by | [[原理-记忆升级权重]] | run/memory.py | MemoryStore | 记忆档位与权重说明 | 仅代码阅读不看原理笔记 | high | 2026-07-19 |
| called_by | run/engine.py | run/memory.py | MemoryStore.mark_used | 成功对话后加权 | 失败/取消不触发 | high | 2026-07-19 |
| depends_on | run/memory_migrate.py | run/memory.py | MEMORY_SCHEMA_VERSION | 迁移检测 | v2 已初始化 | high | 2026-07-19 |

## 相关笔记

- [[run-总览]]
- [[run-memory_pipeline]]
- [[原理-记忆升级权重]]
- [[run-memory_migrate]]
