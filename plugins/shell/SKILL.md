# shell

系统命令执行工具。运行本地命令，支持会话模式、原生单进程命令链、可选命令解释器和跨平台输出解码。无沙箱限制。

## 使用原则

1. **专用工具优先**：有对应专用工具时不得使用 shell。文件读写用 `file` 插件，网络请求用 `network`，下载用 `download`。shell 仅在没有专用工具或需要执行系统级命令时使用。
2. **不可逆操作先确认**：执行 rm / del / format / 覆盖写入 / 批量删除等不可逆操作前，必须先列目录确认目标范围，向用户展示将要影响的内容，获确认后再执行。
3. **匹配当前平台**：在 Windows 环境优先使用 PowerShell 或 cmd 命令，不要直接使用 Unix 专属的 `head`；PowerShell 按条目限制可用 `Select-Object -First`，文件分段读取优先使用 `file.read_range`。在 Unix 环境使用对应 shell 命令。不确定操作系统时先探测运行环境。
4. **连续失败即停止**：同一命令连续失败 2 次即停止重试，向用户报告操作目标、错误信息和需要的帮助，不得以相同参数反复尝试。
5. **合理设置超时**：编译、下载、数据处理等长时间命令应主动设置 `timeout`；未显式设置时使用 `global_config.json → tools.timeout` 注入的运行时默认值，显式有效值会同时覆盖 shell 内部期限和框架外层看门狗。
6. **哈希能力降级**：Windows PowerShell 若缺少 `Get-FileHash`，优先改用 `file` 工具的 `hash` action；必须走系统命令时使用 `certutil -hashfile <path> SHA256`。shell 会在识别到这类失败时保留原错误并附加 `hint`。

## 会话模式

相同 `session_id` 的多次调用共享 cwd、环境变量和命令历史。会话按用户隔离，不同来源（web / cli / cron）互不相通。内置命令 cd / pwd / export / set / env / unset / history 操作会话状态，不创建子进程。

cat / type、ls / dir、mkdir、echo、rm / del 也以内置方式执行；rm / del 只允许删除文件，拒绝删除目录。未提供 `session_id` 时，文件类内置命令仍可使用，但 cwd 和环境变量不会跨调用保留。

支持命令链语法：`&&`（前序成功才执行）、`||`（前序失败才执行）、`;`（无条件顺序执行）。只要命令需要外部解释器，完整命令都会在同一个进程中执行，变量、函数和 Shell 会话状态不会因链操作符被拆散。显式使用 `powershell` 或 `pwsh` 时启用未定义变量和非终止错误保护；`pwsh` 对应 PowerShell 7，`powershell` 保留 Windows PowerShell 5.1 兼容入口。

框架内置命令组成的简单链仍由框架执行，并可使用 `chain_timeout_mode`。外部命令或脚本的 `timeout` 始终约束整个单进程脚本；如需在一个持久会话中切换目录，建议单独调用一次 `cd`，再用相同 `session_id` 执行后续命令。

## 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|:--:|------|
| `command` | string | ✅ | 要执行的命令，支持 && / \|\| / ; 命令链 |
| `working_dir` | string | | 工作目录，默认继承会话 cwd 或项目根 |
| `timeout` | int | | 超时秒数（1-3600），默认来自 `global_config.json → tools.timeout` |
| `stdin` | string | | 标准输入文本 |
| `env` | object | | 附加环境变量；会话模式下写入会话状态 |
| `session_id` | string | | 会话标识，相同值共享 cwd / env / history |
| `reset_session` | bool | | 重置指定 `session_id` 的状态 |
| `shell_type` | string | | 命令解释器：auto / cmd / powershell / pwsh / bash / bash_login，默认 auto |
| `chain_timeout_mode` | string | | 框架内置命令链超时策略：total（全链共享）/ per_command（逐段独立），默认 total；外部脚本始终整体计时 |

## Tool

```json
{
  "name": "shell",
  "description": "执行本地系统命令。专用工具优先，不可逆操作须先确认；支持会话、原生单进程命令链、解释器选择和跨平台输出解码。",
  "input_schema": {
    "type": "object",
    "properties": {
      "command": {"type": "string", "description": "要执行的命令，支持 &&、||、; 命令链"},
      "working_dir": {"type": "string", "description": "工作目录，默认继承会话 cwd 或项目根"},
      "timeout": {"type": "integer", "minimum": 1, "maximum": 3600, "description": "超时秒数，默认来自 global_config.json → tools.timeout"},
      "stdin": {"type": "string", "description": "标准输入文本"},
      "env": {"type": "object", "description": "附加环境变量"},
      "session_id": {"type": "string", "description": "会话标识；相同 session_id 共享 cwd/env/history"},
      "reset_session": {"type": "boolean", "description": "是否重置指定 session_id 的状态"},
      "shell_type": {
        "type": "string",
        "enum": ["auto", "cmd", "powershell", "pwsh", "bash", "bash_login"],
        "default": "auto",
        "description": "命令解释器：auto=系统默认、cmd=cmd.exe /c、powershell=Windows PowerShell 5.1、pwsh=PowerShell 7、bash=bash -c、bash_login=bash -l -c（登录 shell）"
      },
      "chain_timeout_mode": {
        "type": "string",
        "enum": ["total", "per_command"],
        "default": "total",
        "description": "框架内置命令链超时策略：total=全链共享 timeout，per_command=每段独立使用 timeout；外部脚本始终整体计时"
      }
    },
    "required": ["command"],
    "additionalProperties": false
  },
  "version": "1.3.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
