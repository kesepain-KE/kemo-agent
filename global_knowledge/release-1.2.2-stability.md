# 1.2.2 稳定性检查说明

这份文件只说明本次小版本检查的重点。用简单规则描述，方便普通模型和维护者执行。

## 检查范围

- 主要检查 2026 年 8 月 22 日至 2026 年 8 月 23 日的稳定性改动。
- 提交 `18bb0c5` 的 Git 时间是 **2026 年 8 月 23 日 01:56:44（+08:00）**。
- 当前日期是 **2026 年 8 月 23 日**，因此该提交属于本次检查范围，不再标记为未来时间戳。

## 当前 42 项未提交内容

2026 年 8 月 23 日检查工作区时共有 42 项未提交内容。它们不是 42 项独立功能，应按所有权分类：

| 类型 | 数量 | 处理规则 |
|---|---:|---|
| 更新器源码 | 15 | 属于本次单入口重构和稳定性加固，应与对应测试、文档一起提交 |
| Web 源码 | 5 | 属于任务计划会话隔离、浏览器输出脱敏和结束音效终态修正 |
| 测试 | 5 | 覆盖更新器安全合同、任务计划跨会话拒绝、前端 API 与结束音效判断 |
| 文档 | 7 | 用于同步真实实现、发布边界、目录导航和任务计划规则 |
| 忽略规则 | 1 | `.gitignore` 的更新器锁和维护标记规则 |
| 运行态数据 | 9 | 只反映本机调度、采集和激活状态，不应作为发布功能提交 |

9 项运行态数据是：

- `cron/task_cron_system/` 下 5 个系统任务 JSON；
- `global_expand/kemo_app/expand.json`；
- `global_expand/kemo_gateway_status/expand.json`；
- `global_expand/kemo_gateway_status/input_data.md`；
- `global_expand/kemo_graph/expand.json`。

发布时不能直接执行 `git add -A`。应先排除上述 9 项运行态变化，再暂存源码、测试、文档和
忽略规则。内置拓展的 `expand.json` 是模块静态清单，不能从仓库整体删除；发布副本应保持中性
初始状态，不携带部署机的 `recent_update`、本地激活状态或采集正文。

## 本版要保证的事情

1. 同一个用户的不同 `source + session_id` 对话互相隔离。任务计划的编辑、重试、回滚都要带
   `session_id`，不能用 A 对话修改 B 对话的计划。
2. 任务计划摘要、修订记录、错误和工具参数在返回网页前统一脱敏。明显的 Token、API Key、密码、
   Bearer 和私钥不能出现在网页、日志、历史或记忆中。
3. Provider 返回不完整工具 JSON 时只做有界重试，损坏调用不能执行。当前诊断合同仍可能保留最多
   500 字符的原始参数，所以这里不能放密钥；原始诊断字段的统一脱敏留作后续安全修正。
4. 运行结束音效只有真正成功结束才播放。暂停、取消、失败、受限停止和长任务中间 Run 都不播放。
   音效按用户保存，只在 Windows 桌面网页端启用；浏览器不支持播放时才使用受保护的终端降级。
5. 发送附件后立即清除发送框引用。失败或取消也不能继续复用已经提交的 `asset_id`。
6. 运行中引导上传的公开资产使用 `purpose=input`。
7. Cron 运行时间、最近运行时间、激活状态、拓展采集时间和本地凭据属于部署状态，不属于源码版本。
   发布前必须排除这些变化；更新器必须保留部署机状态，不能用源码中的时间覆盖它。
8. 根目录 `update.py` 只能是兼容入口。参数解析、远程源码、版本清单、板块调度、备份、锁、回滚、
   构建、依赖和迁移分别位于 `update/` 内部模块，不能重新堆回根入口。
9. 更新日志、板块警告和 `global_config.json` 差异在输出前必须脱敏；URL 用户信息、Token、API Key、
   Bearer、密码和私钥不能进入终端或 CI 日志。

## `run/` 导入迁移

旧的平铺路径已经删除。外部插件或脚本应改用下面的公开入口：

| 旧路径 | 新入口 |
|---|---|
| `run.agent_runner` | `run.agents` |
| `run.agent_service` | `run.agents` |
| `run.conversation_runtime` | `run.conversation` |
| `run.task_plan_store` | `run.tasks` |
| `run.task_plan_executor` | `run.tasks` |
| `run.memory_store` | `run.memory` |
| `run.prompt` | `run.config` |
| `run.runtime_host` | `run.scheduler` |

不要重新创建兼容平铺文件。需要新增能力时，先放入对应领域包，再从该领域的 `__init__.py` 暴露稳定名称。

## 发布前最小检查

```powershell
python .github/scripts/check_versions.py
python .github/scripts/check_repository_hygiene.py
python -m pytest -q tests/update_restart
python -m pytest -q tests --ignore=tests/template_tests
python -m pytest -q tests/template_tests
cd web/frontend
npm test
npm run build
```

如果某一步失败，不要提交新的版本号。先修复失败，再从头运行检查。`users/`、`runtime/`、本地
拓展配置、Cron 运行状态和上传文件需要单独备份；它们不能靠源码回滚恢复。依赖安装、数据库迁移
和内置拓展运行快照也不是完全可逆操作，生产更新前必须另做数据备份并停止正在运行的服务。
