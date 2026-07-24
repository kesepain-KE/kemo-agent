---
type: domain_overview
project: kemo-agent
domain: project
module: global_expand-全局拓展
layer: L1
scope: global
status: active
summary: global_expand/ — 全局拓展注册目录，JSON 元数据标准化（ExpandMeta）
source: "global_expand-全局拓展.md"
updated: 2026-07-21
verified: false
tags: [kemo-agent, global_expand, 拓展, 注册, ExpandMeta, expand.json, 数据采集, 操控]
created: 2026-07-15
---
# global_expand/ — 全局拓展注册目录

`E:\code\kemo-agent\global_expand/`

## 定位

提供 prompt 注入拓展文件的全局注册点。由 `run/prompt_sources.py` 的 `PromptSourceRegistry.select_expand()` 驱动。

## 标准化（ExpandMeta）

从旧的 `add_expand(scope, module, inject_file)` 注册方式升级为 **expand.json 元数据驱动**。每个子目录代表一个拓展模块，必须包含 `expand.json`。

### 目录结构

```text
global_expand/
├── register.py            # 注册回调
├── example/               # 示例模块
│   ├── expand.json        # 元数据（必选）
│   ├── input_data.md      # 数据采集 Markdown（input_data 引用）
│   ├── expand_control.md  # 操控手册（start_control 引用，含注入层/操作层分离）
│   ├── start_expand.py    # 拓展执行脚本
│   ├── start_update.py    # 数据更新脚本（start_update 引用）
│   └── 乱七八糟的模块.py
├── <module>/              # 其他拓展模块
│   ├── expand.json
│   ├── input_data.md
│   ├── expand_control.md
│   ├── start_expand.py
│   └── start_update.py
```

### expand.json 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 模块显示名 |
| `explain` | string | 功能说明 |
| `open_input` | bool | 是否开启数据采集 |
| `input_data` | string | 模块目录内的 .md 文件名（数据采集源） |
| `input_health` | string | `"正常"` 或 `"异常"` |
| `start_update` | string | 模块目录内的 .py 文件名（数据更新脚本） |
| `open_control` | bool | 是否开启操控 |
| `start_expand` | string | 模块目录内的 .py 文件名（拓展执行脚本） |
| `start_control` | string | 模块目录内的 .md 文件名（操控手册） |

### 校验规则

- 所有字段必须存在（strict schema，不允许多余字段）
- `name` / `explain` 非空字符串
- `open_input` / `open_control` 必须是布尔值
- 所有文件名字段必须指向模块目录内的文件（目录逃逸检查）
- `input_health` 必须是 `"正常"` 或 `"异常"`
- 错误隔离：单个模块 expand.json 损坏不影响其他模块

### 注入层/操作层分离

操控手册（start_control）的 Markdown 文件中：
- `## 注入层` 和 `## 操作层` 之间的内容作为**注入的数据**进入 prompt
- `## 操作层` 之后的内容为操作指南，不进入 prompt
- 无 `## 注入层` 标记时不返回任何操控数据

## register.py

```python
def register(registry) -> None:
    # 注册全局拓展根目录
    registry.add_expand_root("global", Path(__file__).resolve().parent)
    # 可选择性注册模块（不调 add_expand_root 则会自动发现子目录）
```

当前注册的是根目录（自动发现所有子目录作为模块）。也可用 `add_expand_module(scope, module, module_dir)` 显式注册单个模块。

## 调用链

```text
run/prompt.py build_prompt_bundle()
  → PromptSourceRegistry.select_expand(max_chars, allow)
    → global_expand/register.py::register(registry)
    → 遍历 global_expand/ 下每个子目录的 expand.json
    → 读取 input_data + 提取注入层内容
    → 按 [scope:module] 格式注入 prompt
```

## 拓展注入规则

- `open_input` + `input_health == "正常"` 时才注入数据采集层
- `open_control` 时才注入操控能力层
- 注入格式：
  ```text
  [scope:module]
  ## 数据采集
  input_data 文件内容...
  ## 操控能力
  操控手册的注入层内容...
  ```
- 受 `char_limits.expand` 字符上限控制

## 兼容性

`registry.add_expand(scope, module, inject_file)` 旧接口仍可用，但内部会重定向到 expand.json 元数据检测（仅校验目录注册，不读取 inject_file）。

## 相关笔记

- [[run-prompt_sources]]（PromptSourceRegistry.select_expand 实现）
- [[原理-感知与拓展]]
- [[run-prompt]]
