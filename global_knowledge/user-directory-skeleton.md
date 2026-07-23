# 用户文件夹骨架

每个用户的数据位于 `users/<name>/`。创建用户应使用 `user_create.py` 或 Web 用户管理，由 `template/user/` 复制并补齐目录；不要从其他真实用户目录复制，以免带入历史、记忆或凭据。

## 基础骨架

```text
users/<name>/
├── user_config.json                    # Provider、白名单与用户级配置
├── user_soul.md                        # 用户人格与工作偏好
├── memory_temporary_important.md       # 临时重要记忆，子智能体维护
├── avatar/                             # 用户头像
├── file_upload/                        # 用户上传文件
├── download/                           # 智能体生成并交付的文件
├── knowledge/
│   └── data_structure.md               # 用户私有知识索引
├── expand/                             # 用户私有拓展
├── agents/                             # 用户私有子智能体
├── user_skills/
│   ├── agent_create/                   # 智能体创建的用户技能
│   └── user_create/                    # 用户创建的技能
├── task_plan/                          # plan_<8hex>.json
├── task_cron/                          # cron_<8hex>.json
├── history/                            # 完整历史与当前 Provider 工作区
└── improve/
    ├── storage.json                    # 记忆存储 schema 标记
    ├── permanent/                      # 永久记忆
    ├── half_year/
    │   └── data.json
    ├── one_month/
    │   └── data.json
    └── seven_days/
        └── data.json
```

运行中还可能生成：

```text
users/<name>/
├── message_state/processed.json        # 外部消息幂等状态
├── web_preferences.json                # Web 主题、字号等外观偏好
└── history/
    ├── data.json                        # 会话索引
    ├── conv_<uuid>/                     # 完整、不可裁剪的归档会话
    │   ├── data.json
    │   ├── text.json
    │   ├── think.json
    │   ├── tool.json
    │   └── items.json
    └── temp/<conversation-id>/          # 可压缩的 Provider 工作区，同样为五文件
```

## 目录所有权

| 路径 | 主要写入方 | 注意事项 |
|------|------------|----------|
| `user_config.json` | 用户、配置界面 | 密钥可放用户配置，但不得写入知识或记忆 |
| `user_soul.md` | 用户、人格编辑界面 | 只描述偏好，不能覆盖安全底线 |
| `memory_temporary_important.md` | `memory_temporary_important` 子智能体 | 单文件重要记忆，不应删除 |
| `knowledge/` | 用户或明确授权的智能体 | 私有知识默认写这里，并同步索引 |
| `file_upload/` | Web/外部入口 | 同名上传自动防覆盖；不能当临时目录 |
| `download/` | 智能体工具 | 只放需要交给用户的最终产物 |
| `history/` | 历史引擎 | 不手工改五文件或会话索引 |
| `improve/` | 记忆引擎与记忆子智能体 | 不手工改权重、游标和 schema 文件 |
| `task_plan/` | `PlanStore`、任务计划工具 | 使用状态机，不直接覆盖 JSON |
| `task_cron/` | `CronStore`、`task_time` | 时间统一保存为北京时间 ISO 8601 |
| `agents/` | 子智能体创建器或可信管理员 | 自定义 `executor.py` 会在主进程执行，只安装可信代码 |

## 用户名规则

- 长度最多 64 个字符。
- 不能包含 Windows 非法路径字符、控制字符或路径分隔符。
- 不能以 `.` 开头，不能是 `_template`、`.` 或 `..`。
- 用户名决定目录和多处运行时标识，创建后不建议直接重命名目录。

## 创建与迁移

```powershell
python user_create.py
```

创建流程会复制 `template/user/`，初始化知识索引、记忆存储标记并补齐所有空目录。更新器会尝试为现有用户补齐新骨架，但用户历史、记忆、上传、下载和私有资源属于运行数据，不应被框架更新覆盖。

## 备份建议

备份至少包含整个 `users/`。若只做最小备份，也必须包含 `user_config.json`、`user_soul.md`、`knowledge/`、`improve/`、`history/`、`task_plan/`、`task_cron/`、`agents/`、`user_skills/` 和 `expand/`。

