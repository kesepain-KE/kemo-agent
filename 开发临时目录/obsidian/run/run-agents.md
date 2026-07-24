---
type: component
project: kemo-agent
domain: run
module: run-agents
layer: L2
scope: project
status: active
summary: run/agents.py — 子代理清单发现与校验
source: "run/run-agents.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, run, 子代理, 注册, 清单]
---
# run/agents.py — 子代理清单发现与校验

`E:\code\kemo-agent\run\agents.py`

## 概览

确定性扫描 `agents/*/agent.json`，校验并加载为 AgentDefinition。

## 类

### AgentError / AgentManifestError / AgentDisabledError

错误类。

### AgentDefinition (frozen dataclass)

```python
@dataclass(frozen=True)
class AgentDefinition:
    name: str
    version: str
    description: str
    enabled: bool
    instruction_file: str       # 同目录指令文件名
    instruction: str            # 已读取的指令内容
    model_profile: str          # default/cheap/reasoning
    timeout: float
    execution: str              # sync/background_serial
    write_policy: str           # none/derived_cache/user_memory/user_task
    input_schema: dict
    output_schema: dict
    directory: Path
    manifest_path: Path
```

### AgentRegistry

```python
@dataclass(frozen=True)
class AgentRegistry:
    agents: dict[str, AgentDefinition]

    def enabled_agents() -> list[AgentDefinition]
    def get(name) -> AgentDefinition    # 禁用时抛 AgentDisabledError
```

## 函数

### discover_agents

```python
def discover_agents(root: Path) -> AgentRegistry
```

确定性扫描 `agents/*/agent.json`（按名称排序），校验：名称格式 / 目录名一致性 / 重复名称 / 必填字段 / schema_version / JSON 合法性。

### _load_manifest

```python
def _load_manifest(path: Path) -> AgentDefinition
```

读取并校验单个 agent.json → 注入 instruction 内容 → 返回 AgentDefinition。

### _object_schema

```python
def _object_schema(value, *, field, path) -> dict
```

校验 JSON Schema 的 type 为 object。
