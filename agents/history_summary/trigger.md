# 注册信息

- **名称**: history_summary
- **触发**: Maintenance 后台线程领取已关闭会话的 queued 摘要任务时调用
- **职责**: 从用户与助手最终正文生成历史卡片标题和短摘要
- **模型**: cheap
- **工具**: 无

# 操作信息

输入必须包含 `trigger=session_closed`、`session_id`、`target_round` 和 `rounds`。
长对话可分块调用，后续分块通过 `previous_summary` 接收前一批滚动结果。
不得读取主对话、知识库、工具日志、思考过程或记忆，不执行任何写入。
