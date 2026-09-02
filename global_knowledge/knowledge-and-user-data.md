# 知识库与用户数据功能说明

本文集中说明三层知识库、知识索引以及用户目录骨架和数据所有权。知识内容和用户文件布局仍按各自章节的规则执行。

## 知识库创建文档

kemo-agent 使用三层 Markdown 知识库。知识库保存可长期复用的事实、说明、规范和项目文档；对话偏好与个人事实应进入记忆系统，而不是随意写入全局知识。

### 三层知识库

| 层级 | 路径 | 可见范围 | 默认优先级 |
|------|------|----------|------------|
| 用户 | `users/<user>/knowledge/` | 当前用户私有 | 最高 |
| 共享 | `shared_knowledge/` | 多用户共享 | 中 |
| 全局 | `global_knowledge/` | 框架级公共知识 | 最低 |

用户配置 `knowledge.use_shared` 和 `knowledge.use_global` 控制是否启用共享、全局索引；用户知识始终属于当前用户。检索和注入顺序为用户 → 共享 → 全局。

### 索引与正文

运行时只自动注入以下名称的索引文件，且会递归发现：

- `data_structure.md`
- `index.md`
- `索引.md`
- `目录.md`

普通正文不会自动整篇进入 System Prompt。索引应告诉智能体“有哪些文件、各自解决什么问题、何时读取”，需要详情时再读取正文。

推荐结构：

```text
knowledge-root/
├── data_structure.md
├── architecture/
│   ├── index.md
│   ├── runtime.md
│   └── storage.md
└── operations/
    └── troubleshooting.md
```

### 索引模板

```markdown
# 项目知识索引

## 文件清单

| 文件 | 内容 | 何时读取 |
|------|------|----------|
| `architecture/runtime.md` | 运行时组件和生命周期 | 修改启动、调度或会话逻辑时 |
| `operations/troubleshooting.md` | 常见故障定位 | 启动或连接异常时 |

## 维护规则

1. 文件增删改移后同步本索引。
2. `.bak`、日志、缓存和临时产物不进入索引。
```

### 创建流程

1. 判断内容应该属于用户、共享还是全局层；不确定时优先更小的可见范围。
2. 选择稳定、描述性的 `.md` 文件名，可按主题建立子目录。
3. 正文使用明确标题、事实来源、适用范围和更新时间。
4. 更新最近一层索引；新增专题索引时也要让上级索引能够找到它。
5. 检查内容是否含凭据、隐私、运行日志或短期状态。
6. 通过 Web 知识库或 Prompt 诊断确认索引可见。

### 内容边界

适合知识库：

- 项目介绍、目录结构、配置参考和开发规范。
- 稳定的业务规则、术语表和操作手册。
- 经确认的设计决策及其适用范围。

不适合知识库：

- API Key、密码、Token、Cookie、私钥。
- 一次性运行输出、调试日志、缓存和构建产物。
- 仅对某个用户有效却写入共享或全局层的私人信息。
- 需要自动过期、加权或晋升的个人记忆。

### 质量要求

- 一个文档聚焦一个主题，标题与索引描述一致。
- 明确区分事实、建议、限制和未实现能力。
- 路径使用项目相对路径，避免写死个人电脑绝对路径。
- 代码变化导致规则失效时，同步修改正文和索引。
- 不把索引写成全文副本；索引应短而可导航。

---

## 用户文件夹骨架

每个用户的数据位于 `users/<name>/`。创建用户应使用 `user_create.py` 或 Web 用户管理，由 `template/user/` 复制并补齐目录；不要从其他真实用户目录复制，以免带入历史、记忆或凭据。

### 基础骨架

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

### 目录所有权

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

### 用户名规则

- 长度最多 64 个字符。
- 不能包含 Windows 非法路径字符、控制字符或路径分隔符。
- 不能以 `.` 开头，不能是 `_template`、`.` 或 `..`。
- 用户名决定目录和多处运行时标识，创建后不建议直接重命名目录。

### 创建与迁移

```powershell
python user_create.py
```

创建流程会复制 `template/user/`，初始化知识索引、记忆库、历史库和任务计划库，并补齐其他空目录。
可选的 `completion_sound.*` 和 `failure_sound.*` 不在模板中预置，首次由 Windows 网页端上传后才生成在用户根目录；更新器只初始化当前数据库 Schema，不扫描其他状态格式。

### 备份建议

备份至少包含整个 `users/`。若只做最小备份，也必须包含 `user_config.json`、`user_soul.md`、`knowledge/`、`improve/`、`history/`、`task_plan/`、`task_cron/`、`agents/`、`user_skills/` 和 `expand/`；若用户已设置音效，也一并保留用户根目录的 `completion_sound.*` 和 `failure_sound.*`。

