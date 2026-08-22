"""kemo-agent Web UI 启动入口

用法:
    python start_web.py              # 读取 WEB_HOST/WEB_PORT，端口冲突自动轮询（最多10次）
    python start_web.py --port=8080  # 自定义端口
    python start_web.py --host=0.0.0.0 --port=1357  # 开放局域网访问

启动时自动加载 .env、打印版本信息、检查用户，然后拉起 RuntimeHost（Cron + 消息路由）
和 Web 后端，一站式运行。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from run.config import load_dotenv, project_root
from run.scheduler import build_host
from run.config import ensure_user, list_users
from web.auth import WebAuthConfig, WebAuthConfigError


# ── 版本信息 ──────────────────────────────────────────────────────

def _read_version_json(root: Path) -> dict | None:
    path = root / "version.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _print_banner(root: Path) -> None:
    """Print a startup banner from version.json."""
    data = _read_version_json(root)
    if data is None:
        print("kemo-agent (version.json 缺失或损坏)")
        return

    agent_ver = data.get("version", "?")
    components = data.get("components", {})

    BAR = "─" * 46
    title = f"  kemo-agent  {agent_ver}"
    print(f"┌{BAR}┐")
    print(f"│{title:<46s}│")
    print(f"├{BAR}┤")
    for name, info in components.items():
        if isinstance(info, dict):
            ver = info.get("version", "?")
            cnt = info.get("count")
            extra = ""
            if cnt is not None:
                extra = f" {cnt} items" if cnt > 0 else " (empty)"
            line = f"  {name:<18s} {ver:<10s}{extra}"
            print(f"│{line:<46s}│")
    print(f"└{BAR}┘")


# 远程 version.json 镜像源列表（按优先级依次尝试）
_VERSION_URLS = [
    "https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/version.json",
    "https://ghproxy.net/https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/version.json",
    "https://gh.con.sh/https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/version.json",
    "https://mirror.ghproxy.com/https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/version.json",
]

# 版本检查缓存时长（秒）
_CACHE_TTL = 3600


def _cache_path(root: Path) -> Path:
    return root / "tmp" / "version-check-cache.json"


def _read_cache(root: Path) -> dict | None:
    path = _cache_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    cached_at = data.get("cached_at", 0)
    if not isinstance(cached_at, (int, float)):
        return None
    if time.time() - cached_at > _CACHE_TTL:
        return None
    return data.get("remote")


def _write_cache(root: Path, remote_data: dict) -> None:
    path = _cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = {"cached_at": time.time(), "remote": remote_data}
    path.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")


def _build_opener(timeout: float) -> urllib.request.OpenerDirector:
    """Build an opener with proxy from env vars."""
    handlers: list = []
    for scheme, env_var in (("http", "HTTP_PROXY"), ("https", "HTTPS_PROXY")):
        proxy = os.getenv(env_var, "").strip()
        if proxy:
            handlers.append(urllib.request.ProxyHandler({scheme: proxy}))
    opener = urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()
    # 不覆盖全局，只返回局部 opener
    return opener


def _fetch_remote(timeout: float) -> dict | None:
    """Try each mirror URL in order, return parsed JSON or None."""
    opener = _build_opener(timeout)
    last_error: str | None = None
    for url in _VERSION_URLS:
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "kemo-agent-startup"},
            )
            with opener.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            last_error = str(exc)
            continue
    if last_error:
        print(f"  (版本检查: 所有镜像源均失败)", file=sys.stderr)
    return None


def _check_version(root: Path, *, timeout: float = 5.0) -> str | None:
    """Compare local version.json with remote main branch. Returns a hint string or None."""
    local_data = _read_version_json(root)
    if local_data is None:
        return None

    # 先读缓存
    remote_data = _read_cache(root)
    if remote_data is None:
        remote_data = _fetch_remote(timeout)
        if remote_data is not None:
            _write_cache(root, remote_data)
        else:
            return None  # 网络不通静默跳过

    def _cmp(a: str, b: str) -> int:
        try:
            pa = tuple(int(x) for x in str(a).strip().split("."))
            pb = tuple(int(x) for x in str(b).strip().split("."))
        except ValueError:
            return 0
        if pa < pb: return -1
        if pa > pb: return 1
        return 0

    local_ver = str(local_data.get("version", ""))
    remote_ver = str(remote_data.get("version", ""))
    local_comp = local_data.get("components", {})
    remote_comp = remote_data.get("components", {})

    outdated: list[str] = []
    for name in ("core", "agents", "plugins", "web"):
        lv = str(local_comp.get(name, {}).get("version", ""))
        rv = str(remote_comp.get(name, {}).get("version", ""))
        if lv and rv and _cmp(lv, rv) < 0:
            outdated.append(f"{name} ({lv}→{rv})")

    if not outdated and _cmp(local_ver, remote_ver) >= 0:
        return f"✅ 已是最新版本 ({local_ver})"

    if not outdated:
        return None

    lines = [f"⚠ 发现更新: {local_ver} → {remote_ver}"]
    lines.append(f"  更新板块: {', '.join(outdated)}")
    lines.append(f"  运行 python update.py 更新")
    return "\n".join(lines)


def _version_check_thread(root: Path) -> None:
    """Background thread: fetch remote version and print result."""
    try:
        hint = _check_version(root, timeout=5.0)
        if hint:
            print(hint)
    except Exception:
        pass  # 任何异常都不影响启动

# ------------------------------------------------------------------------------------------
# 帮手
# ------------------------------------------------------------------------------------------


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


def _resolve_web_host(cli_value: str | None) -> str:
    value = cli_value if cli_value is not None else os.getenv("WEB_HOST", "")
    return str(value).strip() or "127.0.0.1"


def _resolve_web_port(cli_value: int | None) -> int:
    raw: object = cli_value if cli_value is not None else os.getenv("WEB_PORT", "")
    if raw in {None, ""}:
        return 1357
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("WEB_PORT 必须是整数") from exc
    if port < 1 or port > 65535:
        raise ValueError("Web 监听端口必须在 1–65535 之间")
    return port


def _check_users(root: Path) -> bool:
    """Ensure at least one user exists.  Try _template bootstrap first,
    then prompt interactively.  Returns True when ready."""
    users = list_users(root)
    if users:
                # 确保所有现有用户都有完整的目录框架。
        for name in users:
            try:
                ensure_user(name, root)
            except Exception as exc:
                print(f"警告: 补齐用户 {name} 目录骨架失败: {exc}")
        return True

        # 根本没有用户 → 尝试使用默认名称从 _template 引导。
    print("\n" + "=" * 50)
    print("  欢迎使用 kemo-agent！")
    print("  检测到没有用户。")
    print("=" * 50)

        # 如果 _template 存在，则请求用户名和引导程序。
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


# ------------------------------------------------------------------------------------------
# 主要的
# ------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="启动 kemo-agent Web 后端 + RuntimeHost",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Web 监听地址（默认读取 WEB_HOST，最后回退 127.0.0.1）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Web 监听端口（默认读取 WEB_PORT，最后回退 1357）",
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
    parser.add_argument(
        "--skip-version-check",
        action="store_true",
        help="跳过启动时的版本更新检查",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = project_root().resolve()

        # 1. 加载 .env 并打印版本信息
    load_dotenv(root / ".env")

    try:
        web_host = _resolve_web_host(args.host)
        base_port = _resolve_web_port(args.port)
        auth_config = WebAuthConfig.from_env()
    except (ValueError, WebAuthConfigError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    _print_banner(root)

        # 2. 后台检查版本更新（除非 --skip-version-check）
    if not args.skip_version_check:
        threading.Thread(
            target=_version_check_thread,
            args=(root,),
            daemon=True,
            name="version-check",
        ).start()

        # 3. 检查用户
    if not _check_users(root):
        return 1

        # 4. 端口轮询：1357 + 0..9（最多 10 次尝试）
    max_tries = 10
    chosen_port: int | None = None
    for offset in range(max_tries):
        try_port = base_port + offset
        ok, err = _can_bind(web_host, try_port)
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

        # 5.启动RuntimeHost（除非--no-host）
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

        # 6.启动uvicorn
    try:
        import uvicorn
    except ImportError:
        print("ERROR: 缺少 uvicorn，无法启动 Web 后端", file=sys.stderr)
        print("  pip install uvicorn", file=sys.stderr)
        if host:
            host.stop()
        return 1

    from web.app import create_app
    from web.service import WebRunService

    service = WebRunService(
        root,
        runtime_status_provider=host.status if host is not None else None,
        summary_waker=host.history_summaries.wake if host is not None else None,
        message_health_checker=host.check_message_transport if host is not None else None,
        message_transport_remover=host.remove_message_transport if host is not None else None,
        plan_waker=host.task_plans.wake if host is not None else None,
        router_ref=host.router if host is not None else None,
    )
    app = create_app(root=root, service=service, auth_config=auth_config)

        # 优雅关闭：进程退出时停止 RuntimeHost。
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

    print(f"Web 后端 → http://{web_host}:{chosen_port}")
    try:
        uvicorn.run(
            app,
            host=web_host,
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
