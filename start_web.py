"""kemo-agent Web UI 启动入口

用法:
    python start_web.py              # 默认端口 1357，冲突自动轮询（最多10次）
    python start_web.py --port=8080  # 自定义端口
    python start_web.py --host=0.0.0.0 --port=1357  # 开放局域网访问

启动时自动加载 .env、检查用户，然后拉起 RuntimeHost（Cron + 消息路由）
和 Web 后端，一站式运行。
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import sys
import threading
from pathlib import Path
from typing import Any

from run.config import load_dotenv, project_root
from run.runtime_host import build_host
from run.users import ensure_user, list_users

VERSION = "0.1.0-dev"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _can_bind(host: str, port: int) -> tuple[bool, str]:
    """Try to bind a probe socket.  Returns (ok, error-message)."""
    probe_host = host if host not in ("", "*") else "0.0.0.0"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((probe_host, port))
        return True, ""
    except OSError as exc:
        return False, str(exc)
    finally:
        sock.close()


def _check_users(root: Path) -> bool:
    """Ensure at least one user exists.  Try _template bootstrap first,
    then prompt interactively.  Returns True when ready."""
    users = list_users(root)
    if users:
        # Ensure all existing users have complete directory skeletons.
        for name in users:
            try:
                ensure_user(name, root)
            except Exception as exc:
                print(f"警告: 补齐用户 {name} 目录骨架失败: {exc}")
        return True

    # No users at all → try to bootstrap from _template with a default name.
    print("\n" + "=" * 50)
    print("  欢迎使用 kemo-agent！")
    print("  检测到没有用户。")
    print("=" * 50)

    # If _template exists, ask for a username and bootstrap.
    template = root / "users" / "_template"
    if template.is_dir():
        try:
            name = input("请输入用户名: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return False
        if not name:
            print("用户名为空，已取消。")
            return False
        try:
            ensure_user(name, root)
            print(f"\n用户 {name} 创建成功，正在启动...\n")
            return True
        except Exception as exc:
            print(f"\n用户创建失败: {exc}")
            return False
    else:
        print("用户模板 _template 不存在，无法自动创建用户。")
        print(f"请先在 {root / 'users' / '_template'} 放置用户模板。")
        return False


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="启动 kemo-agent Web 后端 + RuntimeHost",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Web 监听地址（默认 127.0.0.1）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1357,
        help="Web 监听端口（默认 1357，冲突自动轮询最多 10 次）",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        help="uvicorn 日志等级（默认 info）",
    )
    parser.add_argument(
        "--no-host",
        action="store_true",
        help="不启动 RuntimeHost（Cron + 消息路由），仅 Web API",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = project_root().resolve()

    # 1. load .env
    load_dotenv(root / ".env")

    # 2. check users
    if not _check_users(root):
        return 1

    # 3. port polling: 1357 + 0..9 (max 10 tries)
    base_port = args.port
    max_tries = 10
    chosen_port: int | None = None
    for offset in range(max_tries):
        try_port = base_port + offset
        ok, err = _can_bind(args.host, try_port)
        if ok:
            chosen_port = try_port
            if offset > 0:
                print(f"端口 {base_port} 不可用，已切换到 {try_port}")
            break
        if offset == 0:
            print(f"端口 {try_port} 不可用，正在轮询... ({err})")
    if chosen_port is None:
        print(
            f"ERROR: 端口 {base_port}~{base_port + max_tries - 1} 全部被占用，无法启动"
        )
        return 1

    # 4. start RuntimeHost (unless --no-host)
    host = None
    if not args.no_host:
        host = build_host(root)
        try:
            host.start()
            transport_names = ", ".join(host.registry.names()) or "无"
            print(
                f"RuntimeHost 已启动 | transports={transport_names} | "
                f"cron={'on' if host.cron_enabled else 'off'}"
            )
        except Exception as exc:
            print(f"RuntimeHost 启动失败: {exc}", file=sys.stderr)
            return 1

    # 5. start uvicorn
    try:
        import uvicorn
    except ImportError:
        print("ERROR: 缺少 uvicorn，无法启动 Web 后端", file=sys.stderr)
        print("  pip install uvicorn", file=sys.stderr)
        if host:
            host.stop()
        return 1

    from web.app import create_app

    app = create_app(root=root)

    # Graceful shutdown: stop RuntimeHost when process exits.
    def _shutdown() -> None:
        if host is not None:
            host.stop()

    def _signal_handler(signum: int, frame: Any) -> None:
        _shutdown()
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            pass

    print(f"Web 后端 → http://{args.host}:{chosen_port}")
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=chosen_port,
            log_level=args.log_level,
        )
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
