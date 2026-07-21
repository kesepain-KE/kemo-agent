# sense_creater 工具创建 — 编程方案

## 问题

`plugins/sense_creater` 目录当前为空。感知模块（`global_sense/`）目前只能手动创建——建目录、手写 3 个文件（`sense.json`、`sense.md`、`data_update.py`）。需要让主智能体通过工具调用热创建感知模块，类似 `expand_creater` 创建拓展模块。

## 现有感知模块结构

每个感知模块是一个目录，包含 3 个文件：

```
global_sense/system_monitor/
├── sense.json       # 清单：name、data_md、recent_update、health、start_update
├── sense.md         # 感知数据（全文直接注入 system prompt）
└── data_update.py   # 数据采集/刷新脚本
```

**加载机制**：`register.py` 注册 `global_sense` 根目录 → `prompt_sources.py` 扫描子目录 → 读 `sense.json` → 把 `sense.md` 全文注入 system prompt。

与拓展模块的关键区别：感知是**单向**的——只采集数据注入 prompt，不提供操控接口。没有 `start_expand.py` 和 `expand_control.md`。

---

## 能力目标

### 3 个 action

| action | 用途 |
|--------|------|
| `list` | 列出所有感知模块（名称 + 健康状态 + 最后更新时间） |
| `create` | 创建感知模块（四步流程） |
| `validate` | 校验感知模块完整性（sense.json 字段、sense.md 存在性） |

### `create` 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|:--:|------|
| `name` | string | ✅ | 模块目录名（英文，如 `system_monitor`、`weather_sensor`） |
| `explain` | string | ✅ | 一句话功能说明（用于 shell 输出提示，不写入文件） |
| `sense_content` | string | ✅ | 初始 `sense.md` 内容（Markdown 格式的感知数据模板） |
| `data_update` | string | | 自定义 `data_update.py` 内容，不传则使用模板骨架 |

只有 `global` 一个 scope，不需要 scope 参数。

### 返回结构

**`list` 返回**：

```json
{
  "action": "list",
  "modules": [
    {
      "name": "example",
      "health": "正常",
      "recent_update": "2026-07-19 14:30:00",
      "data_md": "sense.md"
    }
  ]
}
```

**`create` 返回**：

```json
{
  "action": "create",
  "name": "system_monitor",
  "path": "global_sense/system_monitor",
  "files": ["sense.json", "sense.md", "data_update.py"],
  "next_steps": [
    "在 data_update.py 的 collect_data() 中填充实际数据采集逻辑",
    "运行 data_update.py 初始化 sense.md"
  ]
}
```

**`validate` 返回**：

```json
{
  "action": "validate",
  "name": "system_monitor",
  "valid": true
}
```

或失败时：

```json
{
  "action": "validate",
  "name": "bad_module",
  "valid": false,
  "errors": ["sense.json 缺失", "sense.md 不存在"]
}
```

### `sense.json` 结构

自动生成，5 个固定字段，永不错：

```json
{
  "name": "system_monitor",
  "data_md": "sense.md",
  "recent_update": "2026-07-21 15:30:00",
  "health": "正常",
  "start_update": "data_update.py"
}
```

---

## 四步创建流程

和 `subagent_dispatch` / `expand_creater` / `skill_creater` 一致，写入 SKILL.md 指令型约束。

### 第一步：批判性判断是否需要感知模块

主智能体必须判断：

| 需要感知模块 | 不需要感知模块 |
|-------------|--------------|
| 需要采集系统/环境数据并注入 prompt（CPU、内存、温度、天气、网络状态） | 需要操控外部设备/服务（用拓展模块 `expand_creater`） |
| 数据单向流动：采集 → 注入 prompt，不需要智能体操控 | 需要可复用的指令或工具（用技能 `skill_creater`） |
| 需要定期刷新数据（配合 cron 定时跑 `data_update.py`） | 需要独立 LLM 推理和工具调用循环（用子代理） |

如果不需要，直接建议替代方案。

### 第二步：确认基本信息

询问用户：

1. 感知模块叫什么名字？（英文目录名，如 `system_monitor`、`weather_station`）
2. 采集什么数据？（用于 `explain` 和 sense.md 内容引导）

### 第三步：确认数据内容

引导用户描述感知数据的格式和内容，整理为 Markdown 模板：

```markdown
# 系统资源感知
> 最后更新: (自动填充)
> 状态: 正常

## CPU
- 使用率: 23%
- 温度: 52°C

## 内存
- 总量: 32.0 GB
- 已用: 18.7 GB
```

主智能体帮助用户设计数据结构、确定采集哪些指标。

### 第四步：检查冲突与创建

1. 调 `list` 确认 `global_sense/` 下无同名模块
2. 确认名称合法（字母开头，不超过 64 字符）
3. 向用户确认：名称、说明、数据模板
4. 调 `create`，传入完整参数
5. 创建完成后报告：模块路径、生成的文件列表、下一步建议

---

## 详细规划

### 步骤 1：创建 `tool.py`

**新建文件**：`plugins/sense_creater/tool.py`

```python
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


# ── 常量 ──

_SENSE_JSON_FIELDS = {
    "name",
    "data_md",
    "recent_update",
    "health",
    "start_update",
}

_DATA_UPDATE_TEMPLATE = '''#!/usr/bin/env python3
"""数据更新入口 — 运行此文件即刷新 sense.md 的内容。

调用方式:
    python data_update.py

该脚本负责从各数据源采集实时信息，将结果写入 sense.md。
运行后系统下次组装 prompt 时会自动读取 sense.md 的更新内容。

TODO: 在 collect_data() 中实现实际数据采集逻辑。
"""

import json
from datetime import datetime
from pathlib import Path


def collect_data() -> dict:
    """采集数据（对接硬件/API/系统接口等）

    TODO: 实现实际数据采集逻辑，返回结构化数据。
    """
    return {{}}


def render_markdown(data: dict, status: str = "正常") -> str:
    """将采集数据渲染为 Markdown。

    TODO: 根据实际数据结构修改渲染逻辑。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"# 感知数据", f"", f"> 最后更新: {{now}}", f"> 状态: {{status}}", f""]

    if not data:
        lines.append("暂无数据")
        return "\\n".join(lines).strip()

    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"## {{key}}")
            for k, v in value.items():
                lines.append(f"- {{k}}: {{v}}")
        else:
            lines.append(f"- **{{key}}**: {{value}}")
        lines.append("")

    return "\\n".join(lines).strip()


def update() -> None:
    base = Path(__file__).resolve().parent
    data = collect_data()
    md_content = render_markdown(data)
    (base / "sense.md").write_text(md_content, encoding="utf-8")
    # 同步更新 sense.json 的时间戳
    json_path = base / "sense.json"
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    meta["recent_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta["health"] = "正常"
    json_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\\n",
        encoding="utf-8",
    )
    print(f"[感知模块] sense.md 已更新")


if __name__ == "__main__":
    update()
'''


# ── 工具函数 ──

def _validate_name(name: str) -> str:
    name = name.strip()
    if not re.fullmatch(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$", name):
        raise ValueError(
            f"感知模块名无效：{name!r}。必须由字母开头，"
            f"仅含字母、数字、下划线、连字符，最长 64 字符"
        )
    return name


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, "utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# ── list action ──

def _run_list(root: Path) -> dict[str, Any]:
    sense_dir = root / "global_sense"
    if not sense_dir.is_dir():
        return {"action": "list", "modules": []}

    modules = []
    for path in sorted(sense_dir.iterdir()):
        if not path.is_dir() or path.name.startswith(".") or path.name == "__pycache__":
            continue
        json_path = path / "sense.json"
        if not json_path.is_file():
            modules.append({
                "name": path.name,
                "health": "异常",
                "recent_update": "",
                "error": "sense.json 缺失",
            })
            continue
        try:
            meta = json.loads(json_path.read_text("utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            modules.append({
                "name": path.name,
                "health": "异常",
                "recent_update": "",
                "error": "sense.json 无效",
            })
            continue
        modules.append({
            "name": path.name,
            "health": meta.get("health", "未知"),
            "recent_update": meta.get("recent_update", ""),
            "data_md": meta.get("data_md", ""),
        })
    return {"action": "list", "modules": modules}


# ── validate action ──

def _run_validate(root: Path, name: str) -> dict[str, Any]:
    module_dir = (root / "global_sense" / name).resolve()
    if not module_dir.is_dir():
        return {"action": "validate", "name": name, "valid": False,
                "errors": ["模块目录不存在"]}

    errors = []
    json_path = module_dir / "sense.json"
    if not json_path.is_file():
        errors.append("sense.json 缺失")
    else:
        try:
            meta = json.loads(json_path.read_text("utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"sense.json 无效: {e}")
            meta = {}
        if isinstance(meta, dict):
            missing = _SENSE_JSON_FIELDS - set(meta)
            if missing:
                errors.append(f"sense.json 缺少字段: {', '.join(sorted(missing))}")
            health = meta.get("health")
            if isinstance(health, str) and health not in ("正常", "异常"):
                errors.append(f"health 必须是'正常'或'异常'，而不是 {health!r}")
            data_md = meta.get("data_md")
            if isinstance(data_md, str) and data_md.strip():
                if not (module_dir / data_md.strip()).is_file():
                    errors.append(f"data_md 指向的文件不存在: {data_md}")
            start_update = meta.get("start_update")
            if isinstance(start_update, str) and start_update.strip():
                if not (module_dir / start_update.strip()).is_file():
                    errors.append(f"start_update 指向的文件不存在: {start_update}")

    return {
        "action": "validate",
        "name": name,
        "valid": len(errors) == 0,
        "errors": errors if errors else None,
    }


# ── create action ──

def _run_create(
    root: Path,
    name: str,
    sense_content: str,
    data_update: str = "",
) -> dict[str, Any]:
    name = _validate_name(name)
    module_dir = (root / "global_sense" / name).resolve()
    if module_dir.exists():
        raise FileExistsError(f"感知模块已存在：{name}")

    sense_body = sense_content.strip()
    if not sense_body:
        raise ValueError("sense_content 不能为空")

    # 拼接 sense.json
    sense_json = {
        "name": name,
        "data_md": "sense.md",
        "recent_update": _now_str(),
        "health": "正常",
        "start_update": "data_update.py",
    }

    # 原子写入（临时目录 → os.replace）
    tmp = module_dir.with_name(f".{name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.mkdir()
        (tmp / "sense.json").write_text(
            json.dumps(sense_json, ensure_ascii=False, indent=2) + "\n",
            "utf-8",
        )
        (tmp / "sense.md").write_text(sense_body + "\n", "utf-8")
        (tmp / "data_update.py").write_text(
            data_update.strip() or _DATA_UPDATE_TEMPLATE,
            "utf-8",
        )
        os.replace(tmp, module_dir)
    finally:
        if tmp.exists():
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    return {
        "action": "create",
        "name": name,
        "path": str(module_dir.relative_to(root)),
        "files": ["sense.json", "sense.md", "data_update.py"],
        "next_steps": [
            "在 data_update.py 的 collect_data() 中填充实际数据采集逻辑",
            "运行 data_update.py 初始化 sense.md 数据",
        ],
    }


# ── 分发 ──

def run(
    action: str,
    name: str = "",
    sense_content: str = "",
    data_update: str = "",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    root = Path(context["root"]).resolve()

    if action == "list":
        return _run_list(root)
    if action == "validate":
        if not name:
            raise ValueError("validate 需要 name")
        return _run_validate(root, name)
    if action == "create":
        return _run_create(
            root,
            name=name,
            sense_content=sense_content,
            data_update=data_update,
        )

    raise ValueError(f"未知 action: {action}，可选: list / create / validate")
```

### 步骤 2：创建 `SKILL.md`

**新建文件**：`plugins/sense_creater/SKILL.md`

```markdown
# sense_creater

热创建全局感知模块。感知模块采集系统/环境数据，通过 `sense.md` 注入 system prompt，是单向数据流（采集 → 注入），不提供操控接口。

## 创建感知模块四步流程（必须遵守）

### 第一步：批判性判断是否需要感知模块

收到创建感知模块的请求后，必须先判断：

- ✅ 需要感知模块：采集系统资源（CPU/内存/磁盘）、环境数据（温度/湿度/天气）、设备状态（在线/离线）、网络状态。数据单向流动，注入 prompt 供智能体感知。
- ❌ 不需要感知模块：需要操控外部设备/服务（用 `expand_creater`）、需要可复用的指令或工具（用 `skill_creater`）、需要独立 LLM 推理（用 `subagent_dispatch`）。

如果不需要，直接建议替代方案。

### 第二步：确认基本信息

询问用户：

1. 模块叫什么名字（英文目录名，如 `system_monitor`、`weather_station`）
2. 采集什么数据（CPU？内存？温度？网络？天气？）

### 第三步：确认数据内容

引导用户描述感知数据的格式，帮助整理为结构化的 Markdown 模板（即 `sense.md` 初始内容）：

```markdown
# 系统资源感知
> 最后更新: (自动填充)
> 状态: 正常

## CPU
- 使用率: (自动填充)
- 温度: (自动填充)

## 内存
- 总量: (自动填充)
- 已用: (自动填充)
```

主智能体负责帮用户设计数据结构和占位标记。

### 第四步：检查冲突与创建

1. 调 `list` 确认 `global_sense/` 下无同名模块
2. 确认名称合法（字母开头，不超过 64 字符）
3. 向用户确认：名称、数据内容模板
4. 调 `create`，传入 `name` 和 `sense_content`
5. 报告结果：路径、生成的文件、下一步（填充 `data_update.py` 实际采集逻辑）

## 与 `expand_creater` 的区别

| | sense_creater | expand_creater |
|---|-------------|---------------|
| 目标目录 | `global_sense/`（仅全局） | `global_expand/` / `shared_expand/` / `users/<name>/expand/` |
| 生成文件 | 3（sense.json、sense.md、data_update.py） | 5（+start_expand.py、expand_control.md） |
| 数据流向 | 单向：采集 → 注入 prompt | 双向：注入 + 智能体操控外部设备 |
| scope 参数 | 无需 | user / shared |
| 注入内容 | sense.md 全文 | expand_control.md 注入层 |

## 参数说明

| 参数 | 适用 action | 默认值 | 说明 |
|------|-----------|--------|------|
| `action` | 全部 | 必填 | list / create / validate |
| `name` | create / validate | — | 感知模块目录名（字母开头，≤64 字符） |
| `sense_content` | create | — | 初始 sense.md 内容（Markdown 格式的感知数据模板） |
| `data_update` | create | 模板 | 自定义 data_update.py 内容，不传则使用骨架模板 |

## 返回字段

| 字段 | 适用 action | 说明 |
|------|-----------|------|
| `modules` | list | 感知模块数组（name + health + recent_update） |
| `name` | create / validate | 模块名 |
| `path` | create | 模块相对路径 |
| `files` | create | 生成的文件列表 |
| `next_steps` | create | 创建后的操作建议 |
| `valid` | validate | 是否通过校验 |
| `errors` | validate | 校验失败时的错误列表 |

## Tool

```json
{
  "name": "sense_creater",
  "description": "热创建全局感知模块。支持列出、创建、校验感知模块。创建前必须走四步判断流程：是否需要感知 → 确认名称和采集目标 → 确认数据模板 → 检查冲突。自动生成 sense.json、sense.md、data_update.py。感知是单向数据流（采集→注入），不提供操控接口。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["list", "create", "validate"],
        "description": "操作：list=列出所有感知模块、create=创建新模块、validate=校验模块完整性"
      },
      "name": {
        "type": "string",
        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
        "description": "感知模块目录名（create/validate 用）"
      },
      "sense_content": {
        "type": "string",
        "description": "初始 sense.md 内容（create 用，Markdown 格式的感知数据模板，含占位数据）"
      },
      "data_update": {
        "type": "string",
        "description": "自定义 data_update.py 内容（create 用），不传则使用模板骨架"
      }
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```

---

### 步骤 3：验证

- `python -m py_compile plugins/sense_creater/tool.py`
- 确认 `list` 返回现有 `example` 模块
- 确认 `create` 在 `global_sense/` 下正确生成 3 个文件
- 确认 `create` 同名模块报 `FileExistsError`
- 确认 `validate(name="example")` 返回 `valid: true`
- 确认 `validate(name="broken_module")` 返回具体错误
- 确认模板 `data_update.py` 可独立运行（`python data_update.py`）
- 确认 `sense.json` 5 个字段完整（name、data_md、recent_update、health、start_update）
- 确认 `sense.md` 内容与传入的 `sense_content` 一致
- 确认原子写入：创建失败不残留半成品

---

## 应达到的效果

1. 主智能体在收到"创建感知"类请求时，走四步判断流程，不直接调 `create`
2. `create` 传入 name + sense_content，自动生成 3 个文件
3. `sense.json` 5 个字段自动生成，格式永不错
4. `data_update.py` 提供可直接运行的骨架模板（含 collect_data/render_markdown/update 签名和 TODO 标记）
5. `sense.md` 初始内容由用户和主智能体共同设计
6. 原子写入（临时目录 → os.replace），创建失败不残留
7. `list` 可查看所有感知模块及其健康状态
8. `validate` 可校验 sense.json 字段完整性和 sense.md 存在性

## 与其他创建器对比

| | subagent_dispatch create | expand_creater create | skill_creater create | sense_creater create |
|---|---|---|---|---|
| 目标 | 子代理 | 拓展模块 | 技能 | 感知模块 |
| scope | user | user / shared | agent_create / user_create / shared | global（固定） |
| 生成文件数 | 4 | 5 | 1（目录 + SKILL.md） | 3 |
| 四步流程 | ✅ | ✅ | ✅ | ✅ |
| 原子写入 | os.replace() | os.replace() 目录级 | os.replace() | os.replace() 目录级 |
