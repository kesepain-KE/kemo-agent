# 用户包验收标准

```powershell
python -m tests.template_tests.user --target users/<name>
```

## 必须成立

- `user_config.json` 能与全局配置合并，`user_soul.md` 能进入真实 Prompt；
- 用户技能、拓展、子代理、知识、上传、下载、历史、头像和运行目录已经初始化；
- 参考用户模板的 `history/` 只保留目录标记，不预置 SQLite、旧索引或会话正文；
- 参考模板只保留 `improve/.gitkeep`，不预置二进制数据库、Markdown 碎片或 `data.json`；复制为真实用户后可初始化独立 `improve/memory.sqlite3`，并通过表结构完整性检查；
- 多用户发现和目录解析互相隔离；报告不回显 Provider 密钥。

## 外部条件

Provider Token 和模型可以由部署环境提供。通用验收只报告是否需要这些条件，不发送真实请求。
`task_cron` 与 `task_plan` 在用户骨架中只检查目录存在，它们的业务合同不属于本批基准。
