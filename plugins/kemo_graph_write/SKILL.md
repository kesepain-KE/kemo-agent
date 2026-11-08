# kemo_graph_write

按需生成 global:kemo_graph 的操作参数。仅可生成写入操作；删除、清理和维护仍须满足用户确认及管理员权限。

## 使用原则

- 仅在用户明确要求操作 Kemo Graph 文档站时调用。
- 只使用注册表中可见的 Library ID，不传裸 Store 路径。
- 普通检索优先使用 `query`；数据更新按 `scan → 用户确认 → sync → ingest → status` 执行。
- 网关不可用不影响本地知识库与对话。
