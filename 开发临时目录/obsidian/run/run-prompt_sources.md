---
type: component
project: kemo-agent
domain: run
module: run-prompt_sources
layer: L3
scope: project
status: active
summary: run/prompt_sources.py — 提示词来源辅助与 PromptSourceRegistry 动态注册
source: "run/run-prompt_sources.md"
updated: 2026-07-21
verified: false
tags: [kemo-agent, run, prompt_sources, PromptSourceRegistry, SenseMeta, ExpandMeta, 感知标准化, 拓展标准化]
---
# run/prompt_sources.py — 提示词来源辅助与动态注册

`E:\code\kemo-agent\run\prompt_sources.py`

## 两大部分

1. **通用辅助函数** — 文件读取、自然排序、截断、SKILL.md 解析
2. **PromptSourceRegistry** — 感知/拓展/技能三层动态注册与选择器

---

## 第一部分：通用辅助

### 文件读取

| 函数 | 行为 |
|------|------|
| `read_optional_text(path)` | 读取文本文件，不存在返回空串 |
| `read_required_text(path)` | 读取文本文件，不可读抛 `PromptSourceError` |

### iter_files()

```python
def iter_files(base, *, suffixes=None, names=None, recursive=True, skip_hidden=True) -> tuple[Path, ...]
```

自然排序的文件迭代器，支持扩展名和后缀名过滤。

### 其他工具

| 函数 | 说明 |
|------|------|
| `natural_path_key(value)` | 大小写不敏感的自然排序键 |
| `relative_path(path, root)` | 返回相对路径字符串 |
| `truncate_chars(text, max_chars)` | 字符截断，返回 (text, truncated) |
| `parse_skill_descriptor(path, *, scope, root)` | 解析 SKILL.md 的标题和描述 |

---

## 第二部分：PromptSourceRegistry

`E:\code\kemo-agent\run\prompt_sources.py` → `PromptSourceRegistry`

### 分层注册体系

```python
class PromptSourceRegistry:
    def __init__(self, root: Path, user: str)
```

注册表分为三个维度：

| 维度 | 注册方法 | 选择方法 | scope 层级 |
|------|---------|---------|-----------|
| 感知数据 | `add_perception(base)` | `select_perception()` | global → shared → user |
| 拓展数据 | `add_expand_root()` / `add_expand_module()` | `select_expand()` | global → shared → user |
| 技能服务 | `add_skills(scope, base)` | `select_skills()` | shared → user |

### 注册加载流程

```python
def load_prompt_source_registry(root, user) -> PromptSourceRegistry
```

按固定顺序加载目录级注册模块：
1. `global_expand/register.py`
2. `shared_expand/register.py`
3. `shared_skills/register.py`
4. `global_sense/register.py`
5. `agents/_runtime/user_resources.py`（attach_user_prompt_sources）

### 感知数据标准化（SenseMeta）

```python
@dataclass(frozen=True, slots=True)
class SenseMeta:
    name: str          # 感知模块名
    data_md: str       # 感知数据 Markdown 文件名
    recent_update: str  # 最近更新时间（%Y-%m-%d %H:%M:%S）
    health: str        # "正常" | "异常"
    start_update: str  # 数据更新脚本文件名（.py）
    data_md_path: Path # data_md 的完整路径
    valid: bool        # 元数据是否有效
    error: str         # 无效时的错误信息
```

感知模块目录下必须包含 `sense.json`，字段要求：
- `name` — 模块名（非空字符串）
- `data_md` — 感知 Markdown 文件名（在模块目录内）
- `recent_update` — 格式 `YYYY-MM-DD HH:MM:SS`
- `health` — `"正常"` 或 `"异常"`
- `start_update` — 数据更新脚本文件名（在模块目录内）

#### perception_inventory()

返回所有已注册感知模块的元数据列表（含 health_status、valid、selected、active 状态）。

#### select_perception()

```python
def select_perception(*, max_chars, mode="full", allow_modules=None) -> PerceptionSelection
```

按 `global_sense/` 下的模块目录扫描，读取每个模块的 `data_md` 文件内容，按 `[module_name]` 格式拼装。

### 拓展数据标准化（ExpandMeta）

```python
@dataclass(frozen=True, slots=True)
class ExpandMeta:
    name: str           # 拓展模块名
    explain: str        # 功能说明
    open_input: bool    # 是否开启数据采集
    input_data: str     # 输入数据 Markdown 文件名
    input_health: str   # 输入健康状态
    start_update: str   # 数据更新脚本文件名
    open_control: bool  # 是否开启操控
    start_expand: str   # 拓展执行脚本文件名
    start_control: str  # 操控手册文件名
    module_dir: Path    # 模块目录路径
    valid: bool         # 元数据是否有效
    error: str          # 无效时的错误信息
```

拓展模块目录下必须包含 `expand.json`，字段要求：
- `name` / `explain` — 非空字符串
- `open_input` / `open_control` — 布尔值
- `input_data` / `input_health` / `start_update` / `start_expand` / `start_control` — 模块目录内的文件名（有目录逃逸检查）
- `input_health` — `"正常"` 或 `"异常"`
- `recent_update` — **新增可选字段**，格式 `YYYY-MM-DD HH:MM:SS`，校验格式正确性，不强制必填

### `_read_expand_meta` 改为公共函数（2026-07-21）

函数 `_read_expand_meta` 重命名为公共函数 `read_expand_meta`，供 `expand_creater` 插件和 Web 服务复用。

#### select_expand()

```python
def select_expand(*, max_chars, mode="full", allow=None) -> ExpandSelection
```

扫描顺序：global → shared → user。

每个模块的注入分两层：
- **数据采集层**（`## 数据采集`）：读取 `input_data` 指定的 Markdown 文件
- **操控能力层**（`## 操控能力`）：从 `start_control` 指定的 Markdown 中提取 `## 注入层` 到 `## 操作层` 之间的内容

### 选择器返回的数据类型

```python
@dataclass(frozen=True, slots=True)
class PerceptionSelection:
    text: str                       # 拼装后的文本
    source_files: tuple[str, ...]   # 来源文件路径列表
    original_chars: int             # 拼装全文长度
    injected_chars: int             # 实际注入长度
    original_items: int             # 来源模块数
    injected_items: int             # 注入模块数
    truncated: bool                 # 是否被 max_chars 截断

@dataclass(frozen=True, slots=True)
class ExpandSelection:
    text: str
    source_files: tuple[str, ...]
    original_chars: int
    injected_chars: int
    original_items: int
    injected_items: int
    truncated: bool
```

### 诊断

```python
def selection_diagnostics(self) -> dict[str, Any]
```

返回 skills / expand / perception 三个维度的诊断信息，含 discovered、selected、filtered、invalid、unmatched、health_status 等字段，用于 Web UI 感知/拓展页面展示。

### 感知数据标准化后的区别

| 特性 | 旧行为 | 新行为 |
|------|--------|--------|
| 感知配置 | 纯文件扫描 | sense.json 元数据驱动（含健康检查） |
| 拓展配置 | add_expand() 注册 inject_file | expand.json 元数据驱动（含数据/操控分离） |
| 数据采集 | 直接读注入文件 | 按 open_input + input_health 条件开启 |
| 操控能力 | 无 | 新增 `## 注入层` / `## 操作层` 两层分离 |
| 错误处理 | 整体失败 | 单模块解析失败不影响其他模块 |

## 调用与依赖

| 关系 | 目标 | 说明 |
|------|------|------|
| reads | `global_sense/register.py` | 注册感知根目录 |
| reads | `global_expand/register.py` | 注册拓展模块 |
| reads | `shared_expand/register.py` | 注册共享拓展 |
| reads | `shared_skills/register.py` | 注册共享技能 |
| reads | `agents/_runtime/user_resources.py` | 注册用户级的 expand / skills / perception |
| used by | `run/prompt.py` | `build_prompt_bundle` 调用选择器 |
| used by | `web/service.py` | 感知/拓展页面展示 |

## 相关笔记

- [[run-prompt]]（build_prompt_bundle 调用选择器）
- [[run-knowledge]]（共享 iter_files / read_required_text）
- [[原理-系统提示词拼接]]
- [[原理-PromptBundle编排]]
- [[原理-感知与拓展]]
- [[global_sense-全局感知]]
- [[global_expand-全局拓展]]
