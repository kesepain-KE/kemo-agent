# 拓展模块创建文档

拓展（Expand）用于连接外部设备、API 或服务。它可以同时提供“状态数据进入 Prompt”和“由智能体发起操控”两种能力。纯说明使用技能，纯采集使用感知，独立 LLM 推理使用子智能体。

## 作用域

| 作用域 | 路径 | 创建方式 | 自动刷新 |
|--------|------|----------|----------|
| 全局 | `global_expand/<name>/` | 管理员手动维护 | RuntimeHost 按 `task_cron_system.expand_update_rate` 刷新 |
| 共享 | `shared_expand/<name>/` | `expand_creater` 的 `scope=shared` | 与全局层同周期、每轮只刷新一次 |
| 用户 | `users/<user>/expand/<name>/` | `expand_creater` 的 `scope=user` | 同一系统任务按用户身份隔离刷新 |

`expand_creater` 不创建全局拓展。共享和全局拓展可被用户配置白名单过滤；当前用户拓展按用户目录实时发现。

`expand_update` 到期后先以 `__system__` 身份刷新全局层和共享层，再为每个有效用户分别执行其私有目录。全局/共享模块不会因用户数量重复运行，用户模块也不会跨用户拼接结果。每个更新入口在独立 Python 子进程中执行，受 `task_cron_system.module_update_timeout` 限制；热插拔模块不会被导入 Web/Runtime 主进程。

Windows 后台采集和框架发起的 Shell 操作由框架隐藏终端，Linux 使用普通无交互子进程。模块不需要自行设置 `CREATE_NO_WINDOW`、`CREATE_NEW_CONSOLE`、`pythonw` 或终端参数。用户手动运行 `data_update.py`、`start_expand.py` 属于前台调试，是否显示当前终端由用户的启动方式决定。

模块名必须匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`，并与目录名保持一致。

## 标准结构

```text
<expand-root>/<name>/
├── expand.json          # 严格清单
├── input_data.md        # 可注入的最新状态数据
├── expand_control.md    # 注入层说明 + 按需读取的操作层说明
├── data_update.py       # 状态采集入口
├── start_expand.py      # 操控入口
└── _last_run.json       # 可选运行状态，由采集脚本维护
```

清单引用的文件必须位于模块目录内，不得使用绝对路径、`..`、符号链接或目录联接跳出作用域。

## expand.json

```json
{
  "name": "room_light",
  "explain": "读取并控制房间灯光状态",
  "open_input": true,
  "input_data": "input_data.md",
  "input_health": "正常",
  "start_update": "data_update.py",
  "open_control": true,
  "start_expand": "start_expand.py",
  "start_control": "expand_control.md"
}
```

| 字段 | 类型 | 规则 |
|------|------|------|
| `name` | string | 非空，建议与目录名一致 |
| `explain` | string | 非空的一句话职责说明 |
| `open_input` | bool | 是否允许状态数据进入 Prompt |
| `input_data` | string | 模块内 `.md` 文件名 |
| `input_health` | string | 只能是 `正常` 或 `异常` |
| `start_update` | string | 模块内 `.py` 采集入口 |
| `open_control` | bool | 是否开放操控能力 |
| `start_expand` | string | 模块内 `.py` 操控入口 |
| `start_control` | string | 模块内 `.md` 操作说明 |
| `recent_update` | string，可选 | 若存在必须为 `YYYY-MM-DD HH:MM:SS`；尚未运行时应省略，不能写空字符串 |

清单拒绝未知字段。即使关闭注入或操控，被引用文件仍应存在并保持合法，便于模块恢复启用。

## 数据层与操作层

`input_data.md` 保存最近一次采集结果。`data_update.py` 应实现确定性采集，原子更新 Markdown 和运行状态；不得把 API Key、Token 或密码写入结果。系统刷新器会在成功后把 `input_health` 设为 `正常` 并更新 `recent_update`；失败时设为 `异常` 并保留上一次成功时间。

调度器优先调用同步、零参数的 `update()`，不存在时回退到同步、零参数的 `main()`。成功可以返回 `None` 或 `{ "ok": true }`；返回 `False`、`{ "ok": false }`、失败状态或抛出异常均视为失败。更新入口必须在超时内自行结束，禁止无限循环、等待交互、常驻线程或自行启动守护进程。

`expand_control.md` 必须包含：

```markdown
## 注入层

说明模块是什么、何时使用、当前可见状态和操控入口。

## 操作层

逐项写清命令名、参数、返回值、失败格式、权限和副作用。
```

注入层可以进入主智能体 Prompt；操作层不会自动全部注入，应由智能体按需读取。Prompt 管线在每次模型请求时读取最新 `input_data.md`，cron 只负责刷新文件，不负责模板变量替换或直接注入。`start_expand.py` 推荐提供统一入口：

```python
def execute(command: str, params: dict | None = None) -> str:
    """返回 JSON 字符串；成功与失败都必须有明确状态。"""
```

不能用占位实现伪造外部操作成功。未接通真实服务时应返回 `not_implemented` 或明确错误。

## 创建流程

1. 判断需求确实需要外部连接或操控，而不是技能、感知或一次性脚本。
2. 确认 `user`/`shared` 作用域、英文名称和职责说明。
3. 确认注入数据、操控命令、参数、返回值及副作用。
4. 先列出现有模块查重，再获得用户最终确认。
5. 使用 `expand_creater action=create` 原子创建；全局层由管理员按相同合同手工创建。
6. 运行 `validate`，再在终端执行一次 `data_update.py` 和无副作用的健康检查；手动测试是前台执行，RuntimeHost 自动刷新仍采用隐藏的隔离子进程。

## 验收清单

- 清单字段完整且无未知字段，所有路径均留在模块目录。
- `input_data.md` 和 `expand_control.md` 为有效 Markdown。
- Python 文件语法通过，不含硬编码凭据。
- 数据采集失败会记录错误，不覆盖成虚假健康状态。
- 更新脚本提供零参数同步 `update()` 或 `main()`，可在配置超时内结束；返回 `False`、`{ "ok": false }` 或失败状态会被调度器判为失败。
- 采集和操控脚本不自行创建终端窗口、守护进程或无限循环；后台隐藏和进程回收由框架负责。
- 所有外部操作返回结构化结果，并清楚标明副作用。
- 共享/全局模块已按需加入用户白名单。

