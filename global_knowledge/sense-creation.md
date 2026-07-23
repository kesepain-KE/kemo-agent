# 感知模块创建文档

感知（Sense）是“外部或系统状态 → Markdown → System Prompt”的单向数据流。它适合 CPU、内存、磁盘、天气、网络或设备在线状态等定期采集，不允许借感知模块操控外部对象。

需要操控时使用拓展；需要独立推理时使用子智能体；只需提供说明时使用技能。

## 位置与发现

感知只有全局层：

```text
global_sense/<name>/
├── sense.json
├── sense.md
├── data_update.py
└── _last_run.json       # 可选运行状态
```

模块必须是 `global_sense/` 的直接子目录。名称匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`。根目录 `global_sense/register.py` 注册整个感知来源，用户配置 `perception.global_whitelist` 决定主智能体可见模块。

RuntimeHost 启用后台调度时，系统任务按 `task_cron_system.sense_update_rate` 扫描所有合法全局感知模块并执行其 `start_update`。每个入口都运行在独立、受超时约束的 Python 子进程中，不会把热插拔代码导入 Web/Runtime 主进程。

Windows 后台采集由框架使用隐藏窗口模式启动，Linux 使用普通无交互子进程。感知模块不需要也不应该自行设置 `CREATE_NO_WINDOW`、`CREATE_NEW_CONSOLE`、`pythonw` 或终端窗口参数。用户手动执行 `python data_update.py` 属于前台调试，是否显示当前终端由用户的启动方式决定。

## sense.json

清单必须且只能包含以下五个字段：

```json
{
  "name": "system_health",
  "data_md": "sense.md",
  "recent_update": "2026-07-23 13:00:00",
  "health": "正常",
  "start_update": "data_update.py"
}
```

| 字段 | 规则 |
|------|------|
| `name` | 非空模块名，建议与目录名一致 |
| `data_md` | 模块目录内的 `.md` 文件名，文件必须存在 |
| `recent_update` | 必须为 `YYYY-MM-DD HH:MM:SS`，不能留空 |
| `health` | 只能是 `正常` 或 `异常` |
| `start_update` | 模块目录内的 `.py` 文件名 |

路径不得是绝对路径、不得包含 `..`，模块目录和关键文件不得是符号链接或目录联接。

## sense.md

该文件全文可能进入所有启用此模块用户的 System Prompt，因此只保存必要、低敏感、可共享的数据。

推荐结构：

```markdown
# 系统健康感知

> 最近更新：2026-07-23 13:00:00
> 健康状态：正常

## 资源

| 指标 | 当前值 |
|------|--------|
| CPU 使用率 | 24% |
| 可用内存 | 12.4 GB |
```

不要写入密钥、Cookie、完整环境变量、私人文件正文或无界日志。应使用摘要值，并控制体积。

## data_update.py 合同

采集脚本应当：

1. 读取本机或外部只读数据。
2. 原子或完整地重写 `sense.md`。
3. 更新 `sense.json` 中的 `recent_update` 与 `health`，或由配套运行逻辑维护等价状态。
4. 失败时返回/抛出明确错误，不把旧数据伪装成刚刚更新。
5. 所有认证信息从环境变量读取，绝不硬编码。

系统更新器优先调用同步、零参数的 `update()`，不存在时回退到同步、零参数的 `main()`。成功可以返回 `None` 或 `{ "ok": true }`；返回 `False`、`{ "ok": false }`、`status=error/failed/failure` 或抛出异常均视为失败。不得在模块导入阶段执行采集或副作用。

每次执行受 `task_cron_system.module_update_timeout` 限制。更新函数必须自行结束，禁止无限循环、常驻线程、后台守护进程或等待交互输入。超时、崩溃和异常只会把当前模块标记为失败，不应影响其他模块和主智能体。

## 创建流程

1. 确认需求是只读采集，不含操控。
2. 确认名称、采集指标、刷新频率、数据可见范围。
3. 设计初始 `sense.md`，明确时间、健康状态和占位值。
4. 列出现有模块查重并获得最终确认。
5. 使用 `sense_creater action=create` 原子创建，随后 `validate`。
6. 在终端手动运行一次采集脚本，确认清单时间格式、Markdown 内容和失败行为；该手动测试是前台执行，不代表 RuntimeHost 后台会弹出窗口。

## 验收清单

- `sense.json` 恰好五字段且值合法。
- `sense.md` 与 `data_update.py` 均存在，路径没有越界。
- 采集脚本提供零参数同步 `update()` 或 `main()`，可重复运行、可在超时内结束，导入时无副作用。
- 脚本不自行创建终端窗口、守护进程或无限循环；后台隐藏由框架负责。
- 输出不含凭据、隐私或大段原始日志。
- 用户白名单与期望一致，Prompt 诊断显示模块已选中。
