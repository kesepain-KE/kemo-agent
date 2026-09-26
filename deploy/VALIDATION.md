# 部署模块验证记录

日期：2026-09-26。验证均使用 `deploy/.test-work` 内的隔离目录；未覆盖开发仓库、
未修改真实用户安装、未上传 Release、未发布 npm、未推送镜像或 Git。

## 已通过

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

- 本机 Docker 引擎未运行：仅完成 Compose 校验，未构建/运行镜像，未实测
  容器旧数据卷跨版本同步及容器退出信号。
- 未从公开 npm Registry 拉取本项目包，也未验证该命名空间发布权限。
- 真实 Release 资产尚未发布，未执行远程 bootstrap 一键安装和公网镜像回退。
- 未在完全干净的机器上从公网安装全部 pip 依赖；实际应用启动测试使用
  预装依赖的解释器。虚拟环境准备失败不污染旧版本的行为有隔离测试覆盖。
- macOS 未实机测试；系统服务、自启动、目录迁移、数据库降级不在本版范围。

发布前应完成这些外部环境验收，不能将 `check all` 或本地测试绿灯解释为
四个线上分发渠道已经全部可用。
