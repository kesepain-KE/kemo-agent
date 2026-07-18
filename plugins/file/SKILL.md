# file

文件操作工具集。提供读写、编辑、搜索、列目录、复制、移动、删除等本地文件操作。
无沙箱限制，可直接操作任意路径。

## Tool

```json
{
  "name": "file",
  "description": "文件操作工具集 — 读取、写入、编辑、列目录、搜索、复制、移动、创建目录、删除文件。通过 action 参数选择操作类型。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["read", "read_range", "write", "append", "edit", "list_dir", "tree_dir", "stat", "search", "copy", "move", "make_dir", "delete"],
        "description": "操作类型: read=读取整个文件, read_range=按行读取, write=覆盖写入, append=追加写入, edit=精确编辑, list_dir=列出目录, tree_dir=目录树, stat=文件信息, search=搜索文件, copy=复制, move=移动/重命名, make_dir=创建目录, delete=删除文件"
      },
      "path": {"type": "string", "description": "文件/目录路径"},
      "content": {"type": "string", "description": "write/append 模式的写入内容"},
      "encoding": {"type": "string", "description": "编码，默认 utf-8"},
      "start_line": {"type": "integer", "description": "read_range 起始行号(1-based)"},
      "end_line": {"type": "integer", "description": "read_range 结束行号; 0=按 max_lines 截断"},
      "tail": {"type": "integer", "description": "read_range 读取末尾 N 行"},
      "max_lines": {"type": "integer", "description": "read_range 最大返回行数，默认 500"},
      "edit_mode": {"type": "string", "enum": ["insert", "replace_line", "replace_range", "replace_text"], "description": "edit 编辑模式"},
      "old_text": {"type": "string", "description": "edit replace_text 模式下要替换的原文"},
      "expected_count": {"type": "integer", "description": "edit 期望匹配次数，默认 1; -1 跳过检查"},
      "line": {"type": "integer", "description": "edit 行号(1-based)"},
      "column": {"type": "integer", "description": "edit 列号(1-based)"},
      "create_backup": {"type": "boolean", "description": "edit 是否创建 .bak 备份，默认 true"},
      "dst_path": {"type": "string", "description": "copy/move 的目标路径"},
      "overwrite": {"type": "boolean", "description": "copy/move 是否覆盖"},
      "max_depth": {"type": "integer", "description": "tree_dir 最大深度，默认 2"},
      "max_entries": {"type": "integer", "description": "tree_dir 最大条目数，默认 200"},
      "include_hidden": {"type": "boolean", "description": "tree_dir/search 是否包含隐藏文件"},
      "query": {"type": "string", "description": "search 搜索关键词或正则"},
      "mode": {"type": "string", "enum": ["file", "name", "text", "content", "code"], "description": "search 搜索模式"},
      "file_glob": {"type": "string", "description": "search 文件名 glob 过滤"},
      "max_results": {"type": "integer", "description": "search 最大结果数，默认 50"},
      "context_lines": {"type": "integer", "description": "search 匹配行上下文行数"},
      "regex": {"type": "boolean", "description": "search query 是否按正则处理"},
      "parents": {"type": "boolean", "description": "make_dir 是否递归创建父目录，默认 true"}
    },
    "required": ["action", "path"]
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
