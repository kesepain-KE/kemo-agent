# 用户包验收标准

```powershell
python -m tests.template_tests.user --target users/<name>
```

## 必须成立

- `user_config.json` 能与全局配置合并，`user_soul.md` 能进入真实 Prompt；
- 用户技能、拓展、子代理、知识、上传、下载、历史、头像和运行目录已经初始化；
- `seven_days`、`one_month`、`half_year` 三层索引使用当前记忆 Schema，永久记忆目录存在；
- 多用户发现和目录解析互相隔离；报告不回显 Provider 密钥。

## 外部条件

Provider Token 和模型可以由部署环境提供。通用验收只报告是否需要这些条件，不发送真实请求。
`task_cron` 与 `task_plan` 在用户骨架中只检查目录存在，它们的业务合同不属于本批基准。
