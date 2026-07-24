---
type: component
project: kemo-agent
domain: cli
module: user-create
layer: L2
scope: project
status: active
summary: user_create.py — 用户创建与管理模块（CLI 交互 + 静默创建 + import API）
source: "cli/user-create.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, cli, 用户, 创建, 管理, provider, API配置]
---
# user_create.py — 用户创建与管理模块

`E:\code\kemo-agent\user_create.py`

## 概览

完整的用户创建与管理 CLI。支持交互式菜单和静默创建两种模式。可作为模块导入使用。

## CLI 模式

### 交互式菜单

```bash
python user_create.py
```

菜单选项：
- `[n]` 新建用户（引导填写 Provider 配置）
- `[e]` 编辑用户 API 配置
- `[d]` 删除用户（输入用户名确认）
- `[q]` 退出

### 静默创建

```bash
python user_create.py <用户名>
python user_create.py <用户名> --overwrite
```

## Import API

```python
from user_create import create_user, edit_user_api_config, delete_user, _list_users

# 创建
create_user("alice", root, interactive=True, provider_type="kemo", ...)

# 编辑
edit_user_api_config(root, "alice")

# 删除
delete_user(root, "alice", confirm=False)

# 列出
_list_users(root)
```

## create_user 流程

1. 校验用户名（`_validate_name`：不包含特殊字符，不等于 `_template` / `.` / `..`）
2. 检查目标目录是否存在（`--overwrite` 覆盖）
3. 从 `users/_template/` 复制骨架（`shutil.copytree`）
4. 后处理：
   - `_post_process_knowledge`：初始化 `knowledge/data_structure.md`
   - `_post_process_storage`：初始化 `improve/storage.json`
   - `_post_process_dirs`：补齐所有空子目录
5. 交互式引导 Provider 配置（可选）

## API 配置引导

- Provider 类型：kemo / chat
- API Base URL
- API Key（getpass 不回显）
- 模型名称

配置写入 `users/<name>/user_config.json` 的 `provider` 字段。

## 用户目录骨架

创建后自动补齐的子目录：

```
download, file_upload, expand, task_plan, task_cron, agents,
history, avatar, improve/{seven_days,one_month,half_year,permanent},
knowledge, user_skills/{agent_create,user_create}
```

## 相关笔记

- [[cli-总览]]
- [[setup-wizard]]
- [[template-总览]]
