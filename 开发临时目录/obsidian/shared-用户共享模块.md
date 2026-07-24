---
type: domain_overview
project: kemo-agent
domain: project
module: shared-用户共享模块
layer: L1
scope: shared
status: active
summary: shared_* — 用户共享模块
source: "shared-用户共享模块.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, shared, 共享, skills, knowledge, expand]
created: 2026-07-15
corrections: 2026-07-21 补充实际目录结构和注册机制
---
# shared_* — 用户共享模块

三类共享目录，面向多用户场景。通过 `register.py` 注册到 PromptSourceRegistry。

## 目录结构

```
shared_expand/   → register.py     # 共享拓展注册
shared_knowledge/ → data_structure.md  # 共享知识库索引
shared_skills/   → register.py     # 共享技能注册
```

## 共享内容

### shared_skills
- 用户共享技能，通过 `register.py → registry.add_expand(scope="shared", ...)` 注册
- 优先级：高于 `plugins`，低于用户 `user_skills`
- 受 `source_policy.shared_skills` 白名单控制

### shared_expand
- 用户共享拓展段（注入到 prompt 第 13 段）
- 通过 `shared_expand/register.py` 中的 `registry.add_expand(scope="shared", module, inject_file)` 注册
- 受 `source_policy.shared_expand` 白名单控制

### shared_knowledge
- 用户共享知识库索引
- 存放 `data_structure.md` 等索引文件
- 通过 `config.json` 中 `knowledge.use_shared` 控制启用
- 不可被更新脚本覆盖

## 注册机制

每个 `shared_*` 目录下的 `register.py` 在 `PromptSourceRegistry` 初始化时被加载：

```python
# shared_expand/register.py 示例
def register(registry):
    registry.add_expand("shared", "my_module", "my_file.md")
```

## 优先级

用户级 > 共享级 > 全局/内置级

## 相关笔记

- [[run-prompt_sources]]（PromptSourceRegistry 注册与选择）
- [[run-source_policy]]（共享资源白名单策略）
- [[users-多用户系统]]
- [[global_knowledge-全局知识库]]
- [[global_expand-全局拓展]]
