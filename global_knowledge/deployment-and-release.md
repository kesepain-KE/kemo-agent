# 一键部署、日常运维与发布

## 范围与发布状态

本文是 `deploy/` 独立部署系统的知识合同；源码业务更新器另见 `version-and-update-modules.md`。
当前主框架暂定版本为 **1.3.1（待发布）**，延续 `kemo-adapter-api 0.8.2` 已确认的兼容基线。
版本唯一真值是根目录 `version.json.version`。Windows、Linux、npm、Docker 不另设部署版本。

**安装入口实现不等于线上已发布。** 原生远程安装须先推送安装脚本，并上传对应 Release 资产；
npm 与 Docker 须分别发布分发包和镜像。Git push 不会自动完成这些发布。
不能把之前 1.3.0 本地验收包重命名为 1.3.1；必须按新版本重新构建和打包。
已完成的验收范围与未完成的远程/容器验收见 `deploy/VALIDATION.md`，不要声称全部渠道已经上线。

## 选择渠道与安装根

| 渠道 | 宿主依赖 | 默认安装根 | 日常入口 |
|---|---|---|---|
| Windows | Python 3.10+ | `%USERPROFILE%\.kemo-agent` | 安装根中的 `deploy/deploy.py` |
| Linux | Python 3.10+、可用的 venv/ensurepip | `~/.kemo-agent` | 安装根中的 `deploy/deploy.py` |
| npm | Node.js 18+、npm、Python 3.10+、可用的 Python 虚拟环境 | 用户主目录下 `.kemo-agent` | `kemo` |
| Docker | Docker Engine、Docker Compose | 容器 `/data` 命名卷 | 同一项目的 `docker compose` |
| 源码 | Git、Python 3.10+、Node.js/npm（前端构建） | 克隆的源码目录 | `setup.py` / `start_web.py` / `update.py` |

Release 包含预构建前端；原生安装不要求 Git/Node.js，但仍需安装应用的 Python 依赖，
只有部署器本身零第三方依赖。macOS 可复用 Unix 入口，尚未完成目标系统实机验收。

同一安装根不能在渠道间接管，也不能交替使用旧 `update.py` 和独立部署器更新。
非空未受管目录不会被接管，开发仓库不能作为首装目标。首装时通过 `--install-root`
指定独立路径；npm 使用 `KEMO_INSTALL_ROOT`。本版不提供安装目录迁移。
使用安装树内的部署入口会自动识别该树的渠道和根目录。

## 四渠道首装命令

以下远程命令仅在各自发布产物就绪后使用。管道脚本会执行远程代码，可先下载审查。

### Windows / PowerShell

```powershell
irm https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/windows/install.ps1 | iex
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" start
```

仅有 `py` 启动器时，将第二行的 `python` 换成 `py -3`。安装脚本支持下载后使用
`-Version`、`-InstallRoot`、`-Yes` 参数；不会自动安装系统 Python、修改 PATH、提权或创建系统服务。

### Linux

```sh
curl -fsSL https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/linux/install.sh | sh
python3 "$HOME/.kemo-agent/deploy/deploy.py" start
```

管道安装使用非交互安装确认；`start` 默认仍走首次配置向导。Windows/Linux 安装脚本
只安装，不自动常驻启动。首次 `start` 调用 `setup.py --skip-deps --skip-web` 初始化配置与用户，
之后启动 `start_web.py`；`init` 只初始化，`start --yes` 使用原向导的默认用户设置。
默认 Web 地址为 `http://127.0.0.1:1357`，首次使用仍应完成模型服务及访问凭据配置。

### npm

```sh
npm install -g @kesepain/kemo-agent
kemo
```

包内附带该版本的应用 ZIP。无 `postinstall`，首次 `kemo` 才部署并启动。
`kemo check` / `kemo update` 的目标是本机 npm 包附带的版本，不是 GitHub 最新 Release。

### Docker

在一个专用且后续固定的 Compose 项目目录中执行，不要覆盖已有 Compose 文件：

```sh
curl -fsSL https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/docker/docker-compose.yml -o docker-compose.yml && docker compose up -d
```

默认镜像 `ghcr.io/kesepain-ke/kemo-agent:latest` 必须先发布；也可通过 `KEMO_VERSION`
指定已发布的主框架版本标签。数据挂载到 `/data`，每次新镜像启动都会判断版本并事务同步
受管应用文件，而不只是初始化空卷。依赖预装在镜像中，启动时不联网安装。
默认宿主只绑定 `127.0.0.1:${KEMO_WEB_PORT:-1357}`，容器内固定 1357。
容器采用 UID/GID 1000，使用 bind mount 时须预先准备目录权限。

## 日常启动、检查、更新和恢复

原生安装关闭启动进程后不会自动常驻或开机自启。更新前先停止应用，尤其是绕过部署器
直接启动的进程；`--yes` 不代表可以安全地在线覆盖运行中的代码。

Windows 默认路径：

```powershell
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" start
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" check
# 先停止应用；check 只检查，不会升级
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" update --yes
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" start
# 仅在存在未完成的部署事务、需要恢复时使用
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" recover
```

Linux 默认路径：

```sh
python3 "$HOME/.kemo-agent/deploy/deploy.py" start
python3 "$HOME/.kemo-agent/deploy/deploy.py" check
# 先停止应用
python3 "$HOME/.kemo-agent/deploy/deploy.py" update --yes
python3 "$HOME/.kemo-agent/deploy/deploy.py" start
# 中断事务恢复
python3 "$HOME/.kemo-agent/deploy/deploy.py" recover
```

npm 先停止应用，升级分发包，再启动：

```sh
npm install -g @kesepain/kemo-agent@latest
kemo
```

Docker 始终在同一 Compose 项目目录执行：

```sh
docker compose logs -f
docker compose stop
docker compose pull
docker compose up -d
```

保持相同项目目录、项目名和数据卷；**不要用 `docker compose down -v` 更新**。
如果换项目后看到空白数据，先排查是否选择了新卷，不要删除原卷。

## 数据与故障边界

- 部署更新以整份 Release 为单位，不接受旧更新器 `--module core/agents/plugins/web` 的语义。
- `check` 只检查，`--dry-run` 只生成计划，`--files-only` 不准备运行依赖，均不能报告成部署成功。
- 用户数据、凭据、绑定、运行配置及数据库受保护；配置按键路径保留旧值、添加新键，类型或 schema 冲突拒绝更新。
- 未知文件冲突不静默覆盖；受管代码有本地改动时需显式授权覆盖。`--force` 只允许同版重装，不自动允许降级或覆盖改动。
- 新依赖环境准备完后切换，更新有持久化事务和失败回滚；进程被强杀留下未完成事务时用 `recover`。
- 保留两次成功备份，但不提供数据库逆向迁移，不能把代码降级当作完整数据恢复。重要数据仍应另行备份。
- 不声称 deploy 与旧业务更新器的全部迁移语义等价。安装版本与部署记录漂移时先排查混用入口。

## 维护者打包与发布

标准资产（非 GitHub 自动源码 ZIP）：

```text
kemo-agent-release-<version>.zip
kemo-agent-release-<version>.zip.sha256
```

ZIP 顶层是 `release-manifest.json` 和 `tree/`，包含主框架、部署器与预构建前端。
包名、manifest、`tree/version.json` 和默认 `v<version>` Release 标签须一致。
官方 Release digest 或官方 `.sha256` 用于校验；镜像仅回退 ZIP 下载，不提供可信校验值。

先构建前端，再从仓库根运行：

```sh
npm --prefix web/frontend ci
npm --prefix web/frontend run build
python -B deploy/pack.py --out deploy/artifacts/release-ready
```

输出位于 `deploy/artifacts/release-ready/<version>/`，包含 ZIP、校验文件、`npm/` 发布暂存目录和
`docker/` 构建上下文。输出已存在时换一个新的 `--out`，工具拒绝覆盖。
默认只纳入 Git 已跟踪交付文件，额外纳入前端 dist；正式发布须从审核过的提交工作树打包。
`--include-untracked` 仅供开发验收，不应不经审核用于正式发布。

发布者需要分别上传 ZIP 和校验文件到对应 Release、发布生成的 npm 暂存目录，以及构建并推送
Docker 主框架版本标签（需要默认入口时同时发布 latest）。不要直接发布标记 private 的
`deploy/npm/` 源码目录。`pack.py` 不提交、不推送、不发布，现有 CI 也不能被假定自动完成此链路。
具体操作和完整参数见 `deploy/README.md`。

## 测试分层与发布门禁

可提交、随源码上传的正式部署测试位于 `tests/deploy/`，由常规发布红线自动收集：

```powershell
python -m pytest tests/deploy -q
```

`deploy/tests/` 仅保留兼容薄入口，导入同一份正式断言，不允许再维护一套平行测试：

```powershell
python -B -m unittest discover -s deploy/tests -v
```

需要真实工作树打包、产物装配及隔离安装的开发期系统验收位于被 Git 忽略的
`开发临时目录/test_kemo/deployment_distribution/`：

```powershell
python -m pytest "开发临时目录/test_kemo/deployment_distribution" -v
```

正式测试负责稳定协议、事务失败路径和文档合同；开发期套件实际调用 `pack.py`，生成
Release/npm/Docker 产物并执行隔离安装，不复制正式套件的单元断言。远程 Release、npm
Registry、GHCR 和 Docker 引擎实机验收仍须在产物发布后单独完成，本地绿灯不能代替。
