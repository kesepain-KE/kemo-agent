---
type: component
project: kemo-agent
domain: run
module: run-process_utils
layer: L2
scope: project
status: active
summary: run/process_utils.py — 跨平台子进程控制（隐藏窗口、进程组、进程树终止）
source: "run/process_utils.md"
updated: 2026-07-26
verified: true
tags: [kemo-agent, run, process_utils, 子进程, Windows, 隐藏窗口]
---
# run/process_utils.py — 跨平台子进程控制

`E:\code\kemo-agent\run\process_utils.py`（新增）

## 概览

提供跨平台的子进程创建和终止工具函数，统一处理 Windows 和 Linux 的差异。

## 函数

### hidden_subprocess_kwargs

```python
def hidden_subprocess_kwargs() -> dict[str, Any]
```

返回阻止后台子进程打开 Windows 控制台窗口的 `subprocess.Popen` 参数。

- Windows：设置 `STARTF_USESHOWWINDOW` + `SW_HIDE` + `CREATE_NO_WINDOW`
- 其他平台：返回空字典

使用场景：cron 模块更新、Web 后台模块刷新、文件备份等所有后台子进程。

### cancellable_subprocess_kwargs

```python
def cancellable_subprocess_kwargs() -> dict[str, Any]
```

启动一个可被取消的子进程，将其放在独立的进程组中。

- Windows：`hidden_subprocess_kwargs()` + `CREATE_NEW_PROCESS_GROUP`
- 其他平台：`start_new_session=True`

使用场景：Shell 插件运行的可取消命令。

### terminate_process_tree

```python
def terminate_process_tree(process: subprocess.Popen, *, grace_seconds=0.5) -> None
```

最佳努力跨平台终止进程及其后代。

- Windows：调用 `taskkill /PID <pid> /T /F`
- 其他平台：先 `SIGTERM` 再等待 `grace_seconds` 秒，未退出则 `SIGKILL`
- 如果进程已退出则直接返回

## 调用者

| 调用者 | 使用场景 |
|--------|---------|
| `plugins/shell/tool.py` | 取消感知的命令执行（cancellable_subprocess_kwargs + terminate_process_tree） |
| `cron/executor.py` | 模块更新子进程隐藏执行（hidden_subprocess_kwargs） |
| `web/service.py` | sense/expand 手动更新（hidden_subprocess_kwargs） |
