---
type: component
project: kemo-agent
domain: plugins
module: plugins-manifest
layer: L2
scope: project
status: active
summary: plugins/manifest.py — 插件 SKILL.md 清单解析
source: "plugins/plugins-manifest.md"
updated: 2026-07-18
verified: true
tags: [kemo-agent, plugins, manifest, SKILL.md]
corrections: 2026-07-18 从 run/skill_manifest.md 迁移至此；原文件名为 ghost note（run/skill_manifest.py 不存在），实际源码在 plugins/manifest.py 和 run/prompt_sources.py
---

# plugins/manifest.py — 插件 SKILL.md 清单解析

实际代码分布在两个文件中：

- **`plugins/manifest.py`**：`PluginManifest`、`PluginManifestError`、`discover_plugin_manifests()`
- **`run/prompt_sources.py`**：`SkillDescriptor`、`parse_skill_descriptor()`（作用于 SKILL.md 的指令描述部分）

## 概览

这层负责把插件 `SKILL.md` 解析成：

- `SkillDescriptor`：供 prompt 使用的说明摘要
- `PluginManifest`：供工具系统使用的结构化清单

## 关键结构

### SkillDescriptor

```python
@dataclass
class SkillDescriptor:
    title: str
    description: str
    path: Path
    relative_path: str
    scope: str
```

### PluginManifest

```python
@dataclass
class PluginManifest:
    descriptor: SkillDescriptor
    tool: dict[str, Any]
```

## 解析规则

### parse_skill_descriptor

- 读取 `SKILL.md`
- 找一级标题 `# ...`
- 从标题后的文本中提取简介
- 遇到二级标题或分隔线就停止描述采集

### PluginManifest 解析逻辑

`plugins/manifest.py` 中 `_parse_manifest(path)` 负责：

1. 读取 `SKILL.md`
2. 解析一级标题作为 `title`
3. 解析 `## Tool` 下的 JSON 代码块，校验：
   - 必填字段：`name`, `description`, `input_schema`, `version`, `enabled`, `entrypoint`
   - `input_schema.type == "object"`
   - `entrypoint` 必须是同目录下的 `file.py:function`
   - 不允许跳出插件目录
4. 返回 `PluginManifest(descriptor=SkillDescriptor(...), tool={...})`

### discover_plugin_manifests

```python
def discover_plugin_manifests(root: Path) -> tuple[PluginManifest, ...]
```

只扫描 `plugins/*/SKILL.md`，按自然排序返回清单元组。

## 相关笔记

- [[run-tools|tools.py]]
- [[run-prompt_sources]]
- [[原理-工具调用]]
- [[原理-PromptBundle编排]]
- [[plugins-expand_creater]]（新增插件）
- [[plugins-sense_creater]]（新增插件）
