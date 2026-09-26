# kemo-agent 独立四渠道部署

此目录包含部署器、测试、打包工具和四种渠道入口。**不导入主框架，不改动主框架文件；只有部署器本身零第三方依赖，应用运行依赖仍须安装。**

> 当前主框架暂定版本 **1.3.1（待发布）**，配套网关仍为 **0.8.2**。本页远程命令仅在
> 安装脚本及对应 Release/npm/GHCR 产物发布后可用；Git push 不会自动发布这些资产。
> 中英文快速开始分别见 `../readme.md`、`../README_EN.md`，日常命令与渠道选择见
> `../global_knowledge/deployment-and-release.md`。已有 1.3.0 本地验收包不能改名充当 1.3.1，须重新构建打包。

## 1. 发布合同：一个版本、一份应用包

版本唯一真值：应用根目录的 `version.json.version`。

```text
kemo-agent-release-<version>.zip
kemo-agent-release-<version>.zip.sha256
```

ZIP 结构固定如下（不接受 GitHub 自动生成的源码 ZIP）：

```text
release-manifest.json             # schema_version/name/version/files(SHA256)
tree/
  version.json                   # 主框架版本
  requirements.txt
  setup.py
  start_web.py
  run/ ...
  web/frontend/dist/index.html   # 预构建前端；客户端不要求 Node.js
  deploy/
    deploy.py
    deploy.yaml
    core/ ...
```

包文件名、Release 标签对应版本、包内 manifest 和主框架版本必须一致。
默认标签格式为 `v<version>`，可在配置中更改 `tag` 模板。
四个渠道不维护独立版本文件。npm 分发版本由打包器从主框架版本生成，
源码 npm 清单不存版本且标记为 private，防止误把未组装的壳直接发布；
Docker 镜像应使用同一主框架版本作为标签。网关 0.8.2 是兼容基线，不是部署器版本。

远程 ZIP 依据官方 GitHub Release API 提供的 SHA256 digest 校验；若 API
没有 digest，则读取**官方来源**的 `.sha256` 资产。镜像只用于 ZIP 下载回退，
不作为校验值的信任来源。此机制是可信源下的完整性校验，不是独立数字签名。
显式 HTTPS `--source` 必须带 `--sha256`；本地 ZIP 是操作者提供的可信输入，
仍会核对内部清单、文件哈希和版本。

Release 尚未发布时，远程安装会明确失败，不把“缺包”误报为已安装或已是最新。

## 2. 开发和发布打包

先按项目流程完成前端生产构建，再在仓库根目录运行：

```powershell
python -B deploy/pack.py
```

默认只选择 Git 跟踪的交付文件，额外加入前端 `dist`。首次开发时，部署器尚未
纳入 Git，可以显式传入 `--include-untracked` 做本地验收；正式发布应从经过
审核、已提交的工作树打包。打包器读取工作树内容，**不保证它与 HEAD 一致**。

默认输出均在本目录内：

```text
deploy/artifacts/<version>/
  kemo-agent-release-<version>.zip
  kemo-agent-release-<version>.zip.sha256
  npm/                           # npm 发布暂存目录
  docker/                        # Docker 构建上下文
```

若输出目录已存在，工具拒绝覆盖；使用 `--out <新的输出父目录>`。
不会自动上传 Release、发布 npm、推送镜像或执行 Git 提交。

打包清单按交付目录限制范围，并排除运行数据库、真实 `.env`、用户目录、APP
凭据/绑定、模块运行数据、缓存和本部署器测试产物。模板中的默认面板值仍会打包。
模块清单中的本地健康/最近更新时间会移除，内置拓展默认不激活。
**发布者仍须审核归档和配置，不应把过滤器当作完备的秘密扫描器。**

发布流程需要上传 ZIP 及其 `.sha256`，另外显式执行：

```text
npm pack ./deploy/artifacts/<version>/npm --dry-run
npm publish ./deploy/artifacts/<version>/npm

# 以下也从仓库根目录执行
docker build -t ghcr.io/kesepain-ke/kemo-agent:<version> deploy/artifacts/<version>/docker
docker push ghcr.io/kesepain-ke/kemo-agent:<version>
```

npm 命名空间、GHCR 权限、镜像标签和 Release 资产由发布者管理。需要 Compose
默认 `latest` 时，还须发布相应镜像标签；否则显式设置 `KEMO_VERSION`。
这里提供构建工具，不修改仓库外层 CI，也不假设远程资产已经存在。

## 3. 本地包首装与更新

需要 Python 3.10+；Linux 发行版还须提供可用的 `venv`/`ensurepip`。

```powershell
python deploy/deploy.py install windows --source "D:\packages\kemo-agent-release-1.3.1.zip" --install-root "D:\apps\kemo-agent" --yes
python deploy/deploy.py update --platform windows --source "D:\packages\kemo-agent-release-1.3.1.zip" --install-root "D:\apps\kemo-agent" --dry-run
```

Windows 默认安装根为 `%USERPROFILE%\.kemo-agent`；Linux 和 npm 为
`~/.kemo-agent`。`--install-root` 可在首装时指定空目录。已有目录若不是本部署器
管理的安装，直接拒绝覆盖，**不会接管开发仓库或旧的手工安装**。

安装完成后使用安装树自己的入口，例如：

```powershell
python "D:\apps\kemo-agent\deploy\deploy.py" start windows --install-root "D:\apps\kemo-agent"
```

首次 `start` 通过子进程调用现有 `setup.py --skip-deps --skip-web`，初始化 `.env`
和用户；之后调用原有 `start_web.py`。加 `--yes` 使用原向导的默认用户设置，
未加时走交互。`init` 可以仅完成初始化，不启动服务。
使用安装树自身的部署入口时，会自动识别该树的安装根与渠道；自定义安装路径无需
每次重复传入。上面的显式路径参数也可用于从外部部署器管理指定安装。

```text
deploy install windows
deploy update windows --yes
deploy check
deploy check all
deploy recover windows
deploy start windows -- --host=127.0.0.1 --port=1357
```

Windows 用 `deploy.cmd`，Unix 用 `sh deploy/deploy` 或为该文件添加执行权限。
位置参数和 `--platform` 等价；两者同时指定且冲突时拒绝执行。

### 参数

| 参数 | 语义 |
|---|---|
| `--platform` | windows/linux/npm/docker/all；未指定时根据宿主选择 windows/linux |
| `--version` | 指定主框架版本；远程默认最新稳定 Release |
| `--source` | 本地 ZIP 或 HTTPS ZIP URL |
| `--sha256` | 来源校验值；显式 HTTPS 来源必须提供 |
| `--config` | 显式部署配置路径 |
| `--install-root` | 首装根目录；已有安装记录禁止静默迁移 |
| `--dry-run` | 只读计划；不创建安装根、不写锁、不建虚拟环境 |
| `--yes` | 跳过安装确认；操作者须事先停止绕过部署器启动的进程 |
| `--force` | 同版重装；不意味着允许降级或覆盖本地代码修改 |
| `--allow-downgrade` | 显式允许降级代码；不提供数据库逆向迁移 |
| `--overwrite-modified` | 备份后覆盖已由发布包管理、被本地修改的代码；不覆盖用户数据 |
| `--files-only` | 仅铺文件，记录为运行环境未准备；用于离线检查和测试 |
| `--runtime-python` | 使用预装好依赖的解释器；供 Docker 使用 |

`all` 仅支持 `check/update`。检查逐一报告配置根目录及实际安装所有者；它不是
“四种渠道已实机验收”的证明。更新只处理已安装且渠道匹配的实例，按绝对根目录
去重；不在 Windows 上部署 Linux、不在宿主上将 `/data` 当作容器更新。
不能解析的跨平台根目录会明确报告；不会猜测另一个操作系统的用户主目录。

## 4. 四种渠道入口

### Windows

```powershell
irm https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/windows/install.ps1 | iex
```

需要已安装 Python 3.10+。可先下载脚本审阅，再通过 `-Version`、`-InstallRoot`、
`-Yes` 参数执行。重跑会按安装记录检查升级。脚本不改 PATH、不提权、不安装系统
级 Python；安装后按提示使用安装树入口启动。

### Linux / macOS Python 环境

```sh
curl -fsSL https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/linux/install.sh | sh
```

管道模式不依赖交互输入。安装完成后：

```sh
python3 "$HOME/.kemo-agent/deploy/deploy.py" start linux
```

macOS 复用 Unix 入口，尚需在目标系统验收；不提供 Homebrew 系统依赖自动安装。
流水线入口运行远程脚本是明确的信任选择，可以先下载审查再运行。

### npm

```sh
npm install -g https://github.com/kesepain-KE/kemo-agent/releases/latest/download/kemo-agent-npm.tgz
kemo
```

包直接从 GitHub Release 资产安装，**不需要任何 registry 凭据**。包含这次框架发布的
ZIP 和独立部署器。**没有 postinstall**，首次 `kemo` 才部署；关闭 npm 安装脚本不影响
命令入口。应用仍需 Python 和 pip 网络。重跑上面同一条命令即可更新分发包（URL 里的
`latest` 永远指向最新版），下次 `kemo` 启动前更新应用。
`kemo update` 使用当前 npm 包携带的框架版本，不再额外跟随 GitHub latest。
`KEMO_INSTALL_ROOT` 可指定独立安装目录；不接管 Windows/Linux 渠道拥有的根目录。

### Docker

没有源码仓库时，可在一个固定的专用目录下载 Compose 后直接启动（不要覆盖已有文件）：

```sh
curl -fsSL https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/docker/docker-compose.yml -o docker-compose.yml && docker compose up -d
# 后续始终在同一个 Compose 项目目录中执行
# 更新前先停止应用，保留命名数据卷
docker compose stop
docker compose pull
docker compose up -d
```

已有源码仓库时，也可使用仓库内配置；不要与上面不同项目目录的命令混用：

```sh
docker compose -f deploy/docker/docker-compose.yml up -d
docker compose -f deploy/docker/docker-compose.yml stop
docker compose -f deploy/docker/docker-compose.yml pull
docker compose -f deploy/docker/docker-compose.yml up -d
```

镜像内是 `/opt/kemo-release.zip` 和预装依赖的 `/opt/venv`，运行树为 `/data`。
**每次新镜像启动都会对已有数据卷做版本判断和事务同步**，不是只在空卷铺种子。
同版不重写应用。同步失败即退出，绝不启动混合版本。运行依赖来自镜像，不在容器
启动时联网安装。容器是非 root 用户 UID/GID 1000，bind mount 需预先设置目录权限。

Compose 默认只映射本机 `127.0.0.1:1357`；容器内固定监听 `0.0.0.0:1357`。
用户数据卷不得使用 `down -v` 删除。入口首次初始化采用原向导默认值，首次使用
应完成用户凭据配置；外网暴露、TLS、反向代理不由部署器自动开启。

## 5. 数据与配置保护

用户指定的 preserve 只能**添加**保护，不能关闭内建保护。

内建保护覆盖：用户目录、运行数据、`.env`、消息队列、图谱存储、APP 绑定与凭据、
模块配置/状态/面板值、数据库、锁、PID、日志、虚拟环境和部署器事务区。
源码、面板定义、模板和预构建前端可更新；用户自行创建的模块默认不被同名文件接管。
删除仅限上次安装清单中的旧官方文件，且删除前哈希必须与上次安装一致；本地修改
过的旧文件保留并报告。不会清理安装树内未知文件。

`config/global_config.json`、模块定义及系统定时任务 JSON 采用保守默认值合并：
保留已有值、添加新键、拒绝 schema/type 冲突。部署配置也是按键路径合并，而不是
比较键数量。列表、false、0、空字符串和 null 不会被错误当作“未配置”。
内容未变时不重写；发生合并时写成合法 JSON（YAML 子集），原文件含注释的版本在
备份中。复杂配置迁移须人工处理，不声称与主框架更新器所有业务迁移完全等价。

本版不提供 `--reset-config`、任意路径映射或目录迁移。固定映射为
`tree/ -> install_root/`；不支持的映射和交互文件明确报错，而不是静默忽略。
锁定根目录记录在 `.kemo-install.json`，复制目录后不能直接冒充原安装继续更新。

## 6. 事务与运行边界

安装/更新核心的写入限定在安装根内。下载在有上限的内存中完成，ZIP 不直接解压到
任意目标；拒绝路径逃逸、链接/junction、ADS、重复大小写路径及过大归档。
外层 bootstrap 唯一例外：在系统私有临时目录保存校验过的包与临时部署器，结束后
仅清理它创建的临时目录。npm 全局包目录和 Docker 镜像是外层包管理器的写入范围。

部署和运行监督器共用原更新器兼容的 `.update.lock` OS 锁，无需导入旧更新器。
由 `deploy start` / npm / Docker 启动的应用存活期间保持锁，防止在线覆盖源码。
**绕过部署入口直接运行原有命令的进程无法全部自动识别，更新前必须手动停止。**
Unix 监督器转发退出信号；Windows 正常停止入口时会结束其自身启动的应用进程树，
属于强制结束，未完成请求可能中断。如果外部工具只强制杀掉启动器而留下服务进程，
也必须先手动停止遗留进程再更新；本版不安装系统服务或 Windows Job Object。
根路径原有命令不受代码修改；但不应交替用旧更新器和 deploy 管理同一安装。
如果实际框架版本与部署记录不一致，部署器拒绝继续，避免掩盖版本漂移。

依赖按 requirements/解释器指纹建立新的虚拟环境，成功后才在安装记录中切换。
不修改旧环境；未完成环境保留在私有缓存区，暂不自动做环境垃圾回收。
requirements 存在版本范围，因此这不是依赖可复现锁定方案。

文件事务先完整备份，再持久化 journal，再执行替换；安装记录最后写入。失败立即
恢复，强制结束后下次 `recover/install/update/start` 恢复未完成事务。
回滚会还原旧文件、删除此次新增文件、恢复删除文件、清理此次产生的空目录。
只自动保留最近两次成功事务备份，未完成/失败事务材料不自动删除。
不承诺断电级文件系统持久性，也不回滚用户数据库或初始化产生的用户数据。

`start` 前的首次初始化与源码安装分开：安装成功意味着源码、前端和依赖准备成功；
不意味着已经填好 API Key、建立网关连接或完成业务健康验收。

## 7. 验证

```sh
python -m pytest tests/deploy -q
# 兼容仅在 deploy 子系统中运行的旧入口：
python -B -m unittest discover -s deploy/tests -v
node --check deploy/npm/bin/kemo.cjs
```

测试只在 `deploy/.test-work` 创建隔离夹具，不连接远程 Release、不改真实用户安装。
包含：本地首装/升级、数据保留、配置合并、版本漂移、降级拒绝、ZIP 校验、只读
dry-run、锁冲突、异常回滚、子进程强制退出后恢复、成功备份保留数量。

发布前必须另外实测：干净 Windows/Linux 首装；Docker 旧卷跨版本更新与信号退出；
npm 在干净 Node/Python 环境安装后的命令启动；真实 Release 下载与镜像回退。
`deploy check all`、单元测试或语法检查都不能替代这些实机测试。
