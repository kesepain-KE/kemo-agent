# kemo-agent update 模块板块化重构 · 编程方案

## 问题

当前 `update.py` 是单体脚本：从 GitHub 克隆整个仓库后，用一套固定的排除/同步逻辑完成全量更新。无法按板块独立更新（如"只更新插件生态"或"只更新 Web 服务"），也无法灵活控制各板块的不同更新策略（智能合并、仅注册模块、询问覆盖等）。

## 方案

在 `E:\code\kemo-agent\update\` 下建立板块化更新脚本体系，每个板块一个模块，统一受 `update.py` 调度。`update.py` 新增 `--module` 参数支持按板块更新，同时保留原有"一键全量更新"功能。

---

## 详细规划

### 第一步：建立 `update/` 包结构

```
E:\code\kemo-agent\update/
├── __init__.py          # 包初始化，导出各板块模块
├── _utils.py            # 公共工具函数（从现有 update.py 提取）
├── core.py              # 板块 1：核心引擎
├── agents.py            # 板块 2：智能体系统
├── plugins.py           # 板块 4：插件生态
└── web.py               # 板块 8：Web 服务
```

### 第二步：实现 `update/_utils.py` 公共工具

从现有 `update.py` 中提取以下函数到此模块，各板块脚本复用：

| 函数 | 来源 | 用途 |
|------|------|------|
| `green / yellow / red` | update.py | 终端着色 |
| `is_interactive()` | update.py | 判断是否交互终端 |
| `ask_yes_no()` | update.py | 交互确认 |
| `ask_choice()` | update.py | 交互选择 |
| `run()` | update.py | 执行命令 |
| `command_exists()` | update.py | 命令检查 |
| `require_commands()` | update.py | 命令前置检查 |
| `read_json()` | update.py | 读 JSON |
| `fetch_json()` | update.py | 读远程 JSON |
| `parse_version()` | update.py | 版本解析 |
| `compare_versions()` | update.py | 版本比较 |
| `tree_digest()` | update.py | 目录内容摘要 |
| `paths_differ()` | update.py | 目录差异判断 |
| `sync_directory()` | update.py | 纯 Python 目录同步（含 `_parse_excludes`、`_is_excluded`、`_walk_sync`、`_walk_delete`） |
| `_resolve_npm_command()` | update.py | npm 命令解析 |
| `_short()` | update.py | 路径缩短显示 |

新增工具函数：

```python
def copy_file_safe(src: Path, dst: Path, *, dry_run: bool = False) -> bool:
    """安全复制单个文件，返回是否实际复制"""
    if not src.is_file():
        return False
    if dst.exists() and tree_digest(src) == tree_digest(dst):
        return False  # 内容相同，跳过
    if dry_run:
        print(f"[dry-run]  复制  {_short(src)} -> {_short(dst)}")
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=False)
    return True


def sync_file_only(src: Path, dst: Path, *, dry_run: bool = False) -> None:
    """同步单个文件（覆盖），目录则报错"""
    if src.is_dir():
        raise ValueError(f"sync_file_only 不支持目录: {src}")
    copy_file_safe(src, dst, dry_run=dry_run)


def sync_files_by_names(src_dir: Path, dst_dir: Path, names: list[str], *, dry_run: bool = False) -> list[str]:
    """同步指定名称的文件列表，返回已同步的文件名列表"""
    ...


def sync_directory_except(src: Path, dst: Path, except_relative: list[str], *, delete: bool = True, dry_run: bool = False) -> None:
    """同步目录但排除指定相对路径（用于 message/ 排除 out/）"""
    ...
```

### 第三步：定义统一板块接口

每个板块模块必须实现以下函数签名：

```python
def update(
    source_root: Path,    # 远程源码根目录（临时克隆目录）
    target_root: Path,    # 本地项目根目录
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> dict:
    """
    返回:
    {
        "module": str,           # 板块名称
        "status": str,           # "ok" | "skipped" | "partial" | "failed"
        "details": list[str],    # 操作详情描述
        "warnings": list[str],   # 警告信息
    }
    """
```

### 第四步：实现各板块模块

#### `update/core.py` — 板块 1：核心引擎

负责以下目录/文件的更新：

| 目标 | 策略 | 说明 |
|------|------|------|
| `run/` | 强制覆盖 | 同步目录，含删除 |
| `cli.py` | 强制覆盖 | 单文件复制 |
| `events.py` | 强制覆盖 | 单文件复制 |
| `setup.py` | 强制覆盖 | 单文件复制 |
| `update.py` | 强制覆盖 | 单文件复制 |
| `requirements.txt` | 强制覆盖 | 单文件复制 |
| `config/global_soul.md` | 强制覆盖 | 单文件复制 |
| `config/global_config.json` | **询问** | 比对 `schema_version` 字段，不同则提示差异并询问 |
| `message/` | 排除 `out/` 覆盖 | 同步整个 message 目录但排除 `message/out/` 子目录 |
| `cron/` | 强制覆盖 | 同步目录，含删除 |
| `template/` | 强制覆盖 | 同步目录，含删除 |
| `tests/` | 强制覆盖 | 同步目录，含删除 |
| `.env.example` | 强制覆盖 | 单文件复制 |
| `LICENSE` | 强制覆盖 | 单文件复制 |
| `README.md` | 强制覆盖 | 单文件复制 |
| `kemo-agent.jpg` | 强制覆盖 | 单文件复制 |
| `version.json` | 强制覆盖 | 单文件复制 |
| `update/` | 强制覆盖 | 同步目录，含删除 |
| `global_expand/register.py` | 仅注册模块 | 只复制 `register.py`，不动其他文件 |
| `global_knowledge/` | 强制覆盖 | 同步目录，含删除 |
| `global_sense/register.py` | 仅注册模块 | 只复制 `register.py`，不动其他文件 |
| `shared_expand/register.py` | 仅注册模块 | 只复制 `register.py`，不动其他文件 |
| `shared_skills/register.py` | 仅注册模块 | 只复制 `register.py`，不动其他文件 |
| `agents.md` | 强制覆盖 | 单文件复制 |
| `user_create.py` | 强制覆盖 | 单文件复制 |

**`global_config.json` 询问逻辑**：

```python
def _update_global_config(source_root, target_root, *, dry_run, assume_yes):
    src = source_root / "config" / "global_config.json"
    dst = target_root / "config" / "global_config.json"
    
    if not src.is_file():
        return  # 源没有，跳过
    
    if not dst.is_file():
        copy_file_safe(src, dst, dry_run=dry_run)
        return
    
    # 比对 schema_version
    src_json = read_json(src)
    dst_json = read_json(dst)
    src_schema = src_json.get("schema_version")
    dst_schema = dst_json.get("schema_version")
    
    if src_schema == dst_schema and not paths_differ(src, dst):
        return  # 无变化
    
    if src_schema != dst_schema:
        print(yellow(f"global_config.json schema 版本不同: 本地={dst_schema}, 远程={src_schema}"))
        print(yellow("差异字段："))
        # 列出远程有但本地没有的顶层字段，以及本地有但远程没有的
        ...
    
    choice = ask_choice(
        "global_config.json 需要更新，请选择:",
        {"o": "覆盖为最新版本", "k": "保留本地版本", "d": "显示完整差异"},
        default="o",
        assume_yes=assume_yes,
    )
    if choice == "o":
        copy_file_safe(src, dst, dry_run=dry_run)
    elif choice == "d":
        # 调用 diff 或打印两边内容
        ...
        # 再次询问
        ...
```

#### `update/agents.py` — 板块 2：智能体系统

**智能合并**策略：

```python
def update(source_root, target_root, *, dry_run, assume_yes):
    """
    智能合并 agents/ 目录:
    1. 遍历 source_root/agents/ 下的子代理目录
    2. 对于每个远程子代理:
       - 如果本地不存在 → 直接复制
       - 如果本地存在且内容相同 → 跳过
       - 如果本地存在但内容不同 → 覆盖更新（因为这是框架内置代理）
    3. 不删除本地多出来的子代理目录（用户自建代理不动）
    4. _runtime/ 目录强制覆盖
    5. __init__.py 强制覆盖
    """
```

具体实现：

1. 先同步 `agents/_runtime/`（强制覆盖目录）
2. 同步 `agents/__init__.py`（强制覆盖文件）
3. 遍历 `source_root/agents/` 下的子代理目录（排除 `_runtime/`、`__pycache__/`、`__init__.py`）：
   - 用 `paths_differ()` 判断是否需要更新
   - 需要更新的用 `sync_directory(source, target, delete=True)` 覆盖
   - 不需要的跳过
4. 对于 `agent-config.json` 和 `agent.json` 等关键文件，单独做版本比较

#### `update/plugins.py` — 板块 4：插件生态

```python
def update(source_root, target_root, *, dry_run, assume_yes):
    """
    强制覆盖 plugins/ 目录:
    - 使用 sync_directory(source, target, delete=True) 完全同步
    - 删除本地多余文件
    """
```

#### `update/web.py` — 板块 8：Web 服务

```python
def update(source_root, target_root, *, dry_run, assume_yes):
    """
    强制覆盖 web/ 目录:
    - 使用 sync_directory(source, target, delete=True) 完全同步
    - 排除 web/node_modules/、web/dist/、web/frontend/node_modules/、web/frontend/dist/
    """
```

### 第五步：改造 `update.py` 调度器

新增 `--module` 参数，支持：

```bash
python update.py                  # 一键全量（等同于 --module all）
python update.py --module core    # 仅核心引擎
python update.py --module agents  # 仅智能体系统
python update.py --module plugins # 仅插件生态
python update.py --module web     # 仅 Web 服务
```

改造后的 `main()` 流程：

```
1. 解析参数（新增 --module，默认 "all"）
2. 如果 --check：仅打印版本比对结果
3. 加载本地/远程 version.json，比对版本
4. 询问用户是否继续（除非 --yes）
5. 克隆远程仓库到临时目录
6. 创建备份
7. 按 --module 参数调用对应板块：
   - "all" → 依次调用 core → agents → plugins → web
   - 指定板块 → 只调用该板块
8. 各板块返回结果汇总
9. 迁移用户骨架和记忆
10. 构建 Web 前端（除非 --skip-web-build，且仅在 web 板块被更新时）
11. 刷新 Python 依赖（除非 --skip-deps，且仅在 core 板块被更新时）
12. 打印汇总报告
```

板块调度逻辑：

```python
MODULES = {
    "core": ("核心引擎", "update.core"),
    "agents": ("智能体系统", "update.agents"),
    "plugins": ("插件生态", "update.plugins"),
    "web": ("Web 服务", "update.web"),
}

def run_modules(module_names, source_root, target_root, *, dry_run, assume_yes):
    results = []
    for name in module_names:
        label, import_path = MODULES[name]
        print(f"\n{'='*50}")
        print(f"  板块: {label}")
        print(f"{'='*50}")
        mod = importlib.import_module(import_path)
        result = mod.update(source_root, target_root, dry_run=dry_run, assume_yes=assume_yes)
        results.append(result)
    return results
```

保留现有参数：`--check`、`--force`、`--yes`/`-y`、`--dry-run`、`--skip-web-build`、`--skip-deps`、`--repo-url`、`--branch`。

### 第六步：`version.json` 已对齐板块

`version.json` 已精简为与更新板块一一对应，结构如下：

```json
{
  "name": "kemo-agent",
  "version": "0.1.0",
  "schema_version": 1,
  "components": {
    "core":    { "version": "0.1.0", "description": "核心引擎" },
    "agents":  { "version": "0.1.0", "description": "子代理系统" },
    "plugins": { "version": "0.1.0", "description": "工具插件生态" },
    "web":     { "version": "0.1.0", "description": "Web 前端+后端" }
  }
}
```

顶层 `version` 对应 `--module all`，四个 `components` 各自对应一个更新板块。`update.py` 的 `--check` 比对各组件版本时，远程 `version.json` 必须与此结构一致。

### 第七步：清理

- 移除 `update.py` 中原有的 `MAIN_EXCLUDES`、`sync_main_source()`、`handle_config()`、`handle_global_knowledge()` 等已被板块模块取代的逻辑
- 保留 `make_backup()`、`clone_latest()`、`migrate_user_skeletons()`、`migrate_user_memories()`、`build_web_frontend()`、`refresh_dependencies()`、`load_versions()`、`should_update()` 等顶层流程函数

---

## 应达到的效果

1. **板块独立更新**：`python update.py --module plugins` 只更新 `plugins/` 目录，不动其他任何文件
2. **一键全量不变**：`python update.py` 或 `python update.py --module all` 等同于原先的完整更新流程
3. **智能合并 agents/**：远程新增的代理复制过来，远程更新的代理覆盖，用户自建的代理原封不动
4. **global_config.json 受保护**：schema 版本变化时明确提示差异字段，用户可选择覆盖/保留/查看差异
5. **message/out/ 受保护**：无论全量更新还是核心引擎更新，`message/out/` 目录不会被覆盖或删除
6. **仅注册模块更新**：`global_sense/register.py`、`shared_expand/register.py`、`shared_skills/register.py`、`global_expand/register.py` 只更新 register.py 文件，不触及同目录下的用户数据
7. **统一结果报告**：每次更新完成后汇总各板块状态（ok/skipped/failed），清晰展示变更范围
8. **向后兼容**：现有 `--check`、`--yes`、`--dry-run` 等参数行为不变
