# Kemo 2.0 接口影响清单

- `protocol_version` 请求字段改为必填，仅接受精确 `2.0`；所有公开端点统一校验 `X-Kemo-Protocol-Version`。
- Provider `create()` 在 `n>1` 时返回 `KemoResponseBatch`，不再返回裸数组。
- `generation.parallel_tool_calls` 删除，改为 `KemoRequest.parallel_tool_calls`。
- `structured_output`、`tool_choice`、`prediction`、`service` 成为原生请求字段。
- Item 不再携带 `metadata/extensions`；拒答、引用和 logprobs 有独立强类型字段。
- 流式新增 `response.in_progress`、`output_text.done`、`output_refusal.delta/done`，并要求 `previous_sequence`。
- Embedding 新增 `truncate/truncated_ids`；Rerank 新增 `score_threshold/filtered_count`。
- Chat 兼容层只能生成 effective request 和诊断，不能反向放宽 Kemo 原生模型。
