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
├── task_plan/                          # 任务计划 SQLite 目录
├── task_cron/                          # cron_<8hex>.json
├── history/                            # 首次会话时生成用户级 SQLite 历史库
└── improve/                            # 模板中只保留 .gitkeep
```

运行中还可能生成：

```text
users/<name>/
├── web_preferences.json                # Web 主题、字号等外观偏好
├── task_plan/
│   ├── task_plans.sqlite3              # 计划、步骤、依赖的唯一权威数据库
│   ├── task_plans.sqlite3-wal          # 运行时 WAL
│   └── task_plans.sqlite3-shm          # 运行时共享内存索引
├── improve/
│   ├── memory.sqlite3                  # 四档记忆及生命周期的唯一权威数据库
│   ├── memory.sqlite3-wal              # 运行时 WAL，正常关闭后自动合并
│   └── memory.sqlite3-shm              # SQLite 共享内存索引，运行时自动生成
├── completion_sound.{mp3,wav,ogg,webm}  # 可选；Windows 网页端成功结束音效，未设置时不存在
├── failure_sound.{mp3,wav,ogg,webm}     # 可选；Windows 网页端最终失败音效，未设置时不存在
└── history/
    ├── history.sqlite3                  # 会话、正文、状态和检索的权威数据库
    ├── history.sqlite3-wal              # 运行时 WAL，正常关闭后自动合并
    └── history.sqlite3-shm              # SQLite 共享内存索引，运行时自动生成
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
| `history/history.sqlite3` | 历史引擎 | 不手工修改表；备份运行中数据库时使用 SQLite backup，而不是只复制主文件 |
| `improve/memory.sqlite3` | 记忆引擎与记忆子智能体 | 不手工改表；备份运行中数据库应使用 SQLite backup，而不是只复制主文件 |
| `task_plan/task_plans.sqlite3` | `PlanStore`、任务计划工具 | 使用状态机和 revision 事务，不直接编辑表或放置 JSON |
| `task_cron/` | `CronStore`、`task_time` | 时间统一保存为北京时间 ISO 8601 |
| `agents/` | 子智能体创建器或可信管理员 | 自定义 `executor.py` 会在主进程执行，只安装可信代码 |
| `completion_sound.*` | Windows 网页端成功结束音效 API | 上传后写入用户根目录；不进入 `download/`、`file_upload/` 或文件列表；未设置时不存在 |
| `failure_sound.*` | Windows 网页端最终失败音效 API | 上传后写入用户根目录；不进入 `download/`、`file_upload/` 或文件列表；未设置时不存在 |

## 用户名规则

- 长度最多 64 个字符。
- 不能包含 Windows 非法路径字符、控制字符或路径分隔符。
- 不能以 `.` 开头，不能是 `_template`、`.` 或 `..`。
- 用户名决定目录和多处运行时标识，创建后不建议直接重命名目录。

## 创建与迁移

```powershell
python user_create.py
```

创建流程会复制 `template/user/`，初始化知识索引、记忆库、历史库和任务计划库，并补齐其他空目录。
可选的 `completion_sound.*` 和 `failure_sound.*` 不在模板中预置，首次由 Windows 网页端上传后才生成在用户根目录；更新器只初始化当前数据库 Schema，不扫描其他状态格式。

## 备份建议

备份至少包含整个 `users/`。若只做最小备份，也必须包含 `user_config.json`、`user_soul.md`、`knowledge/`、`improve/`、`history/`、`task_plan/`、`task_cron/`、`agents/`、`user_skills/` 和 `expand/`；若用户已设置音效，也一并保留用户根目录的 `completion_sound.*` 和 `failure_sound.*`。
