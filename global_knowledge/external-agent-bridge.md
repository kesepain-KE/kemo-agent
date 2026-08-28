# 外部智能体桥接合同

`kemo-agent 1.2.4` 支持把已经授权的拓展模块包装成外部子智能体入口。该能力用于连接另一个
kemo-agent、其他 Agent 服务或本地代理程序，同时保留核心现有的权限、超时、取消和结果大小边界。

## 设计原则

- 核心不接收模型或用户临时提供的任意远程 URL。
- 外部连接由拓展的 `start_expand.py` 执行，拓展继续运行在现有隔离子进程中。
- 远程 URL、Token、密码、Cookie 和私钥只从拓展自己的受信环境变量或私有配置读取，不写入
  `agent_bridge.json`、Prompt、回复、历史或日志。
- 外部代理必须显式放在全局、共享或当前用户的拓展目录中，并通过现有拓展白名单和 `open_control`
  开关授权。
- 当前只支持同步调用。外部服务没有接入 kemo-agent 的持久任务状态、取消和恢复合同前，不允许
  使用 `wait=false` 创建无人管理的后台任务。

## 文件位置

在拓展模块同一目录增加可选文件：

```text
global_expand/<module>/agent_bridge.json
shared_expand/<module>/agent_bridge.json
users/<user>/expand/<module>/agent_bridge.json
```

拓展本身仍须有有效的 `expand.json`、`start_expand.py` 和 `expand_control.md`，并且
`open_control=true`。没有 `agent_bridge.json` 的拓展不作为外部智能体发现。

## `agent_bridge.json`

根节点只允许 `schema_version` 和 `agents`：

```json
{
  "schema_version": 1,
  "agents": [
    {
      "name": "researcher",
      "description": "外部研究智能体",
      "command": "external_agent_call",
      "input_schema": {
        "type": "object",
        "properties": {"request": {"type": "string"}},
        "required": ["request"],
        "additionalProperties": false
      },
      "output_schema": {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": false
      },
      "timeout": 600
    }
  ]
}
```

每个代理项的规则：

| 字段 | 规则 |
|---|---|
| `name` | 以字母开头，只能使用字母、数字、`_`、`-`，最长 64 字符 |
| `description` | 非空，最长 2000 字符；写清外部代理负责什么 |
| `command` | 传给拓展 `start_expand.py` 的合法命令名 |
| `input_schema` | 可选 object JSON Schema，省略时使用宽松对象 Schema |
| `output_schema` | 可选 object JSON Schema，省略时使用宽松对象 Schema |
| `timeout` | 可选正数，最大 3600 秒，默认 600 秒 |

桥接文件最大 256 KiB，最多声明 64 个代理。名称、命令、Schema 和超时在发现与调用时都会重新
校验；解析失败只会隐藏该绑定，不会导致本地内置/用户子代理整体不可用。

## 发现和调用

主智能体使用 `subagent_dispatch action=list` 查看统一列表。内置/用户代理继续使用原名称；外部
代理使用不可冲突的句柄：

```text
external:<scope>:<module>:<name>
```

例如：

```text
external:user:remote_bridge:researcher
```

调用格式：

```json
{
  "action": "call",
  "agent": "external:user:remote_bridge:researcher",
  "wait": true,
  "input": {"request": "请整理这份资料"}
}
```

核心会按当前用户重新确认拓展作用域、白名单、清单、符号链接和输入 Schema，然后通过拓展隔离
进程调用声明的命令。传给 `start_expand.py` 的 `params` 为：

```json
{
  "agent": "researcher",
  "input": {"request": "请整理这份资料"},
  "protocol": "kemo-agent-external-agent-v1"
}
```

拓展应返回：

```json
{
  "status": "completed",
  "data": {"answer": "外部代理结果"},
  "usage": {"total_tokens": 123},
  "model": "remote-model"
}
```

`status` 为 `error`、`failed` 或 `failure`，或 `ok=false` 时，核心按失败处理；成功结果中的
`data` 必须通过声明的输出 Schema。核心只把受限的状态、数据、用量和模型名称返回给主智能体，
不会把桥接文件路径或内部认证信息返回。

## 稳定性与副作用

- 外部调用的等待期限最大 3600 秒；调用方期限到达且任务仍在运行时先返回本地 `task_id`，底层拓展继续保留配置的收尾存活期，最终执行上限还会额外包含该存活期。当前 Run 取消会传给拓展隔离进程。
- 拓展的模块执行锁仍然生效，同一模块的采集和操控不会并行破坏共享状态。
- 外部代理可能产生不可逆副作用时，调用方必须遵循拓展操作手册中的确认规则；核心不会假装远程
  操作具有幂等性，也不会在没有明确合同时自动重复调用。
- 外部代理当前不进入 `AgentRunner` 的本地工具白名单，不继承主会话历史、主智能体工具或用户技能。
  它只接收 `input` 中显式传入的数据。
- 使用 `wait=false` 会被明确拒绝，而不是悄悄放入本地子代理队列。将来若外部服务提供统一的
  持久任务 ID、状态、取消、恢复和幂等合同，可再单独扩展后台模式。

## 验收

至少验证以下路径：

1. `action=list` 能列出合法的外部绑定，且不列出未授权或损坏的拓展。
2. `action=call` 能通过拓展命令获得结构化 `data`，输入和输出 Schema 错误会在调用边界拒绝。
3. global/shared 拓展不在用户白名单时不能调用；user 拓展只能属于当前用户。
4. 符号链接、越界路径、超大桥接文件、非法命令和超时值不会被接受。
5. `wait=false` 明确失败，取消和拓展异常不会留下本地后台任务。
6. 测试不得在输出、日志或快照中写入 URL 中的凭据、Token、密码、Cookie 或私钥。
