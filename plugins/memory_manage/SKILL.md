# memory_manage

记忆管理插件。按当前用户隔离，支持列出、获取、单项/批量搜索、删除、编辑和新增记忆，覆盖三层临时记忆、永久记忆与临时重要记忆热画像。

## 使用原则

1. **层级选择**：
   - `seven_days`：7 天微记忆碎片，有权重和到期时间。
   - `one_month`：30 天微记忆碎片，有权重和到期时间。
   - `half_year`：180 天微记忆碎片，有权重和到期时间。
   - `permanent`：永久记忆碎片，不会过期，权重为 null。
   - `important`：单文件临时重要记忆热画像，由 `memory_temporary_important` 子代理自动维护，权重和到期时间为 null。**此文件不可删除，不可写入空内容。** 主智能体只允许 `get` 和 `search_by_title`/`search_by_content` 读取此层级，不得使用 `add`、`edit` 或 `delete` 操作。
2. **权限范围**：主智能体可以使用全部 action，但 `important` 层级只允许读取（`get`、`search_by_title`、`search_by_content`、`search_many`），禁止写入或删除；`self_improve` 子代理只能使用三个搜索 action，且后台 `context_compression` / `memory_promotion` 模式禁止读取 `important`，只有用户主动的 `manual_review` 保留只读白名单；`memory_temporary_important` 只能使用 `list/get`。候选、热视图、遗忘、永久协调和晋升均由运行时原子持久化。所有查看和搜索都不改变权重。
3. **搜索、列出与获取**：列出整层摘要使用 `list`；按文件名搜索使用 `search_by_title`；按正文搜索使用 `search_by_content`，只返回 snippet；三个搜索 action 均可传 `tier: "all"` 跨 `seven_days`、`one_month`、`half_year`、`permanent` 四个碎片层查询，结果中的 `tier` 与 `memory_ref` 用于精确定位；多个候选应优先使用一次 `search_many` 同时搜索标题和正文；获取单条完整正文使用 `get`。
4. **禁止空搜索**：两个搜索 action 的 query 都必须是非空字符串。列出全部记忆不能再依赖空 query，应使用 `list`，需要正文时再逐条 `get`。
5. **敏感凭据检测**：`add` 与 `edit` 会拒绝包含疑似密码、API Key、Token、Cookie 或私钥的内容。
6. **控制结果规模**：`list` 与搜索默认最多返回 50 条。`truncated=true` 表示还有结果，可缩小层级或关键词后继续查询。
7. **精确寻址**：`get`、`edit`、`delete` 使用 `tier + filename` 作为组合身份。即使异常数据中多个层级存在同名文件，也只操作指定层级；删除缺失正文的临时层孤儿索引会返回 `repaired_orphan=true`。
8. **稳定引用**：`list`、搜索及所有单条 CRUD 结果均返回 `memory_ref`，格式为 `tier:filename`；展示标题仍使用 `filename`，程序传递目标时优先保留 `memory_ref`。

## 参数说明

| 参数 | 适用 action | 默认值 | 说明 |
|------|-------------|--------|------|
| `action` | 全部 | 必填 | `list` / `get` / `search_by_title` / `search_by_content` / `search_many` / `add` / `edit` / `delete` |
| `tier` | 全部 | 必填 | 普通操作使用具体层级；三个搜索 action 可使用 `all` 一次搜索四个碎片层级；`all` 不适用于 list/get/增删改 |
| `query` | search_* | 必填 | 非空搜索关键词；列出全部请使用 list |
| `queries` | search_many | 必填 | 1–20 个 `{title, content}` 查询对象，每项至少提供一个非空字段 |
| `filename` | get / add / edit / delete | — | 记忆文件名 |
| `content` | add / edit | — | Markdown 记忆正文 |
| `new_filename` | edit | 无 | 重命名后的目标文件名 |
| `limit` | list / search_* | 50 | 最大返回条数（1–500） |
| `context_chars` | search_by_content | 240 | snippet 最大字符数（60–2000，包含省略号） |
| `case_sensitive` | search_* | false | 是否区分英文字母大小写 |

## 返回字段

| 字段 | 适用 action | 说明 |
|------|-------------|------|
| `entries` | list | 记忆摘要数组，不包含正文 |
| `memory_ref` | list / get / search_* / add / edit / delete | 稳定组合引用，格式为 `tier:filename` |
| `total` | list | 当前层级总条目数 |
| `content` | get | 单条完整记忆正文 |
| `featured_sources` | get important | 当前有效热画像来源的 `{tier, filename}` 数组 |
| `filename` | get / add / edit / delete | 记忆文件名 |
| `snippet` | search_by_content | 首次命中位置附近的有界片段 |
| `matches` | search_* | 匹配结果数组；跨层搜索时每项额外包含实际 `tier` |
| `weight` | list / get / search_* | 临时层权重；permanent 与 important 为 null |
| `expires_at` | list / get / search_* | 临时层到期时间；permanent 与 important 为 null |
| `total_matches` | search_* | 实际命中总数，包含被 limit 截断的结果 |
| `truncated` | list / search_* | 返回结果是否被 limit 截断 |

## Tool

```json
{
  "name": "memory_manage",
  "description": "按当前用户和记忆层级列出、获取、单项或批量搜索、增删改记忆。三个搜索 action 支持 tier=all 跨全部碎片层查询；self_improve 应用 search_many 一次匹配整批候选，候选持久化仍由运行时处理。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["list", "get", "search_by_title", "search_by_content", "search_many", "add", "edit", "delete"],
        "description": "记忆操作"
      },
      "tier": {
        "type": "string",
        "enum": ["seven_days", "one_month", "half_year", "permanent", "important", "all"],
        "description": "记忆层级"
      },
      "query": {
        "type": "string",
        "minLength": 1,
        "description": "search_* 使用的非空关键词；列出全部请使用 list"
      },
      "queries": {
        "type": "array",
        "minItems": 1,
        "maxItems": 20,
        "items": {
          "type": "object",
          "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"}
          },
          "additionalProperties": false
        },
        "description": "search_many 的批量标题和正文查询"
      },
      "filename": {
        "type": "string",
        "description": "get/add/edit/delete 使用的记忆文件名"
      },
      "content": {
        "type": "string",
        "description": "add/edit 使用的 Markdown 正文"
      },
      "new_filename": {
        "type": "string",
        "description": "edit 使用的重命名目标"
      },
      "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": 500,
        "default": 50,
        "description": "list/search 最大返回条数"
      },
      "context_chars": {
        "type": "integer",
        "minimum": 60,
        "maximum": 2000,
        "default": 240,
        "description": "search_by_content 的 snippet 最大字符数，包含省略号"
      },
      "case_sensitive": {
        "type": "boolean",
        "default": false,
        "description": "search_* 是否区分大小写"
      }
    },
    "required": ["action", "tier"],
    "additionalProperties": false
  },
  "version": "1.5.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
