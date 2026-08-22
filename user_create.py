"""kemo-agent 用户创建与管理模块

用法:
    python user_create.py              # 交互式管理菜单（新建/编辑/删除/退出）
    python user_create.py <用户名>      # 静默创建用户
    python user_create.py <用户名> --overwrite  # 覆盖已有用户

也可作为模块导入:
    from user_create import create_user, edit_user_api_config, delete_user
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import shutil
import sys
from pathlib import Path

# ── 常量 ──

_PROVIDER_TYPES = ("kemo", "chat")
_NAME_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f.][^\\/:*?\"<>|\x00-\x1f]{0,63}$")


# ── 工具函数 ──


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _validate_name(name: str) -> str:
    """校验用户名合法性，返回规范化名称。"""
    name = name.strip()
    if not name or not _NAME_RE.fullmatch(name) or name in {"_template", ".", ".."}:
        raise ValueError(f"非法用户名：{name!r}")
    return name


def _list_users(root: Path) -> list[str]:
    """列出所有非模板用户。"""
    users_dir = root / "users"
    if not users_dir.is_dir():
        return []
    return sorted(
        item.name
        for item in users_dir.iterdir()
        if item.is_dir() and not item.name.startswith("_")
    )


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


# ── 核心：创建用户 ──


def create_user(
    name: str,
    root: Path | None = None,
    *,
    interactive: bool = False,
    provider_type: str = "kemo",
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    overwrite: bool = False,
) -> Path:
    """创建用户目录并初始化。

    Args:
        name: 用户名
        root: 项目根目录，默认当前文件所在目录
        interactive: 是否交互式引导 API 配置
        provider_type / base_url / api_key / model: API 配置默认值
        overwrite: 是否覆盖已有用户目录

    Returns:
        用户目录路径
    """
    if root is None:
        root = _project_root()
    root = root.resolve()
    name = _validate_name(name)
    dest = root / "users" / name

    # ── 检查已存在 ──
    if dest.exists():
        if not overwrite:
            raise FileExistsError(f"用户已存在：{name}")
    else:
        template = root / "template" / "user"
        if not template.is_dir():
            raise FileNotFoundError(f"用户模板不存在：{template}")
        shutil.copytree(template, dest)

    # ── 后处理 ──
    _post_process_knowledge(root, name)
    _post_process_dirs(root, name)
    _post_process_memory(root, name)

    # ── 引导 API 配置 ──
    if interactive:
        provider_type, base_url, api_key, model = _guide_api_config(
            provider_type, base_url, api_key, model
        )
        _apply_api_config(root, name, provider_type, base_url, api_key, model)

    return dest


def _post_process_knowledge(root: Path, name: str) -> None:
    """初始化 knowledge/data_structure.md"""
    index_path = root / "users" / name / "knowledge" / "data_structure.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if index_path.exists() and index_path.stat().st_size > 0:
        return
    index_path.write_text(
        f"# users/{name}/knowledge/ 目录结构\n\n"
        f"用户 {name} 的私有知识库。\n\n"
        f"| 文件 | 说明 |\n"
        f"|------|------|\n"
        f"| `data_structure.md` | 本索引文件 |\n\n"
        f"## 检索规则\n\n"
        f"1. 先查此目录，再查全局知识库。\n"
        f"2. 用户私有信息默认写入此目录。\n"
        f"3. 文件发生增删改移后必须同步本索引。\n",
        "utf-8",
    )


def _post_process_memory(root: Path, name: str) -> None:
    """初始化每用户独立的 SQLite 记忆、历史与任务计划库。"""

    from run.history import connection as history_connection
    from run.memory import connection
    from run.tasks import PlanStore

    with connection(root, name):
        pass
    with history_connection(root, name):
        pass
    PlanStore(root, name).list_plans()


def _post_process_dirs(root: Path, name: str) -> None:
    """补齐所有应有的空子目录"""
    user_dir = root / "users" / name
    subdirs = (
        "download",
        "file_upload",
        "expand",
        "task_plan",
        "task_cron",
        "agents",
        "history",
        "avatar",
        "improve",
        "knowledge",
        "user_skills/agent_create",
        "user_skills/user_create",
    )
    for sub in subdirs:
        (user_dir / sub).mkdir(parents=True, exist_ok=True)


# ── API 配置引导 ──


def _guide_api_config(
    provider_type: str = "kemo",
    base_url: str = "",
    api_key: str = "",
    model: str = "",
) -> tuple[str, str, str, str]:
    """交互式引导用户填写 Provider 配置。返回最终的四个值。"""
    print()
    print("  " + "─" * 42)
    print("  Provider 配置（直接回车保留当前值 / 跳过）")
    print("  " + "─" * 42)
    print()

    answer = input(f"  Provider 类型 [kemo/chat] ({provider_type}): ").strip().lower()
    if answer in _PROVIDER_TYPES:
        provider_type = answer

    display_url = base_url or "(默认读环境变量)"
    answer = input(f"  API Base URL {display_url}: ").strip()
    if answer:
        base_url = answer

    try:
        answer = getpass.getpass("  API Key (不回显，留空不变): ")
        if answer.strip():
            api_key = answer.strip()
    except (EOFError, KeyboardInterrupt):
        print()

    display_model = model or "(默认)"
    answer = input(f"  模型名称 {display_model}: ").strip()
    if answer:
        model = answer

    print()
    return provider_type, base_url, api_key, model


def _apply_api_config(
    root: Path,
    name: str,
    provider_type: str,
    base_url: str,
    api_key: str,
    model: str,
) -> None:
    """将 API 配置写入 user_config.json"""
    config_path = root / "users" / name / "user_config.json"
    config = _read_json(config_path)
    provider = config.setdefault("provider", {})
    provider["type"] = provider_type
    if base_url:
        provider["base_url"] = base_url
    if api_key:
        provider["api_key"] = api_key
    if model:
        provider["model"] = model
    _write_json(config_path, config)
    print("  ✓ 配置已写入\n")


# ── 编辑用户 ──


def edit_user_api_config(root: Path | None = None, name: str = "") -> None:
    """交互式编辑用户的 Provider 配置。

    Args:
        root: 项目根目录
        name: 用户名；为空则交互选择
    """
    if root is None:
        root = _project_root()
    root = root.resolve()

    if not name:
        users = _list_users(root)
        if not users:
            print("  没有可编辑的用户\n")
            return
        name = _pick_user(users, "编辑")
        if not name:
            print("  → 已取消\n")
            return

    _validate_name(name)
    config_path = root / "users" / name / "user_config.json"
    if not config_path.is_file():
        print(f"  用户 {name} 的配置文件不存在\n")
        return

    config = _read_json(config_path)
    provider = config.get("provider", {})

    print(f"\n  当前配置 ({name}):")
    print(f"    Provider: {provider.get('type', 'kemo')}")
    print(f"    Base URL: {provider.get('base_url', '(默认读环境变量)')}")
    print(f"    Model:    {provider.get('model', '(默认)')}")
    print()

    provider_type, base_url, api_key, model = _guide_api_config(
        provider_type=provider.get("type", "kemo"),
        base_url=provider.get("base_url", ""),
        model=provider.get("model", ""),
    )
    _apply_api_config(root, name, provider_type, base_url, api_key, model)


# ── 删除用户 ──


def delete_user(
    root: Path | None = None,
    name: str = "",
    *,
    confirm: bool = False,
) -> bool:
    """删除用户及所有数据。

    Args:
        root: 项目根目录
        name: 用户名；为空则交互选择
        confirm: 是否跳过交互确认（调用方自行保证安全）

    Returns:
        是否成功删除
    """
    if root is None:
        root = _project_root()
    root = root.resolve()

    if not name:
        users = _list_users(root)
        if not users:
            print("  没有可删除的用户\n")
            return False
        name = _pick_user(users, "删除")
        if not name:
            print("  → 已取消\n")
            return False

    _validate_name(name)
    user_dir = root / "users" / name
    if not user_dir.is_dir():
        raise FileNotFoundError(f"用户不存在：{name}")

    if not confirm:
        print(f"\n  ⚠  将永久删除用户 {name} 的所有数据:")
        print("      - 对话历史")
        print("      - 记忆碎片")
        print("      - 知识库")
        print("      - 定时任务")
        print("      - 配置文件")
        print()
        try:
            answer = input("  输入用户名确认删除: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  → 已取消\n")
            return False
        if answer != name:
            print("  → 已取消\n")
            return False

    shutil.rmtree(user_dir)
    return True


# ── 交互菜单 ──


def _pick_user(users: list[str], action: str) -> str | None:
    """让用户从列表中选一个，返回用户名或 None"""
    while True:
        try:
            raw = input(f"  选择要{action}的用户 (1-{len(users)}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            return None
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(users):
                return users[idx]
        except ValueError:
            pass
        print(f"  请输入 1-{len(users)}")


def _interactive_main(root: Path) -> None:
    """交互式用户管理主循环"""
    print()
    print("  ═" + "═" * 42)
    print("      kemo-agent 用户管理")
    print("  ═" + "═" * 42)

    while True:
        users = _list_users(root)
        print()
        if users:
            print(f"  已有用户 ({len(users)}):")
            for i, name in enumerate(users, 1):
                print(f"    {i}. {name}")
        else:
            print("  已有用户: (无)")

        print()
        print("  " + "─" * 42)
        print("  [n] 新建用户")
        if users:
            print("  [e] 编辑用户的 API 配置")
            print("  [d] 删除用户")
        print("  [q] 退出")
        print("  " + "─" * 42)

        try:
            choice = input("\n  请选择: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  已退出\n")
            return

        if choice == "q":
            print("  已退出\n")
            return

        if choice == "n":
            _menu_create(root)

        elif choice == "e" and users:
            name = _pick_user(users, "编辑")
            if name:
                try:
                    edit_user_api_config(root, name)
                except Exception as exc:
                    print(f"  ✗ {exc}\n")

        elif choice == "d" and users:
            name = _pick_user(users, "删除")
            if name:
                try:
                    if delete_user(root, name, confirm=False):
                        print(f"  ✓ 用户 {name} 已删除\n")
                except Exception as exc:
                    print(f"  ✗ {exc}\n")

        else:
            print(f"  无效选项: {choice}")


def _menu_create(root: Path) -> None:
    """新建用户的完整交互流程"""
    try:
        raw = input("\n  用户名: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  → 已取消")
        return
    if not raw:
        print("  → 已取消")
        return

    try:
        name = _validate_name(raw)
    except ValueError as exc:
        print(f"  ✗ {exc}")
        return

    dest = root / "users" / name
    if dest.exists():
        try:
            answer = input(f"  用户 {name} 已存在，是否覆盖? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  → 已取消")
            return
        if answer != "y":
            print("  → 已取消")
            return

    try:
        create_user(name, root, interactive=True, overwrite=dest.exists())
    except (ValueError, FileNotFoundError) as exc:
        print(f"  ✗ {exc}")
        return

    print(f"  ✅ 用户 {name} 创建成功")
    print(f"  路径: {dest}")
    print("")
    print(f"  配置文件:  users/{name}/user_config.json")
    print(f"  人格文件:  users/{name}/user_soul.md")
    print(f"  知识库:    users/{name}/knowledge/")
    print(f"  记忆目录:  users/{name}/improve/")
    print(
        "  结束音效:  未设置；Windows 桌面网页端上传后生成 "
        f"users/{name}/completion_sound.*"
    )


# ── CLI 入口 ──


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="kemo-agent 用户创建与管理",
    )
    parser.add_argument(
        "name",
        nargs="?",
        help="静默创建用户名（不提供则进入交互菜单）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已有用户",
    )
    args = parser.parse_args(argv)
    root = _project_root()

    if args.name:
        # 静默模式
        try:
            dest = create_user(args.name, root, overwrite=args.overwrite)
            print(f"用户 {args.name} 创建成功: {dest}")
            return 0
        except FileExistsError:
            print(
                f"错误: 用户 {args.name} 已存在，使用 --overwrite 覆盖", file=sys.stderr
            )
            return 1
        except (ValueError, FileNotFoundError) as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 1
    else:
        # 交互菜单
        _interactive_main(root)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
