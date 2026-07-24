---
type: component
project: kemo-agent
domain: run
module: run-history
layer: L2
updated: 2026-07-22
status: active
summary: run/history.py — 双层历史存储（无界归档 + 有界 temp 工作区）
source: "run/run-history.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, run, 历史, 窗口, 会话, 双层, temp, max_rounds, undo]
---

# run/history.py — 双层历史存储

`E:\code\kemo-agent\run\history.py`

## 概览

双层会话存储架构：

- **归档层** (`history/<window>/`)：保存用户可见的完整对话，不受轮次上限影响，不允许上下文整理裁剪。`data.json` 只保存持久元数据，不保存 context/summary 诊断。
- **临时工作区** (`history/temp/<window>/`)：上游 Provider 使用的可变上下文窗口，受 `agents.max_rounds` 限制，保存压缩统计和局部轮号偏移。

两层都包含 text.json、think.json、tool.json、items.json 和 data.json。data.json 以 `complete=true` 原子提交。

## 常量

### _ARCHIVE_DATA_FIELDS

```python
_ARCHIVE_DATA_FIELDS = frozenset({
    "schema_version", "user", "source", "session_id", "title",
    "created_at", "updated_at", "rounds", "round_metrics",
    "token_usage", "complete",
})
```

归档层 `data.json` 只允许这些字段。temp 工作区可以额外保存 `context` 诊断。

## 核心函数

### commit_window

```python
def commit_window(directory: Path, window: dict) -> None
```

原子提交窗口。如果 `directory.parent.name == "temp"`，保存完整 data 字段；否则（归档层），只保留 `_ARCHIVE_DATA_FIELDS` 中的字段。保留磁盘上已有标题。

### runtime_window_path

```python
def runtime_window_path(archive_directory: Path) -> Path
```

返回 temp 工作区路径：`archive_directory.parent / "temp" / archive_directory.name`。

### _trim_to_max_rounds

```python
def _trim_to_max_rounds(window: dict, max_rounds: int) -> dict
```

deep-copy 窗口并只保留最近 `max_rounds` 轮。关键逻辑：
- 按 user 消息分组 text.json 消息，只保留最后 N 组
- think/tool/rounds 和 items 的 `round` 号重映射为局部连续编号
- `data.context.round_offset` 记录被裁掉的全局轮数
- `data.context.workspace_rounds` 记录裁后轮数

### undo_last_round (2026-07-22 新增)

```python
def undo_last_round(
    root, user, source, session_id, *,
    expected_round: int, expected_prompt: str,
) -> dict[str, Any]
```

撤销最近一次成功提交的对话轮次，同时回写归档层和 temp 工作区。

- `expected_round == current + 1`：代表前端被中断的轮次，安全 no-op
- `expected_round != current`：拒绝，防止过期浏览器撤销无关轮次
- 验证 `expected_prompt` 与最后一轮用户消息一致
- 返回 `{found, rolled_back, round, remaining_rounds, prompt, content}`

配合 `_remove_last_round` 和 `_usage_from_round_metrics` 使用：

### _remove_last_round (2026-07-22 新增)

```python
def _remove_last_round(window: dict, round_number: int) -> dict
```

从窗口 deep-copy 中移除指定轮次的全部痕迹：text/think/tool/items/round_metrics，并重建 `token_usage` 和清除 `context` 诊断。

### _usage_from_round_metrics (2026-07-22 新增)

```python
def _usage_from_round_metrics(metrics: list) -> dict
```

从剩余的 `round_metrics` 重新累加 token 用量，填补被撤销轮次的份额，包括 stages、provider_raw、media、measurement 等嵌套字段。

### _message_rounds / _local_round

辅助函数：按 user 消息分组、局部轮号计算。

### load_runtime_window

```python
def load_runtime_window(
    archive_directory: Path,
    archive_window: dict | None = None,
    *,
    max_rounds: int = 80,
) -> tuple[Path, dict]
```

加载 temp 工作区：
1. 如果 temp 已有 data.json → `load_window` + `_trim_to_max_rounds`
2. 如果不存在 → 从归档 deep copy + 移除旧 context + `_trim_to_max_rounds`

损坏的 temp 工作区不影响归档可用性。

## Items v2 核心函数

### synthesize_items / append_round_items / _item_id / _text_content / _history_message_item

与上一版一致，从旧版 text/think/tool 合成 Items v2 结构或追加结构化条目。

### queue_memory_extraction — 2026-07-23 新增

```python
def queue_memory_extraction(root, user, source, session_id) -> dict[str, Any]
```

会话关闭时持久化排队待提取的记忆轮次：
- 查找归档窗口，读取 `memory_processed_round` 游标
- 检查 `extraction_mode`，`disabled` 模式直接跳过
- 有未处理轮次时将 `memory_status` 改为 `queued`
- 返回 `{status, reason, rounds, processed_round, pending_rounds?}`
- 被 `message/router.py` 的 `/new` 指令和 `web/service.py` 的 `close_session` 调用

## 旧版函数（保留向后兼容）

### empty_window / load_window / find_window / prepare_window / get_or_create_window

### list_sessions / clear_session / rename_session / delete_session / delete_all_sessions / session_messages

## 关键变更（2026-07-22 追加）

### 持久化会话索引集成

`commit_window` 在归档层提交后自动调用 `history_index.upsert_window` 更新可重建索引。

### 窗口命名改为 conv_id

`_window_name(session_id)` 优先使用 `conv_` 前缀的会话 ID 作为目录名，旧时间戳目录保持兼容。

### _ARCHIVE_DATA_FIELDS 扩展

新增 `memory_processed_round`、`memory_status`、`memory_error` 字段。

### provider_request_count

`empty_window` 和 `_usage_from_round_metrics` 新增 `provider_request_count` 字段跟踪 Provider 请求次数。

### list_sessions 使用索引

`list_sessions` 改为从 `history_index.list_records` 读取，不再遍历目录。返回字段新增 `conversation_id`、`summary`、`state`、`run_state`、`chain`。

### rename/delete 同步索引

`rename_session` 和 `delete_session`/`delete_all_sessions` 操作后同步更新索引。

### undo_last_round 记忆游标

`_remove_last_round` 中同步调整 `memory_processed_round` 和 `memory_status`。

## 变更记录

| 旧版 | 新版 |
|------|------|
| 四文件历史管理 | 五文件双层历史：归档(无界) + temp(有界 max_rounds) |
| `commit_window` 无差别写全部 data | 归档层过滤非 `_ARCHIVE_DATA_FIELDS` 字段 |
| 无 `_trim_to_max_rounds` | temp 工作区裁剪到 max_rounds 轮，局部轮号 + round_offset |
| `load_runtime_window` 无 max_rounds 参数 | 新增 `max_rounds` 关键字参数，默认 80 |
| 归档 data.json 保存 context 诊断 | 归档 data.json 不保存 context/summary；temp 保存 |
| 无撤销机制 | `undo_last_round` 精确回滚单轮，含并发保护和过期请求检测 |
| 时间戳窗口命名 | `conv_<uuid>` 机器标识符，兼容旧目录 |
| `list_sessions` 遍历目录 | 使用 `history_index.list_records` 读取 |
| 无记忆游标 | `memory_processed_round` / `memory_status` 跟踪提取进度 |
| commit 不更新索引 | `commit_window` 自动调用 `history_index.upsert_window` |

## 相关笔记

- [[run-总览]]
- [[run-engine]]
- [[run-runtime_host]]
- [[run-history_index]]
- [[web-service]]