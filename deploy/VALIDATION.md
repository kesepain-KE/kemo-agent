# 部署模块验证记录

## 1.5.1 网页长文滚动与文档补充验收（2026-10-19）

版本仍为 1.5.1，未改动 `version.json`、`cli.py`、前端 `package.json` 和锁文件，
四份文件的 SHA-256 与本次开始前一致。中英文 README 和项目介绍在原 1.5.1 说明内补充
引导／任务计划内部滚动，不新增版本或历史更新章节；详细合同仍由全局知识库维护。

- 正式前端 68 个文件、605 项全部通过；新增覆盖长文换行与末尾保留、引导归档、
  失败取消／重试、计划折叠恢复和批准操作，并核对实际应用入口加载生产样式。
- TypeScript 构建类型检查通过。前一轮网页修复的 Vite 生产构建及 40 组浏览器验收证据
  保存在 `web/frontend/output/overflow-qa/`，本次未修改生产前端实现。
- 正式部署文档合同与完整 `test_kemo` 合计 113 项、18 项子测试通过，其中开发期套件
  107 项、正式文档合同 6 项；开发期分发验收包含 3 项。
- 新分发检查实际创建当前工作树的 Release ZIP，从包内 `index.html` 追踪入口及动态资源
  所引用的 CSS，确认引导与辅助区滚动、计划标题／描述限高、包内最新项目介绍，
  并拒绝分发临时浏览器验收页。原有隔离安装、npm tarball 和本机渠道检查继续通过。
- 版本一致性检查通过，Kemo 2.0 与配套网关兼容基线保持原样。

首轮文档检查时修改了项目介绍，触发文档正文与已打包快照不一致；固定文档后完整重跑上述
Python 检查通过。原始失败与最终日志分别保存在
`tmp/ui-doc-sync-20261019/python-tests.log`、`python-tests-final.log`；
前端与类型日志位于 `web/frontend/output/overflow-qa/docs-sync-*.log`。
这份记录是同版本修复的补充验收，不把 10 月 18 日的完整发布前报告改写成覆盖新工作树，
不表示已重新执行全部 11 阶段或完成远程 Release/npm/GHCR 发布。

## 1.5.1 本地发布前验收（2026-10-18）

当前源码与四个组件版本为 1.5.1，配套网关基线保持 1.0.0，Kemo 2.0 线路协议不变。
本次执行完整 11 阶段检查，并在隔离类型检查环境后对类型门禁和开发期系统验收补测：

- 版本一致性、仓库卫生、Python 编译和补丁格式检查通过。
- 开发期系统验收最终 104 项通过，其中发布编排失败/超时/构建依赖与检查环境回归 14 项。
- 正式后端 1644 项通过、67 项跳过、370 项子测试通过，保留 3 条 Pydantic 序列化警告。
  首轮 Windows 后台超时测试在临时目录清理时遇到进程退出竞态，补上命令与 Worker 身份退出等待后，
  连续三次定向测试及随后全量后端均通过；没有放宽超时断言或修改 Shell 业务实现。
- 模板合同 12 项、前端 600 项通过；TypeScript 与 Vite 生产构建成功，仍有资源块体积提示。
- 固定 Pyright 1.1.414 最终 0 错误、0 警告。初次完整运行的类型检查误继承测试专用
  PYTHONPATH，导致同名拓展入口冲突；已隔离测试路径并补齐独立检查作用域，复测通过。
- 真实分发验收 2 项通过：使用本次 dist 生成 Release/npm/Docker 构建上下文，
  核对前端字节与 ZIP 摘要，生成并检查真实 npm tarball，执行隔离 files-only 安装和 check，
  再从安装树通过隔离 Python 检查用户骨架、知识分类、通用 Chat 状态续接及 DeepSeek 分支。
- 前端锁文件只推进项目自身版本，第三方依赖记录保持原样；56 个 Kemo/DeepSeek 受保护文件
  的 SHA-256 与基线全部一致。没有重启正在运行的后端。

本机证据在 `tmp/release-check-1.5.1-summary.json`，按阶段保留完整运行与定向补测的实际来源。
原始整轮记录 `tmp/release-check-1.5.1-final.json` 保留类型门禁失败，
补测记录为 `tmp/release-check-1.5.1-rechecks.json`，日志使用同名前缀。
开发期入口及 `test_kemo` 仍由 Git 忽略，正式文档合同测试随源码维护。

以上只证明本机发布前检查通过，不表示远程 Release/npm/GHCR 或 Docker 引擎、真实厂商 API
已经完成发布和联调。没有创建 tag、推送或上传产物；正式发布仍应从审核过的提交工作树重新打包。

## 历史验证证据

日期：2026-09-26，外部环境补验更新于 2026-09-28。本地验证均使用 `deploy/.test-work`
内的隔离目录，未覆盖开发仓库、未修改真实用户安装；2026-09-28 的补验在隔离安装目录与
临时容器数据卷中进行，同样未改动任何真实安装。同日源码内的错误序列重试修复落地后，
四个 1.3.2 分发产物已按同一版本号重建并覆盖上传，哈希与镜像摘要在下节同步更新。

## 已通过

### 1.3.2 外部环境实机补验（2026-09-28）

发布 v1.3.2 后，在真实公网资产与运行中的 Docker 引擎上补齐了此前标注为“未实机验收”的链路。
所有安装与运行都在隔离目录、临时数据卷中完成，未触碰任何真实安装：

- 远程 Release 直装：从 `releases/download/v1.3.2/` 下载 ZIP，SHA256 与同版本 `.sha256`
  资产一致（`28904d22…3991`），内层 `release-manifest.json` 版本为 1.3.2、`tree/` 与
  `tree/version.json` 齐备，归档内无 `users/` 与 `.env`。随后用 `deploy.py install windows
  --files-only` 装到隔离目录（1024 个文件），`deploy.py check windows` 报
  `installed=1.3.2; target=1.3.2; current`。
- npm 渠道免凭据安装：`npm install -g` 直接指向
  `releases/latest/download/kemo-agent-npm.tgz`，匿名拉取成功、装出 1.3.2，包内
  `release.zip` 的 SHA256 与 Release ZIP 完全一致，证明固定名资产与版本名资产同源。
- GHCR 镜像：由 `pack.py` 生成的 Docker 构建上下文（其 `release.zip` 与 Release 资产哈希一致）
  构建并推送 `ghcr.io/kesepain-ke/kemo-agent:1.3.2` 与 `latest`，索引摘要
  `sha256:1b0411d9…f424`，仅含 `linux/amd64` 平台。
- 容器运行与跨版本同步：用 1.3.1 镜像在空数据卷上首装（四个组件均为 1.3.1、
  `cron.session_idle_close_seconds=86400`），再以 1.3.2 镜像挂载**同一数据卷**启动。
  升级后四个组件均为 1.3.2、`version.json` 为 1.3.2、`/api/health` 返回 200 且容器进入
  `healthy`；挂载前写入数据卷的自定义文件仍在原处，且 1.3.2 的窄范围迁移把仍是旧默认值的
  86400 改成了 5400。这条实测覆盖了“容器旧数据卷跨版本同步”与“升级迁移生效”。

### 1.3.2 分发产物按同版本重建（2026-09-28）

源码内的「重试额度按错误序列重计」修复落地后，四个 1.3.2 分发产物以同一版本号重建并覆盖上传，
避免源码与产物语义不一致（版本号与 `version.json` 未变，tag `v1.3.2` 未移动）：

- `deploy/pack.py --out deploy/artifacts/1.3.2-retryfix` 产出 ZIP（6,029,115 字节，
  SHA256 `28904d22c4a2ee980abbf5942af821492b4709a5d2fbe560174095e5c0443991`）、npm 暂存目录与
  Docker 构建上下文；ZIP 内 1025 条目、manifest 1.3.2 共 1024 文件、含预构建前端，
  无 `users/` 与 `.env`，`tree/run/retry/loop.py` 已含新账本实现。
- `npm pack` 产出 `kesepain-ke-kemo-agent-1.3.2.tgz`（5,950,217 字节，SHA256
  `5e9c87d8…6da4`，17 个文件），内层 `release.zip` 与发布 ZIP 哈希一致，固定名
  `kemo-agent-npm.tgz` 与其逐字节相同。
- 镜像重建并推送后 `1.3.2` 与 `latest` 同指索引摘要 `sha256:1b0411d9…f424`；容器内
  `/opt/kemo-release.zip` 的 SHA256 与发布 ZIP 一致，容器启动后进入 `healthy`。
- 四个资产用 `gh release upload --clobber` 覆盖同一 Release；复核显示远程 ZIP 为
  6,029,115 字节、SHA256 与 `.sha256` 资产一致（`28904d22…3991`），内层 manifest 与
  `tree/version.json` 均为 1.3.2，两个 npm tarball 同源，`releases/latest` 仍指向 v1.3.2。

### 1.3.1 暂定版本分层补验

- 2026-09-26 完整执行 `python 开发临时目录/release_check.py`，8/8 阶段通过：
  `test_kemo` 92 项通过；正式后端 1419 项通过、9 项跳过、225 个 subtest 通过；
  模板合同 12 项通过；Vitest 371 项通过；Python 编译、`git diff --check` 和前端生产构建通过。
- `deploy/.test-work/` 是部署故障测试的持久工作根，运行后允许继续存在；模块行数与
  Run 导入边界的生产源码扫描显式排除 `.test-work`，不能通过手工删除目录来维持绿灯。
- 正式发布红线已归入 `tests/deploy/`；`python -m pytest tests/deploy -q` 当前为
  48 通过、1 跳过。跳过项仍是当前 Windows 权限不允许创建测试符号链接。
- `deploy/tests/` 只保留兼容薄入口，调用同一份 `tests/deploy/` 断言；独立运行结果同为
  48 通过、1 跳过，不再维护第二套重复测试。
- `开发临时目录/test_kemo/deployment_distribution/` 新增真实工作树系统验收：实际调用
  `pack.py --include-untracked` 生成 1.3.1 Release ZIP、npm 暂存目录和 Docker 构建上下文，
  校验 SHA256/manifest/预构建前端，再执行隔离的 `--files-only` 安装及安装树 `check`；
  当前 2 项全部通过。该目录被 Git 忽略，只补强系统集成，不替代正式测试。
- 四渠道命令、中英文介绍、知识索引、暂定版本与发布前提由正式文档合同测试覆盖。

以下记录为部署器首次实现时的跨平台与真实应用验收，继续作为历史证据保留：

- Windows Python：45 个部署测试，44 通过、1 跳过。跳过原因是当前 Windows
  权限不允许创建测试符号链接；对应符号链接测试在 Linux 通过。
- Ubuntu 24.04 / WSL，Python 3.12.3：同一组 45 个测试全部通过。
- Python 3.10 语法兼容检查（AST）；不等同于所有 Python 3.10 依赖实装测试。
- Windows PowerShell 安装脚本语法检查。
- Ubuntu `sh -n` 检查 Linux 安装脚本及薄包装。
- Node.js 启动器语法检查。
- Docker Compose 配置展开/校验。
- 真实工作树本地打包，ZIP 内清单、SHA256、版本及 Python 源文件语法验证。
- npm 发布暂存目录的 `npm pack --dry-run --ignore-scripts` 检查。
- 本地真实应用 ZIP 安装到隔离目录，使用已有依赖的解释器完成运行环境探测。
- 首次 `init --yes` 初始化成功。
- 实际启动隔离 Web，`/api/health` 返回 HTTP 200，随后停止测试进程树。
- npm Node 启动器的本地 `install/check/init` 链路。
- 使用 `python -S` 禁用 site-packages 执行本地包 `check all`，证明检查入口不依赖
  第三方 Python 库。

测试还覆盖：同版重装、版本顺序、降级拒绝、安装渠道冲突、配置只增不改、
配置类型/schema 冲突、用户数据保护、来源 SHA256、损坏包、目录逃逸、
大小写冲突、OS 锁并发、失败恢复、强制结束子进程后的恢复及两份成功备份保留。

## 尚未通过实机验收的范围

- 公开 npm Registry 仍未使用：npm 渠道按设计从 Release 资产直装，无需 registry 凭据，
  因此未验证 `@kesepain-ke` 命名空间的发布权限（当前流程也不再依赖它）。
- 未在完全干净的机器上从公网安装全部 pip 依赖；实际应用启动测试使用
  预装依赖的解释器。虚拟环境准备失败不污染旧版本的行为有隔离测试覆盖。
- 镜像只构建了 `linux/amd64`；ARM64、多架构清单与 Apple Silicon 未实测。
- 容器退出信号与 `stop_grace_period` 的停机行为未逐项计时验证，只确认了正常启动、
  健康检查与跨版本重启。
- macOS 未实机测试；系统服务、自启动、目录迁移、数据库降级不在本版范围。

发布前应完成这些外部环境验收，不能将 `check all` 或本地测试绿灯解释为
四个线上分发渠道已经全部可用。
