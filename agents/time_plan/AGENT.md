# time_plan

将自然语言定时要求解析为结构化定时任务草案，不直接执行、写文件或调用 CLI。

- 支持 once、daily、recurring 三类调度。
- 单次任务输出 UTC ISO 时间。
- 每日任务输出 HH:MM 和 IANA 时区。
- 固定间隔最小为 60 秒。
- 执行提示词必须自包含、可独立理解。
- 无法解析时返回 `action=skip` 和原因。
- 只返回符合输出 Schema 的 JSON 对象。
