---
type: component
project: kemo-agent
domain: message
module: message-identity
layer: L3
scope: project
status: active
summary: message/identity.py — 身份映射与工具权限
source: "message/message-identity.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, message, 身份, 绑定, 权限]
---
# message/identity.py — 身份映射与工具权限

`E:\code\kemo-agent\message\identity.py`

## 类

### IdentityBinding (frozen)

```python
@dataclass(frozen=True, slots=True)
class IdentityBinding:
    platform: str
    external_user_id: str
    internal_user: str
    chat_type: str | None = None        # 可选精确定位
    external_chat_id: str | None = None
```

`match_score(envelope)` → 无匹配返回 None，匹配返回 2~5 分（字段越多分越高）。

### IdentityResolver

```python
class IdentityResolver:
    def __init__(self, root, bindings)
    def from_config(root, config)
    def resolve(envelope) -> str          # 返回内部用户名
```

**resolve 逻辑**：
1. 所有绑定按 match_score 匹配
2. 取最高分 → 若多个不同 user → 冲突报错
3. 校验内部用户存在性（`user_dir`）
4. 返回唯一用户

### IdentityError

`IdentityError(RuntimeError)`

## 函数

### filter_tool_registry

```python
def filter_tool_registry(registry, allowed_tools) -> ToolRegistry
```

工具权限交集：
- `allowed_tools=None` → 不限制
- `allowed_tools=frozenset()` → 禁用所有工具
- 指定列表 → 只保留交集中的工具
