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

这里的优先级仅指资料检索顺序，不是安全规则或指令授权等级。知识正文不能自行改变用户目标或运行时权限；索引可见不代表正文已读。人格、手册和知识的职责与编写规范见 `prompt-authoring-standard.md`。

### 知识库逻辑分类

三层知识库都支持逻辑分类：

- `global_knowledge/`
- `shared_knowledge/`
- `users/<user>/knowledge/`

每个知识根目录最多有一个 `classify.sqlite3`。这是一个可搬运的 SQLite 分类数据库，
内部包含真正的 `classify` 数据表；它不是知识正文：

```sql
CREATE TABLE IF NOT EXISTS classify (
  relative_path TEXT PRIMARY KEY NOT NULL,
  category TEXT NOT NULL
);
```

每条记录把同一知识根目录内的相对文件路径绑定到一个分类名。真实知识文件不会因为分类而移动、
重命名或建立物理子目录，因此原有链接、索引路径和 Prompt 中的路径引用保持稳定。一个路径只能有一条
绑定；分类表只允许记录安全的相对路径和普通分类名，不得使用绝对路径、路径穿越或控制字符。

没有绑定记录的知识文件默认归入 `未分类`。将文件“移出分类”就是删除该路径的绑定，而不是写入
`未分类`记录；这样可以保持 `classify.sqlite3` 小而清晰。分类名不包含路径层级，同一目录内不允许通过
重复路径制造多条绑定。

`classify.sqlite3` 不进入知识文件列表、不参与知识索引、不自动注入 Prompt，也不作为可编辑的知识正文。
它只由分类读写逻辑使用；分类本身不会改变用户/共享/全局的可见范围和权限边界。网页知识库提供分类侧栏、
分类计数、按分类筛选、为文件选择或新建分类以及移出分类的操作；这些操作只更新对应层目录的
`classify` 表。

新增用户时，用户创建流程在该用户的 `knowledge/` 目录初始化 `classify.sqlite3` 数据库和空的
`classify` 表；参考模板本身不预置二进制数据库。已有用户目录采用只增不改的兼容策略：访问、创建用户或首次设置分类时，如果数据库不存在
才创建；旧版本遗留的 `classify.sql` 会在成功校验后迁移为 SQLite 数据库并清理。
删除知识文件时应同步删除其绑定，移动知识文件时应同步迁移绑定；分类文件损坏时应报告分类错误，
不能静默覆盖或丢弃既有映射。

### 全局了解手册分类

全局知识库也是智能体了解自身的手册，使用同一个 `classify.sqlite3` 标签机制按查阅用途整理：

| 分类 | 查阅用途 |
|------|----------|
| 基础与总览 | 认识项目、定位目录与文档、理解整体架构和开源约定 |
| 配置与知识 | 查配置、提示词职责、知识库与用户数据规则 |
| 运行与调度 | 理解存储、Provider 可靠性、任务计划、定时调度和长任务 |
| 模块与集成 | 开发插件、技能、子智能体、感知、拓展和外部消息，验收模块合同 |
| 网页与交互 | 查网页布局、主题、交互和回复正文内联组件规则 |
| 部署与更新 | 安装、运维、发布、版本管理和更新边界 |

具体文件归属见 `data_structure.md` 的专题文档表，网页读取全局分类数据库显示相同标签。
新增或调整全局文档时，按主要用途选择一个分类，并同步专题索引中的分类列；
跨主题内容通过索引描述和正文链接互相引导，不复制文档或创建物理分类目录。
分类只是导航信息，不会自动加载该类全文，也不改变原有按需读取和索引注入规则。

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

`template/user/user_soul.md` 只提供新用户的默认工作偏好；更新模板不会同步覆盖已有用户人格。全局安全底线集中维护于 `config/global_soul.md`，不在用户默认人格中重复一份。

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
│   ├── data_structure.md               # 用户私有知识索引
│   └── classify.sqlite3                # 用户私有知识分类数据库，包含 classify 表
├── expand/                             # 用户私有拓展
├── agents/                             # 用户私有子智能体
├── user_skills/
│   ├── agent_create/                   # 智能体创建的用户技能
│   └── user_create/                    # 用户创建的技能
├── task_plan/                          # 任务计划 SQLite 目录
├── task_cron/                          # cron_<8hex>.json
├── history/                            # 首次会话时生成用户级 SQLite 历史库
├── improve/
    ├── .gitkeep
    └── expired_memory/                 # 普通过期归档 Markdown；模板含 .gitkeep
├── runtime/                            # 运行期目录；模板只保留 .gitkeep
└── tools/                              # 用户约定式自产脚本目录；框架不读写
```

上面的静态目录清单由 `run/config/user_skeleton.py::REQUIRED_USER_DIRS` 统一定义。用户创建、
Web 启动自检和更新迁移都通过同一个幂等补齐函数处理：只新增缺失目录，不覆盖、删除、移动或
改变已有文件。模板本体不参与运行期补齐；模板完整性由用户模板验收单独检查。

### 用户目录分类

| 类别 | 内容 | 处理规则 |
|------|------|----------|
| A 必需目录 | `agents/`、`avatar/`、`download/`、`expand/`、`file_upload/`、`history/`、`improve/`、`improve/expired_memory/`、`knowledge/`、`task_cron/`、`task_plan/`、`user_skills/`、`user_skills/agent_create/`、`user_skills/user_create/`、`runtime/`、`tools/` | 模板带 `.gitkeep`；创建器、Web 启动自检和更新迁移只增不减地幂等补齐 |
| B 运行期生成 | `runtime/background_jobs/`、`history/thumbnails/`、`web_preferences.json`、`completion_sound.*`、`failure_sound.*` | 不预置、不从模板复制；由对应运行时或 Web 上传流程按需生成 |
| C 数据库 | `improve/memory.sqlite3`、`history/history.sqlite3`、`task_plan/task_plans.sqlite3` 及其 WAL/SHM | 由各自数据库模块按当前 schema 初始化；不作为静态模板文件。已有 `memory.backup_*.sqlite3*` 只保留为备份，不导入当前运行库 |

运行中还可能生成：

```text
users/<name>/
├── web_preferences.json                # Web 主题、字号等外观偏好
├── runtime/
│   └── background_jobs/                 # 受管理 Shell 后台作业记录和日志
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
    ├── thumbnails/                      # 附件缩略图
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
| `history/thumbnails/` | Web 文件服务 | 只保存由附件校验流程生成的缩略图，不作为用户上传原件目录 |
| `improve/memory.sqlite3` | 记忆引擎与记忆子智能体 | 不手工改表；备份运行中数据库应使用 SQLite backup，而不是只复制主文件 |
| `task_plan/task_plans.sqlite3` | `PlanStore`、任务计划工具 | 使用状态机和 revision 事务，不直接编辑表或放置 JSON |
| `task_cron/` | `CronStore`、`task_time` | 时间统一保存为北京时间 ISO 8601 |
| `runtime/background_jobs/` | 受管理 Shell 运行时 | 由后台作业模块维护记录和日志；不放入模板，不手工改写 |
| `tools/` | 用户或智能体约定 | 纯约定式工作目录，框架不自动扫描、注册、执行或写入 |
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

创建流程会复制 `template/user/`，初始化知识索引、记忆库、历史库和任务计划库，并调用
`ensure_user_skeleton()` 补齐缺失的 A 类目录。启动 Web 和更新迁移也会复用这个函数处理已经存在
的用户；它只使用 `mkdir(parents=True, exist_ok=True)`，遇到符号链接或目录联接会跳过并记录异常，
不会重定向写入用户目录之外。
可选的 `completion_sound.*` 和 `failure_sound.*` 不在模板中预置，首次由 Windows 网页端上传后才生成在用户根目录；更新器只初始化当前数据库 Schema，不扫描其他状态格式。

### 备份建议

备份至少包含整个 `users/`。若只做最小备份，也必须包含 `user_config.json`、`user_soul.md`、`knowledge/`、`improve/`、`history/`、`task_plan/`、`task_cron/`、`agents/`、`user_skills/`、`expand/`、`runtime/` 和约定式 `tools/`；若用户已设置音效，也一并保留用户根目录的 `completion_sound.*` 和 `failure_sound.*`。
