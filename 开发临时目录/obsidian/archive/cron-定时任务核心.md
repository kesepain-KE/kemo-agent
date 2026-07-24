---
type: component
project: kemo-agent
domain: archive
module: cron-定时任务核心
layer: L2
scope: project
status: archived
summary: {创建/编辑/删除定时任务}
source: "archive/cron-定时任务核心.md"
updated: 2026-07-18
verified: partial
---
{创建/编辑/删除定时任务}
获取用户自然语言定时要求和当前 UTC 时间。
分析用户要求的调度类型：
|- 单次执行（once）：提取执行时间和时区，计算 UTC ISO 时间填入 schedule.start_at
|- 每日执行（daily）：提取 HH:MM 时间和时区，填入 schedule.time 和 schedule.timezone
|- 固定间隔（recurring）：提取间隔秒数，最小 60，填入 schedule.interval_seconds
提取任务标题（title）和执行提示词（prompt）。
prompt 是执行时发给智能体的完整指令，应该是自包含的、可独立理解的。
session_id 默认为 "cron"。
返回 action=create，包含 title、prompt、schedule、session_id。

{编辑定时任务}
获取现有任务数据和用户修改要求。
根据用户要求修改标题、提示词或调度配置。
返回 action=edit，包含修改后的 title、prompt、schedule、session_id。

{删除定时任务}
获取要删除的任务信息。
返回 action=delete。

{输出格式}
只返回符合 output_schema 的 JSON 对象，不使用 Markdown。
不直接执行任务、不写文件、不调用 CLI。
如果用户要求无法解析为定时任务，返回 action=skip 和 message 说明原因。
