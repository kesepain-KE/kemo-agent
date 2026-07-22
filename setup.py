"""kemo-agent 首次部署引导脚本

用法:
    python setup.py              # 交互式部署向导
    python setup.py --yes        # 跳过交互，全部使用默认值
    python setup.py --skip-deps  # 跳过 pip install
    python setup.py --skip-web   # 跳过前端构建
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# ═══════════════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════════════

def _title(text: str) -> None:
    print(f"\n  {'─' * 48}")
    print(f"  {text}")
    print(f"  {'─' * 48}")


def _ok(text: str) -> None:
    print(f"  ✓ {text}")


def _warn(text: str) -> None:
    print(f"  ⚠ {text}")


def _fail(text: str) -> None:
    print(f"  ✗ {text}")


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> bool:
    """执行命令，返回是否成功"""
    print(f"  $ {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=str(cwd or ROOT), check=check)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        _fail(str(exc))
        return False


def _command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _resolve_npm() -> str | None:
    cmd = shutil.which("npm")
    if not cmd:
        return None
    if os.name == "nt" and not cmd.lower().endswith(".exe"):
        cmd_path = cmd + ".cmd"
        if os.path.isfile(cmd_path):
            return cmd_path
    return cmd


# ═══════════════════════════════════════════════════════════════════
# 各步骤
# ═══════════════════════════════════════════════════════════════════

def _check_python() -> bool:
    """检查 Python 版本 ≥ 3.10"""
    ver = sys.version_info
    if ver >= (3, 10):
        _ok(f"Python {ver.major}.{ver.minor}.{ver.micro}")
        return True
    _fail(f"Python {ver.major}.{ver.minor}，需要 ≥ 3.10")
    return False


def _install_deps() -> bool:
    """pip install"""
    req = ROOT / "requirements.txt"
    if not req.is_file():
        _warn("requirements.txt 不存在，跳过")
        return True
    return _run([sys.executable, "-m", "pip", "install", "-r", str(req)])


def _setup_env(assume_yes: bool) -> bool:
    """创建 .env（不存在时从 .env.example 复制），可选引导填写 API Key"""
    env_path = ROOT / ".env"
    example_path = ROOT / ".env.example"

    if env_path.is_file():
        _ok(".env 已存在，跳过")
        return True

    if not example_path.is_file():
        _warn(".env.example 不存在，请手动创建 .env")
        return True

    shutil.copy(example_path, env_path)
    _ok(".env 已从 .env.example 创建")

    if assume_yes:
        return True

    # 引导填写关键配置
    print()
    try:
        answer = input("  是否需要现在填写 API Key? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return True
    if answer != "y":
        return True

    # KEMO_API_KEY
    try:
        key = getpass.getpass("  KEMO_API_KEY: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        key = ""
    if key:
        _replace_env(env_path, "KEMO_API_KEY", key, "your-kemo-api-key")

    # KEMO_BASE_URL
    url = input("  KEMO_BASE_URL (留空默认 127.0.0.1:8741): ").strip()
    if url:
        _replace_env(env_path, "KEMO_BASE_URL", url, "http://127.0.0.1:8741/v1")

    # WEB_USERNAME / WEB_PASSWORD
    web_user = input("  Web 登录用户名 (留空跳过): ").strip()
    if web_user:
        _replace_env(env_path, "WEB_USERNAME", web_user, "")
    if web_user:
        try:
            web_pass = getpass.getpass("  Web 登录密码: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            web_pass = ""
        if web_pass:
            _replace_env(env_path, "WEB_PASSWORD", web_pass, "")

    _ok("配置已写入 .env")
    return True


def _replace_env(path: Path, key: str, value: str, default: str) -> None:
    """替换 .env 中指定 key 的值"""
    lines = path.read_text("utf-8").splitlines(keepends=True)
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
            new_lines.append(f"{key}={value}\n")
        else:
            new_lines.append(line)
    path.write_text("".join(new_lines), "utf-8")


def _build_web(assume_yes: bool) -> bool:
    """构建 Web 前端"""
    web_dir = ROOT / "web" / "frontend"
    package_json = web_dir / "package.json"

    if not package_json.is_file():
        _ok("前端目录不存在，跳过 Web 构建")
        return True

    dist_dir = web_dir / "dist"
    if dist_dir.is_dir() and any(dist_dir.iterdir()):
        _ok("web/frontend/dist 已存在，跳过构建")
        return True

    npm = _resolve_npm()
    if not npm:
        if dist_dir.is_dir() and any(dist_dir.iterdir()):
            _ok("未找到 npm，但 dist 已存在，跳过")
            return True
        _warn("未找到 npm。请安装 Node.js 后手动构建：")
        print(f"    cd {web_dir}")
        print("    npm install && npm run build")
        return True

    _ok(f"npm: {npm}")
    if not _run([npm, "install"], cwd=web_dir):
        return False
    if not _run([npm, "run", "build"], cwd=web_dir):
        return False
    _ok("Web 前端构建完成")
    return True


def _create_first_user(assume_yes: bool) -> bool:
    """检查并创建第一个用户"""
    from user_create import _list_users

    users = _list_users(ROOT)
    if users:
        _ok(f"已有用户 ({len(users)}): {', '.join(users)}")
        return True

    _warn("没有用户，需要创建")
    if assume_yes:
        _ok("使用默认用户名 kesepain 创建...")
        try:
            from user_create import create_user
            create_user("kesepain", ROOT, interactive=False)
            _ok("用户 kesepain 已创建（使用默认配置）")
            return True
        except Exception as exc:
            _fail(str(exc))
            return False

    try:
        name = input("  用户名: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消")
        return True

    if not name:
        _warn("用户名为空，跳过。稍后运行 python user_create.py 创建")
        return True

    try:
        from user_create import create_user
        create_user(name, ROOT, interactive=True)
        _ok(f"用户 {name} 已创建")
        return True
    except Exception as exc:
        _fail(str(exc))
        return False


def _ensure_dirs() -> bool:
    """确保基础目录存在"""
    dirs = [
        ROOT / "tmp",
        ROOT / "users",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    _ok("tmp/ users/ 已就绪")
    return True


# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="kemo-agent 首次部署引导")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过交互，全部默认")
    parser.add_argument("--skip-deps", action="store_true", help="跳过 pip install")
    parser.add_argument("--skip-web", action="store_true", help="跳过前端构建")
    args = parser.parse_args(argv)

    print()
    print("  ═" + "═" * 48)
    print("      kemo-agent 部署向导")
    print("  ═" + "═" * 48)

    steps = [
        ("Python 环境", _check_python),
        ("安装依赖", lambda: args.skip_deps or _install_deps()),
        ("环境变量", lambda: _setup_env(args.yes)),
        ("前端构建", lambda: args.skip_web or _build_web(args.yes)),
        ("创建用户", lambda: _create_first_user(args.yes)),
        ("目录检查", _ensure_dirs),
    ]

    for i, (label, step) in enumerate(steps, 1):
        _title(f"[{i}/{len(steps)}] {label}")
        try:
            if not step():
                _fail(f"{label} 失败")
                print(f"\n  请解决上述问题后重新运行 python setup.py")
                return 1
        except KeyboardInterrupt:
            print("\n  已中断")
            return 130

    # ── 完成 ──
    print()
    print("  ═" + "═" * 48)
    print("      ✅ 部署完成！")
    print("  ═" + "═" * 48)
    print()
    print(f"  启动:    python start_web.py")
    print(f"  管理:    python user_create.py")
    print(f"  更新:    python update.py --check")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
