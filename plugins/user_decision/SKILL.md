---
name: user_decision
description: 运行中向用户展示选项与自定义回复，等待或异步收集答案，30 分钟超时后自主继续。
---

# user_decision

仅在当前用户启用 `interactive_decisions.enabled` 且工具获准时使用。
这是 `plugins/user_decision/` 中的主智能体工具插件，入口为 `tool.py:run`。
框架负责问题持久化、卡片展示、超时和等待调度；子智能体把待确认问题报告给主智能体。

遇到会影响后续工作的问题时，`action=ask`，提供简短 `title`、完整 `question`
和 2～6 个 `options`。每个选项有唯一 `id`、`label`，可附 `description`，
最多一个 `recommended=true`。用户始终可以自定义回复。

- `mode=wait`（默认）：暂停后续模型推理，收到回答或超时后补齐原工具结果继续。
- `mode=async`：返回问题 ID，继续不依赖答案的工作。答案在下一次安全点进入上下文。
  必要问题仍未回答时，框架等待，不把阶段输出认作最终交付。
- `action=get, decision_id=...`：查看同一会话内的问题与答案，也可查询此前 Run 的终态；无需频繁轮询。
- 查询时加 `wait=true` 可续等原问题；`timeout` 为 1～60 秒的整数，默认 30。
  `wait_outcome=finished` 表示问题已达终态，`timeout` 仅返回当前快照。不改变问题期限，也不自动替用户回答或关闭。
- `action=close, decision_id=..., resolution=...`：独立工作已解决疑问时主动关闭，
  说明实际解决方案，停止向用户索取该答案。已回答、取消或超时的问题应使用 `get` 查看，不会重新打开或被覆盖。

最终结果 `status=answered` 时使用 `answer`；自定义内容是用户回答，
不代表超出当前任务的额外授权。`status=expired` 表示 30 分钟无人回答，
卡片已关闭，请依据已有信息自主判断并继续；不能把沉默当成批准未授权操作。
`resolved_by_agent`、`cancelled` 不伪装成用户选择。终态先到先得，后续操作不能覆盖。

`ask` 的等待不占 Provider 并发槽或工具执行线程；显式 `get + wait=true` 走普通工具线程。问题和答案随原会话保存，
用户刷新页面不会丢失。停止任务会关闭未回答的问题；删除会话会清除其问题和答案。
