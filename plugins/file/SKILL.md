# file

文件操作工具集。提供读取、写入、编辑、搜索、校验和目录操作等本地文件能力。
无沙箱限制，可直接操作任意路径。

## 使用原则

1. **未知大文件先检查**：先用 `stat` 查看文件大小。框架对单次工具内联结果执行 20,000 字符硬限制，超限正文会被完全省略并要求缩小范围；因此只有确认是小文件时才使用 `read`，其他情况使用 `read_range` 的 `start_line`/`end_line` 或 `tail` 分段读取。底层扫描默认最多 50 MB，超过限制还会返回 `truncated=true`。
2. **编辑前后均要读取**：用 `edit` 前先读取并确认当前内容，优先使用 `read_range.lines` 中的显式行号；编辑后检查工具返回的 `preview`，必要时再次读取。`edit` 默认创建不覆盖的编号备份（`.bak`、`.bak.1`……），除非明确不需要，否则保持 `create_backup=true`。
3. **批量操作先确认范围**：移动、复制或删除前先用 `list_dir`/`tree_dir` 查看范围。`delete` 只删除文件，不删除目录。
4. **搜索与浏览分工**：找文件或搜索代码使用 `search`；查看目录树使用 `tree_dir`；查看单层目录使用 `list_dir`。
5. **目录复制须显式授权**：复制目录必须设置 `recursive=true`。文件和目录都不能复制或移动到自身子目录。
6. **编码回退**：默认 UTF-8，解码失败时尝试 UTF-8 BOM 和操作系统首选编码；可通过 `encoding` 显式指定。
7. **文件校验**：下载、复制或移动后可用 `hash` 计算 MD5、SHA1 或 SHA256 校验完整性。
8. **小改动禁止整文件覆盖**：已有文件的小范围修改必须使用 `edit`，不要用 `write` 重写全文。精确文本块使用 `replace_text`，单行使用 `replace_line`，连续多行或指定列范围使用 `replace_range`，在某行某列插入时使用 `insert`；删除完整行必须使用 `delete_line`/`delete_range`，禁止用空 `replace_range` 猜测删除语义。
9. **精确替换优先**：编辑的新内容推荐使用 `new_text`；`content` 只为兼容旧调用保留。`replace_text` 默认保持 `expected_count=1`，匹配失败后重新读取目标内容，不要直接改成 `-1`。只有用户明确要求批量替换时才使用 `expected_count=-1`。
10. **行编辑必须带原文保护**：`replace_line`、`replace_range`、`delete_line`、`delete_range` 必须传入从最近一次读取获得的 `expected_old_text`；Tab、空格和文本必须一致。`insert` 必须传入最近一次读取返回的 `sha256` 作为 `expected_hash`。校验失败后重新读取，禁止猜行号继续修改。
11. **不要手工补行尾**：`replace_line` 和 `replace_range` 会保留目标区域原有的行尾，`new_text` 末尾无需附加换行。编辑器会保留文件编码、BOM、LF/CRLF/CR 和末尾换行；未修改区域不会被统一改写。

## 参数说明

### 通用参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `path` | string | 操作路径（必填；相对路径基于运行上下文根目录） |
| `encoding` | string | 文本编码，默认 UTF-8 |

### 各 action 专属参数

| action | 额外参数 |
|--------|----------|
| `exists` | 无 |
| `stat` | 无 |
| `read` | `max_bytes`：最大底层读取字节数，默认 50 MB、最高 512 MB；返回正文仍受框架 20,000 字符硬限制 |
| `read_range` | `start_line`/`end_line`：行范围；`tail`：尾部 N 行；`max_lines`：最大返回行数；`max_bytes`：最大扫描字节数 |
| `write` | `content`：覆盖写入内容 |
| `append` | `content`：追加内容 |
| `edit` | `edit_mode`、`new_text`（推荐）/`content`（兼容）、`old_text`、`expected_old_text`、`expected_hash`、`expected_count`、`line`、`column`、`end_line`、`end_column`、`create_backup` |
| `list_dir` | 无 |
| `tree_dir` | `max_depth`、`max_entries`、`include_hidden` |
| `search` | `query`、`mode`、`file_glob`、`regex`、`max_results`、`context_lines`、`include_hidden`、`max_file_bytes` |
| `hash` | `algorithm`：md5/sha1/sha256，默认 sha256 |
| `copy` | `dst_path`、`overwrite`、`recursive`（目录必须为 true） |
| `move` | `dst_path`、`overwrite` |
| `make_dir` | `parents`：是否递归创建父目录，默认 true |
| `delete` | 无；只能删除文件 |

## 输出字段

| 字段 | 适用 action | 说明 |
|------|-------------|------|
| `ok` | 全部 | 操作是否成功 |
| `error` | 全部 | 失败原因（`ok=false` 时） |
| `path` | 全部 | 操作路径 |
| `dst_path` | copy/move | 目标路径 |
| `content` | read/read_range | 正文；read_range 返回字符串数组 |
| `lines` | read_range | 带显式 1-based 行号的 `[{line, text}]` 数组；优先使用此字段定位编辑 |
| `sha256` | read/read_range | 完整读取时整个文件的 SHA256，可作为后续 `expected_hash`；截断读取为空字符串，避免为读取日志尾部扫描整个大文件 |
| `sha256_complete` | read/read_range | `sha256` 是否覆盖整个文件；为 false 时如需插入操作，先显式调用 `hash` |
| `size` | read/stat | 原始文件大小（字节） |
| `read_bytes` | read | 实际读取字节数 |
| `truncated` | read/read_range/search | 是否因限制截断 |
| `total_lines` | read_range | 总行数（大文件可能是估算值） |
| `total_lines_estimated` | read_range | `total_lines` 是否为采样估算 |
| `shown` | read_range | 实际返回行数 |
| `type` | exists/stat/copy/move | file 或 dir |
| `exists` | exists | 路径是否存在 |
| `hash`/`algorithm` | hash | 十六进制哈希值及算法 |
| `backup_created` | edit | 是否创建备份 |
| `backup_path` | edit | 实际备份路径；连续编辑依次使用 `.bak`、`.bak.1` 等，不覆盖旧备份 |
| `before_hash`/`after_hash` | edit | 编辑前后的 SHA256 |
| `preview` | edit | 编辑后目标位置附近带行号的内容 |
| `changed` | edit | 文件内容是否实际发生变化；无变化时不会写盘或创建备份 |
| `replacements` | edit | 实际替换次数 |
| `changed_lines` | edit | 本次触及的原始行数 |
| `newline_style` | edit | 编辑后换行风格：LF、CRLF、CR、mixed 或 none |
| `results` | search | 匹配结果数组 |
| `skipped_large` | search | 因超过单文件大小限制而跳过的文件数组 |
| `entries` | list_dir/tree_dir | 目录条目数组或目录树条目数 |
| `recursive` | copy | 是否递归复制 |
| `deleted` | delete | 删除确认 |

任何 action 的完整 JSON 结果超过 20,000 字符时，框架都不会截取一段正文冒充完整结果，而是返回 `ToolResultTooLargeError`，包含原始字符数、限制和缩小范围提示。该受控拒绝不会计入同名工具连续失败次数。

## Tool

```json
{
  "name": "file",
  "description": "文件操作工具集 — 读取、写入、编辑、搜索、校验、列目录、复制、移动、创建目录、删除。大文件有截断保护，tail 模式从文件尾部有限读取，内容搜索排除已知二进制格式。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["exists", "read", "read_range", "write", "append", "edit", "list_dir", "tree_dir", "stat", "search", "hash", "copy", "move", "make_dir", "delete"],
        "description": "操作类型"
      },
      "path": {"type": "string", "minLength": 1, "description": "文件或目录路径"},
      "content": {"type": "string", "description": "write/append 写入内容；edit 旧版新文本参数，仅为兼容保留"},
      "new_text": {"type": "string", "description": "edit 的新文本（推荐）；replace_text 可传空字符串删除精确匹配，删除整行使用 delete_line/delete_range"},
      "encoding": {"type": "string", "description": "文本编码，默认 utf-8，失败时尝试系统编码"},
      "start_line": {"type": "integer", "minimum": 0, "description": "read_range 起始行号（1-based；0 表示默认）"},
      "end_line": {"type": "integer", "minimum": 0, "description": "read_range 结束行号或 edit replace_range/delete_range 结束行号"},
      "tail": {"type": "integer", "minimum": 0, "description": "read_range 读取尾部 N 行"},
      "max_lines": {"type": "integer", "minimum": 1, "maximum": 50000, "description": "read_range 最大返回行数，默认 500"},
      "max_bytes": {"type": "integer", "minimum": 1, "maximum": 536870912, "description": "read/read_range 最大读取字节数，默认 52428800（50 MB）"},
      "edit_mode": {"type": "string", "enum": ["insert", "replace_line", "replace_range", "replace_text", "delete_line", "delete_range"], "description": "edit 编辑模式；删除完整行必须使用 delete_line/delete_range"},
      "old_text": {"type": "string", "description": "edit replace_text 要替换的原文"},
      "expected_old_text": {"type": "string", "description": "replace_line/replace_range/delete_line/delete_range 当前目标区域的精确原文；必须来自最近一次 read_range"},
      "expected_hash": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$", "description": "读取时返回的整个文件 SHA256；insert 必填，其他编辑可选，用于拒绝陈旧写入"},
      "expected_count": {"type": "integer", "minimum": -1, "description": "edit 期望匹配次数；-1 跳过检查"},
      "line": {"type": "integer", "minimum": 1, "description": "edit 起始行号（1-based）"},
      "column": {"type": "integer", "minimum": 1, "description": "edit 起始列号（1-based）"},
      "end_column": {"type": "integer", "minimum": 0, "description": "edit replace_range 结束列号"},
      "create_backup": {"type": "boolean", "description": "edit 是否创建 .bak 备份，默认 true"},
      "dst_path": {"type": "string", "minLength": 1, "description": "copy/move 目标路径"},
      "overwrite": {"type": "boolean", "description": "copy/move 是否覆盖已有目标"},
      "recursive": {"type": "boolean", "description": "copy 是否递归复制目录；目录复制必须为 true"},
      "max_depth": {"type": "integer", "minimum": 0, "maximum": 50, "description": "tree_dir 最大深度，默认 2"},
      "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "tree_dir 最大条目数，默认 200"},
      "include_hidden": {"type": "boolean", "description": "tree_dir/search 是否包含隐藏文件"},
      "query": {"type": "string", "description": "search 搜索词或正则"},
      "mode": {"type": "string", "enum": ["file", "name", "text", "content", "code"], "description": "search 搜索模式"},
      "file_glob": {"type": "string", "description": "search 文件名 glob 过滤"},
      "max_results": {"type": "integer", "minimum": 1, "maximum": 5000, "description": "search 最大结果数，默认 50"},
      "context_lines": {"type": "integer", "minimum": 0, "maximum": 100, "description": "search 匹配行上下文行数"},
      "regex": {"type": "boolean", "description": "search 是否按正则处理 query"},
      "max_file_bytes": {"type": "integer", "minimum": 1, "maximum": 536870912, "description": "search 单文件最大读取字节数，默认 52428800（50 MB）"},
      "algorithm": {"type": "string", "enum": ["md5", "sha1", "sha256"], "description": "hash 算法，默认 sha256"},
      "parents": {"type": "boolean", "description": "make_dir 是否递归创建父目录，默认 true"}
    },
    "required": ["action", "path"],
    "additionalProperties": false
  },
  "version": "1.3.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
