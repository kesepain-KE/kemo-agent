# file

文件操作工具集。提供读取、写入、编辑、搜索、校验和目录操作等本地文件能力。
无沙箱限制，可直接操作任意路径。

## 使用原则

1. **未知大文件先检查**：先用 `stat` 查看文件大小。框架对单次工具内联结果执行 100,000 字符硬限制，超限正文会被完全省略并要求缩小范围；因此只有确认是小文件时才使用 `read`，其他情况使用 `read_range` 的 `start_line`/`end_line` 或 `tail` 分段读取。底层扫描默认最多 50 MB，超过限制还会返回 `truncated=true`。
2. **编辑前后均要读取**：用 `edit` 前先读取并确认当前内容，优先使用 `read_range.lines` 中的显式行号；编辑后检查工具返回的 `preview`，必要时再次读取。
3. **批量操作先确认范围**：移动、复制或删除前先用 `list_dir`/`tree_dir` 查看范围。目录结果带 `total`、`has_more` 和 `next_offset`；存在下一页时沿 `next_offset` 分页读取，不能把当前页误当完整目录。`delete` 只删除文件，不删除目录。
4. **搜索与浏览分工**：找文件或搜索代码使用 `search`；查看目录树使用 `tree_dir`；查看单层目录使用 `list_dir`。
5. **目录复制须显式授权**：复制目录必须设置 `recursive=true`。文件和目录都不能复制或移动到自身子目录。
6. **编码回退**：默认 UTF-8，解码失败时尝试 UTF-8 BOM 和操作系统首选编码；可通过 `encoding` 显式指定。
7. **文件校验**：下载、复制或移动后可用 `hash` 计算 MD5、SHA1 或 SHA256 校验完整性。
8. **小改动禁止整文件覆盖**：已有文件的小范围修改必须使用 `edit`，不要用 `write` 重写全文。精确文本块使用 `replace_text`，单行使用 `replace_line`，连续多行或指定列范围使用 `replace_range`，在某行某列插入时使用 `insert`；删除完整行必须使用 `delete_line`/`delete_range`，禁止用空 `replace_range` 猜测删除语义。
9. **精确替换优先**：编辑的新内容推荐使用 `new_text`；`content` 只为兼容旧调用保留。`replace_text` 默认保持 `expected_count=1`，匹配失败后重新读取目标内容，不要直接改成 `-1`。只有用户明确要求批量替换时才使用 `expected_count=-1`。
10. **行编辑必须带原文保护**：`replace_line`、`replace_range`、`delete_line`、`delete_range` 必须传入从最近一次读取获得的 `expected_old_text`；Tab、空格和文本必须一致。`insert` 必须传入最近一次读取返回的 `sha256` 作为 `expected_hash`。校验失败后重新读取，禁止猜行号继续修改。
11. **不要手工补行尾**：`replace_line` 和 `replace_range` 会保留目标区域原有的行尾，`new_text` 末尾无需附加换行。编辑器会保留文件编码、BOM、LF/CRLF/CR 和末尾换行；未修改区域不会被统一改写。
12. **新文件可直接安全续编**：`write` 成功后会返回完整文件 `sha256` 和有界的带行号 `lines` 快照。刚创建的文件可依据这些字段定位后续 `edit`；快照被截断时仍应先使用 `read_range` 获取目标区域，不能猜测行号。

## 输出字段

| 字段 | 适用 action | 说明 |
|------|-------------|------|
| `ok` | 全部 | 操作是否成功 |
| `error` | 全部 | 失败原因（`ok=false` 时） |
| `path` | 全部 | 操作路径 |
| `dst_path` | copy/move | 目标路径 |
| `content` | read/read_range | 正文；read_range 返回字符串数组 |
| `lines` | read_range | 带显式 1-based 行号的 `[{line, text}]` 数组；优先使用此字段定位编辑 |
| `sha256` | read/read_range/write | 完整读取或写入后整个文件的 SHA256，可作为后续 `expected_hash`；截断读取为空字符串，避免为读取日志尾部扫描整个大文件 |
| `sha256_complete` | read/read_range/write | `sha256` 是否覆盖整个文件；为 false 时如需插入操作，先显式调用 `hash` |
| `size` | read/stat | 原始文件大小（字节） |
| `read_bytes` | read | 实际读取字节数 |
| `truncated` | read/read_range/search | 是否因限制截断 |
| `total_lines` | read_range | 总行数（大文件可能是估算值） |
| `total_lines_estimated` | read_range | `total_lines` 是否为采样估算 |
| `shown` | read_range | 实际返回行数 |
| `type` | exists/stat/copy/move | file 或 dir |
| `exists` | exists | 路径是否存在 |
| `hash`/`algorithm` | hash | 十六进制哈希值及算法 |
| `before_hash`/`after_hash` | edit | 编辑前后的 SHA256 |
| `preview` | edit | 编辑后目标位置附近带行号的内容 |
| `changed` | edit | 文件内容是否实际发生变化 |
| `replacements` | edit | 实际替换次数 |
| `changed_lines` | edit | 本次触及的原始行数 |
| `newline_style` | edit | 编辑后换行风格：LF、CRLF、CR、mixed 或 none |
| `results` | search | 匹配结果数组 |
| `skipped_large` | search | 因超过单文件大小限制而跳过的文件数组 |
| `entries` | list_dir/tree_dir | 单层目录当前页条目数组，或目录树当前页条目数 |
| `items` | tree_dir | 当前页结构化目录树条目，包含相对路径、类型、深度和显示文本 |
| `total`/`returned` | list_dir/tree_dir | 符合条件的总条目数 / 当前页返回数 |
| `offset`/`limit`/`next_offset`/`has_more` | list_dir/tree_dir | 分页位置、页大小、下一页位置和是否仍有后续条目 |
| `recursive` | copy | 是否递归复制 |
| `deleted` | delete | 删除确认 |

任何 action 的完整 JSON 结果超过 100,000 字符时，框架都不会截取一段正文冒充完整结果，而是返回 `ToolResultTooLargeError`，包含原始字符数、限制和缩小范围提示。该受控拒绝不会计入同名工具连续失败次数。
