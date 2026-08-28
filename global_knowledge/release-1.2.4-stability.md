# 1.2.4 稳定性与外部智能体更新说明

本版本在 `1.2.3` 稳定性基础上增加外部智能体桥接，并收紧网页端重试状态和子智能体模型配置说明。
版本号推进到 `1.2.4`，不新增远程凭据配置字段，不改变 Chat 协议的固定思考档位。

## 主要变化

- 网页设置页明确说明默认、轻量、推理三个子智能体模型档位分别对应普通代理、摘要/上下文压缩/临时记忆整理，以及任务计划/自我改进/深度分析。
- 自动重试气泡在下一次尝试产生真实思考、正文或工具进度时立即消失；成功终态、失败终态、取消和切换会话都会清理本地提示状态。
- 新增可选 `agent_bridge.json` 合同。全局、共享或用户拓展可声明外部智能体名称、说明、输入/输出 Schema、拓展命令和超时。
- `subagent_dispatch action=list` 将本地代理和授权外部绑定统一列出；`action=call` 使用
  `external:<scope>:<module>:<name>` 句柄同步调用外部代理。
- 外部调用通过已有拓展隔离子进程执行，复用拓展白名单、清单校验、符号链接检查、模块锁、取消、超时和结果大小限制。
- 外部桥接输入和输出在核心边界再次校验 JSON Schema；远程地址、访问令牌、密码、Cookie 和私钥必须留在拓展私有配置或环境变量中。
- 没有统一的持久任务状态、取消、恢复和幂等合同前，外部代理不支持 `wait=false`，避免留下无法管理的远程任务。

## 兼容边界

- 内置和用户本地子代理的 `agent.json`、`agent-config.json`、`AGENT.md`、`trigger.md` 和执行器合同保持不变。
- `agent_bridge.json` 是拓展的可选附加文件；没有该文件的拓展行为不变。
- 外部绑定不会继承主对话历史、主智能体工具、用户技能或用户配置中的凭据，只接收 `input` 显式传入的数据。
- 外部调用失败不会伪造本地子代理成功，也不会自动重复可能产生副作用的远程操作。

## 发布验证

```powershell
D:\Anaconda3\envs\kemo\python.exe -m pytest -q tests/agents tests/provider_tool_recovery
D:\Anaconda3\envs\kemo\python.exe -m pytest -q tests
cd web/frontend
npm test -- --run
npx tsc -b
npm run build
```

发布前还应运行 `开发临时目录/release_check.py`。所有测试输出、日志和桥接返回都不得包含
API Key、Token、Cookie、密码、私钥或其他敏感凭据。
