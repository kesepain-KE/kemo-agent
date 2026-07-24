---
type: domain_overview
project: kemo-agent
domain: project
module: global_sense-全局感知
layer: L1
scope: global
status: active
summary: global_sense/ — 全局感知注册目录，JSON 元数据标准化（SenseMeta）
source: "global_sense-全局感知.md"
updated: 2026-07-21
verified: false
tags: [kemo-agent, global_sense, 感知, 传感器, 注册, SenseMeta, sense.json]
created: 2026-07-15
---
# global_sense/ — 全局感知注册目录

`E:\code\kemo-agent\global_sense/`

## 定位

提供 prompt 注入感知数据的全局注册点。由 `run/prompt_sources.py` 的 `PromptSourceRegistry.select_perception()` 驱动。

## 标准化（SenseMeta）

从"扫描所有 .md 文件"升级为 **JSON 元数据驱动**。每个子目录代表一个感知模块，必须包含 `sense.json`。

### 目录结构

```text
global_sense/
├── register.py        # 注册回调
├── example/           # 示例模块
│   ├── sense.json     # 元数据（必选）
│   ├── sense.md       # 感知数据 Markdown（data_md 引用）
│   ├── data_update.py # 数据更新脚本（start_update 引用）
│   └── 乱七八糟的模块.py
├── <module>/          # 其他感知模块
│   ├── sense.json
│   ├── <data_md>.md
│   └── <start_update>.py
```

### sense.json 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 模块显示名（非空） |
| `data_md` | string | 模块目录内的 .md 文件名（感知数据正文） |
| `recent_update` | string | 格式 `%Y-%m-%d %H:%M:%S` |
| `health` | string | `"正常"` 或 `"异常"` |
| `start_update` | string | 模块目录内的 .py 文件名（数据更新脚本） |

### 校验规则

- 所有字段必须存在且为非空字符串
- `data_md` 和 `start_update` 不得跳出模块目录
- `data_md` 指向的文件必须存在
- `recent_update` 必须符合 `%Y-%m-%d %H:%M:%S`
- `health` 必须是 `"正常"` 或 `"异常"`
- 不含未知字段（strict schema）

## register.py

```python
def register(registry) -> None:
    registry.add_perception(Path(__file__).resolve().parent)
```

注册当前目录（`global_sense/`）作为感知根目录。选择器会自动扫描该目录下的所有子目录作为感知模块。

## 调用链

```text
run/prompt.py build_prompt_bundle()
  → PromptSourceRegistry.select_perception(max_chars, allow_modules)
    → global_sense/register.py::register(registry)
    → 遍历 global_sense/ 下每个子目录的 sense.json
    → 读取 data_md 引用文件 → 按 [module_name] 格式注入 prompt
```

## 感知注入规则

- 每个子目录的 `sense.json` 必须有 `health: "正常"` 才会注入
- 感知数据拼接格式：`[模块名]\n数据内容...`
- 受 `char_limits.perception` 字符上限控制
- 错误隔离：单个模块 sense.json 损坏不影响其他模块

## 相关笔记

- [[run-prompt_sources]]（PromptSourceRegistry.select_perception 实现）
- [[原理-感知与拓展]]
- [[run-prompt]]
