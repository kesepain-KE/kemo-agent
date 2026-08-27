# 1.2.3 稳定性补丁说明

这份说明对应 2026 年 8 月 26 日的版本小更新。目标是收紧已有运行边界，降低长时间运行、工具重试和后台进程操作的风险，不新增用户配置字段。

## 主要变化

- Kemo Provider 工具参数在执行前必须是完整 JSON 对象，并通过请求 Schema 校验；非法参数统一返回明确的 `response.incomplete`，不会把残缺参数交给插件。
- 流式 `tool_call.completed` 事件在统一终态校验后才发布。并行调用采用批次级原子语义，同批出现非法调用时不提前发布其他调用。
- Schema 校验有递归深度、节点总数和数组项上限，防止深层或超大参数造成递归崩溃与无界 CPU 消耗。
- Shell 后台 Worker 负责执行作业截止时间。日志文件无法写入时仍会 drain stdout/stderr，避免子进程阻塞；公开状态只返回项目根相对路径。
- 取消后台作业前再次确认 PID 的进程名和启动时间。身份不确定时拒绝破坏性操作，防止 PID 复用误杀。
- 对话在取消、Provider 异常、缺失终态和参数重试时幂等归档当前正文与思考，避免同一轮跨 attempt 重复累加。
- Provider 诊断支持更多敏感字段别名、前缀 JSON 错误和循环引用保护，并限制递归深度、节点数、集合项数及消息扫描长度。

## 兼容边界

- Chat 协议的固定思考档位不变。
- 长任务、任务计划、记忆和用户配置的数据格式不变。
- 后台作业的取消接口可能在无法确认进程身份时返回失败，这是保护行为，不是静默强杀。
- 未执行提交、推送或部署重启；发布前必须运行项目 CI 与本地全量测试。

## 发布验证

```powershell
D:\Anaconda3\envs\kemo\python.exe -m pytest -q tests
cd web/frontend
npm test -- --run
npm run build
```

网关项目还要运行 `tests/test_tool_arguments.py` 及完整 pytest。所有测试输出、日志和报告都不得包含 API Key、Token、Cookie、密码或私钥。
