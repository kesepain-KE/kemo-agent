# 知识库与用户数据功能说明

本文说明三层知识库、按真实路径组织的文档，以及用户目录骨架和数据所有权。

## 三层知识库

| 层级 | 路径 | 范围 |
|------|------|------|
| 用户 | `users/<user>/knowledge/` | 当前用户私有 |
| 共享 | `knowledge/share/` | 多用户共享 |
| 全局 | `knowledge/global/` | 框架级公共知识 |

知识库保存可长期复用的事实、说明、规范和项目文档；对话偏好与个人事实进入记忆系统。
`knowledge.use_shared` 与 `knowledge.use_global` 控制子智能体可声明的知识范围，
不触发知识目录扫描或正文注入。知识正文由获准工具按需读取；知识文件本身不能改变用户目标或运行时权限。
人格、手册、知识与技能的职责见 `knowledge/global/engineering/prompt-authoring-standard.md`。

网页「全部」的根目录固定显示用户、共享、全局三个入口，分别映射上表中的真实路径。
子文件夹按硬盘逐层展开，空文件夹也可见；目录层级就是硬盘路径层级，不根据标签生成虚拟目录，
也不自动搬动现有文档。知识库不使用分类标签、分类筛选或归类操作。

全局专题按实际文件路径分为六类：

| 子目录 | 内容 |
|--------|------|
| `knowledge/global/overview/` | 项目介绍、架构与许可 |
| `knowledge/global/engineering/` | 配置、开发规范、知识与用户数据 |
| `knowledge/global/runtime/` | 会话、记忆、任务、Provider 与调度运行机制 |
| `knowledge/global/modules/` | 插件、技能、子智能体、拓展与感知 |
| `knowledge/global/web/` | 网页约定与正文组件协议 |
| `knowledge/global/release/` | 部署、发布、更新与恢复 |

根目录 `agents.md` 的「全局文档入口」列出分类及专题路径，正文仍按任务需要读取。
用户、共享、全局三个知识层均不再使用知识索引文件；文件和文件夹自身的路径就是导航，
不需要 `index.md` 或 `data_structure.md`。用户模板只保留知识目录占位，新用户创建和目录修复不生成索引。

## 更新与回滚行为

| 目录 | 框架更新 | 源码回滚 |
|------|----------|----------|
| `users/<user>/knowledge/` | 保留本机内容 | 不从源码快照恢复 |
| `knowledge/share/` | 保留整个目录，同名文件也不覆盖 | 不恢复旧内容、不删除新增文件，也不重建已删除文件 |
| `knowledge/global/` | 按发布内容完整同步，保留图谱存储及既有分类数据 | 按源码快照恢复框架文档 |

共享层属于用户数据，不随框架分发默认文档；源码更新不扫描或同步它，发布打包也不携带本机共享资料。
独立部署器更新时会解除安装清单对共享文档的管理，保留文件原样。发布包仅携带空的
`knowledge/share/.gitkeep`：缺失时补建目录或占位文件，已有文件不覆盖，也不纳入受管清单。
不接受旧分类数据库种子。源码备份排除整个共享层，恢复快照时也忽略其中的共享内容。
`users/` 和 `knowledge/share/` 都需要独立的数据备份，不能依赖框架源码回滚恢复。

## Web 知识库操作

目录树点击目录整行、名称或箭头均可展开／折叠；文件和文件夹支持重命名、删除、复制、剪切、
粘贴、复制相对/绝对路径、新建文件和文件夹。复制和剪切记录应用内文件引用，进入三层中的
目标目录后粘贴；复制遇到重名自动生成副本，移动遇到重名拒绝覆盖。
复制相对路径以项目根目录为基准，复制绝对路径使用服务器上的完整硬盘路径。
三层根入口支持粘贴、路径复制和新建操作，不能重命名、删除或整体搬走。
路径穿越、链接、知识图谱运行目录和隐藏内部数据不进入浏览与递归操作。
删除文件夹须确认会删除整个目录及其内容；既有部署管理员认证与当前用户范围不变。

文件列表显示所有可见的普通文件，不按文本后缀过滤。图片、音视频、PDF、Office 文档及表格
复用文件空间的预览器与本机转换能力，支持放大和下载原文件；未知格式仍可浏览、下载及管理。
导入按原始字节写入当前目录，单文件上限 80 MiB，覆盖已有文件须确认。
点击文件默认预览，仅在用户主动切换编辑时读取编辑内容。网页仅开放 `.md/.txt` 编辑，
其他格式的编辑按钮禁用；后端已有 JSON 读写与格式校验接口保持兼容。
预览只按需读取当前选中文件，不将多媒体资料自动注入提示词。

知识正文不整库拼入 System Prompt。提示词只包含常驻个人智能操作手册及当轮所需的能力信息；
正文在明确任务需要时通过获准工具读取。

知识库不再提供分类标签、分类筛选或归类操作，新用户不再创建分类数据库。
旧的 `classify.sqlite3` / `classify.sql` 仅保留原文件，不再读取、写入、迁移或随新发布包分发，
也不进入知识文件列表和 Prompt；分类数据损坏不会阻塞正文编辑或文件移动。

## 知识内容规范

创建文档时先判断用户、共享或全局范围；不确定时选更小的可见范围。使用描述清楚的 `.md` 文件名，
按主题建立实际子目录，正文注明适用范围和事实依据；路径调整时同步更新 `agents.md` 的专题入口。

适合保存项目介绍、配置参考、开发规范、稳定业务规则、术语和已确认的设计决策。
不保存凭据、一次性运行输出、调试日志、缓存、构建产物、其他用户的私人信息或应由记忆系统管理的内容。
明确区分事实、建议、限制和未实现能力，并使用项目相对路径。

---

## 用户文件夹骨架

每个用户的数据位于 `users/<name>/`。创建用户应使用 `user_create.py` 或 Web 用户管理，由 `template/user/` 复制并补齐目录；不要从其他真实用户目录复制，以免带入历史、记忆或凭据。

`template/user/user_soul.md` 只提供新用户的默认工作偏好；更新模板不会同步覆盖已有用户人格。全局安全底线集中维护于 `config/global_soul.md`，不在用户默认人格中重复一份。

### 基础骨架

```text
users/<name>/
├── user_config.yaml                    # Provider、白名单与用户级配置
├── user_soul.md                        # 用户人格与工作偏好
├── background.webp                     # 新用户复制的默认网页背景；可换图片/视频，默认不启用
├── memory_temporary_important.md       # 临时重要记忆，子智能体维护
├── avatar/                             # 用户头像
├── file_upload/                        # 用户上传文件
├── download/                           # 智能体生成并交付的文件
├── knowledge/                           # 用户私有知识，按真实子路径组织
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
| C 数据库 | `history/cards.sqlite3`、`improve/memory.sqlite3`、`task_plan/task_plans.sqlite3`、`runtime/agent_tasks.sqlite3` 及其 WAL/SHM | 由各自数据库模块按当前 schema 初始化；不作为静态模板文件。旧 history/window 文件和 `memory.backup_*.sqlite3*` 保留原样，不导入当前运行库 |
| D 可选静态媒体 | 根目录 `background.{jpg,jpeg,png,webp,mp4,webm}`；模板为 `background.webp` | 新用户从模板复制轻量默认背景、默认不启用；已有用户修复/升级不补入、不覆盖。用户可通过网页上传替换或手工放入，读取不修改文件 |

运行中还可能生成：

```text
users/<name>/
├── web_preferences.json                # Web 主题、字号等外观偏好
├── runtime/
│   ├── agent_tasks.sqlite3             # 业务父子任务账本，按需创建
│   └── background_jobs/                # 受管理 Shell 后台作业记录和日志
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
    ├── cards.sqlite3                  # CardStream RAW/VIEW、会话、整合、工具执行及维护状态
    ├── cards.sqlite3-wal              # 运行时 WAL
    ├── cards.sqlite3-shm              # 运行时共享内存索引
    └── thumbnails/                    # 附件缩略图；用户既有历史文件保持原样
```

### 目录所有权

| 路径 | 主要写入方 | 注意事项 |
|------|------------|----------|
| `user_config.yaml` | 用户、配置界面 | 密钥可放用户配置，但不得写入知识或记忆 |
| `user_soul.md` | 用户、人格编辑界面 | 只描述偏好，不能覆盖安全底线 |
| `memory_temporary_important.md` | `memory_temporary_important` 子智能体 | 单文件重要记忆，不应删除 |
| `knowledge/` | 用户或明确授权的智能体 | 私有知识默认写这里，按主题使用真实子目录 |
| `file_upload/` | Web/外部入口 | 同名上传自动防覆盖；不能当临时目录 |
| `download/` | 智能体工具 | 只放需要交给用户的最终产物 |
| `history/cards.sqlite3` | CardStream 与会话/工具运行时 | RAW 只追加、VIEW 压缩原子提交；运行中备份使用 SQLite backup，不只复制主文件 |
| `history/` 中的旧历史 | 用户既有数据 | 当前引擎不读取、迁移、覆写或删除 |
| `history/thumbnails/` | Web 文件服务 | 只保存由附件校验流程生成的缩略图，不作为用户上传原件目录 |
| `improve/memory.sqlite3` | 记忆引擎与记忆子智能体 | 不手工改表；备份运行中数据库应使用 SQLite backup，而不是只复制主文件 |
| `task_plan/task_plans.sqlite3` | `PlanStore`、任务计划工具 | 使用状态机和 revision 事务，不直接编辑表或放置 JSON |
| `task_cron/` | `CronStore`、`task_time` | 时间统一保存为北京时间 ISO 8601 |
| `runtime/background_jobs/` | 受管理 Shell 运行时 | 由后台作业模块维护记录和日志；不放入模板，不手工改写 |
| `tools/` | 用户或智能体约定 | 纯约定式工作目录，框架不自动扫描、注册、执行或写入 |
| `agents/` | 子智能体创建器或可信管理员 | 自定义 `executor.py` 会在主进程执行，只安装可信代码 |
| `completion_sound.*` | Windows 网页端成功结束音效 API | 上传后写入用户根目录；不进入 `download/`、`file_upload/` 或文件列表；未设置时不存在 |
| `failure_sound.*` | Windows 网页端最终失败音效 API | 上传后写入用户根目录；不进入 `download/`、`file_upload/` 或文件列表；未设置时不存在 |

历史、任务计划、日志和记忆数据库在迁移提交后才切换 WAL。并发写者可能在此时取得写锁，
日志模式切换通过 `run/infra/sqlite_migrations.py` 在 5 秒期限内重试这一锁冲突；
超时和其他数据库错误仍向调用方报告，不重放迁移或业务写入。

### 用户名规则

- 长度最多 64 个字符。
- 不能包含 Windows 非法路径字符、控制字符或路径分隔符。
- 不能以 `.` 开头，不能是 `_template`、`.` 或 `..`。
- 用户名决定目录和多处运行时标识，创建后不建议直接重命名目录。

### 创建与迁移

```powershell
python user_create.py
```

创建流程会复制 `template/user/`，确保知识目录存在，初始化记忆库、CardStream 库和任务计划库，并调用
`ensure_user_skeleton()` 补齐缺失的 A 类目录。启动 Web 和更新迁移也会复用这个函数处理已经存在
的用户；它只使用 `mkdir(parents=True, exist_ok=True)`，遇到符号链接或目录联接会跳过并记录异常，
不会重定向写入用户目录之外。
可选的 `completion_sound.*` 和 `failure_sound.*` 不在模板中预置，首次由 Windows 网页端上传后才生成在用户根目录；更新器只初始化当前数据库 Schema，不扫描其他状态格式。

### 备份建议

网页背景 `background.*` 不进入上传/下载文件列表、知识库或模型输入。显示配置归
`user_config.yaml → web_ui.background`；格式、安全读取和视频降级见
`knowledge/global/web/frontend-conventions.md` 的“用户网页背景”。默认背景为项目原创程序绘制的静态 WebP，
不依赖远程素材或外部图片版权；模板验收检查默认文件可正常解码。

备份至少包含整个 `users/`。若只做最小备份，也必须包含 `user_config.yaml`、`user_soul.md`、`history/cards.sqlite3` 及其 WAL/SHM、`knowledge/`、`improve/`、`history/`、`task_plan/`、`task_cron/`、`agents/`、`user_skills/`、`expand/`、`runtime/` 和约定式 `tools/`；运行中的数据库须使用 SQLite backup。若用户已设置音效或背景，也一并保留用户根目录的 `completion_sound.*`、`failure_sound.*` 和 `background.*`。
