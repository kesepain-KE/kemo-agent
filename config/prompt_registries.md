# 提示词来源注册与用户资源解析

提示词来源分成两类：项目内受信任的静态注册模块，以及按当前用户动态解析的数据目录。中央管线位于 `run/prompt_sources.py`，`run/prompt.py` 不硬编码各来源的读取实现。

## 模块契约

每个注册模块必须导出：

```python
def register(registry) -> None:
    ...
```

静态模块固定加载顺序：

1. `global_expand/register.py`
2. `shared_expand/register.py`
3. `shared_skills/register.py`
4. `global_sense/register.py`

随后由 `agents/_runtime/user_resources.py` 解析当前调用用户：

1. `users/<user>/user_skills/**/SKILL.md`
2. `users/<user>/expand/<module>/inject.md`

用户目录不需要、也不允许通过 Python 注册模块接入。切换用户时会重新按目标用户目录解析，所以新增用户不需要修改项目静态注册表。

## 注册与过滤分离

- 注册阶段始终发现并保留完整库存，不读取用户白名单决定“是否注册”。
- 主智能体在 `run/prompt.py` 组装 Prompt 时，才通过 `run/source_policy.py` 按当前用户合并配置过滤技能、Expand、感知与知识范围。
- 主智能体白名单 `[]` 表示全量允许；非空数组表示精确白名单。配置值不接受 `"*"`、空字符串或非字符串项。
- 用户 Expand 由当前用户目录动态解析并全量注入；`expand.*_whitelist` 只控制 global/shared 两个静态层。
- 子代理不继承主智能体来源策略，也不与其求交集；每个子代理只服从自己目录内的 `agent-config.json`。

## Expand 注册

```python
from pathlib import Path


def register(registry) -> None:
    base = Path(__file__).resolve().parent
    registry.add_expand("global", "water", base / "water" / "inject.md")
```

静态 Expand 的 Scope 可为 `global` 或 `shared`。第二个参数是来源根目录的直接子模块名，第三个参数必须是该模块目录内的现有注入文件。

空静态 Expand 目录的 `register.py` 使用 `pass`。新增静态模块时增加 `registry.add_expand(...)` 调用。

用户 Expand 使用固定约定 `users/<user>/expand/<module>/inject.md`，可信解析器只选择直接子目录中的 `inject.md`，不执行用户代码。

## Skills 注册

```python
from pathlib import Path


def register(registry) -> None:
    registry.add_skills("shared", Path(__file__).resolve().parent)
```

静态 Skills 只注册 `shared` Scope。用户 Skills 由可信解析器直接绑定到当前用户的 `user_skills/`。两层都会递归发现 `SKILL.md`；技能只提供提示描述，不提供 Provider 工具。

技能白名单使用相对于技能层根目录的目录 ID，例如 `shared_skills/development/python/SKILL.md` 的 ID 是 `development/python`。

## Sense 注册

```python
from pathlib import Path


def register(registry) -> None:
    registry.add_perception(Path(__file__).resolve().parent)
```

仅允许 `global_sense/register.py` 注册感知根目录。

`global_sense/` 的每个直接子目录都是一个独立感知模块；模块内部递归读取非隐藏 Markdown。根目录 Markdown、隐藏目录、`__pycache__` 与非 Markdown 文件不会注入。`perception.global_whitelist` 使用直接子目录名作为模块 ID。

## 主智能体配置入口

| 配置 | 行为 |
|------|------|
| `knowledge.enabled` | `false` 时知识索引、正文搜索、`knowledge_search` 与未来图谱调用全部为空 |
| `knowledge.use_shared` | 控制共享知识是否进入有效范围 |
| `knowledge.use_global` | 控制全局知识是否进入有效范围；用户知识在总开关开启时始终有效 |
| `skills.shared_whitelist` | 共享 Prompt 技能过滤 |
| `skills.user_whitelist` | 当前用户 Prompt 技能过滤 |
| `expand.global_whitelist` | 全局 Expand 过滤 |
| `expand.shared_whitelist` | 共享 Expand 过滤 |
| `perception.global_whitelist` | 全局感知模块过滤 |
| `kemo_graph.enabled` | 仅记录请求状态；当前显示 `disabled` 或 `not_connected`，不启动或调用图谱 CLI |

## 安全与错误行为

- 注册目录必须与注册模块的规定位置严格一致。
- 同一来源不得重复注册。
- 注册模块缺少 `register()` 或执行失败时直接报错。
- Expand 注册仍校验重复项、路径逃逸和缺失文件。
- 用户目录中的 `register.py` 不会被导入；用户资源只能按目录约定提供数据。
- `registry.json` 不属于当前注册协议，任何层都不需要维护它。
