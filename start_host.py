"""Long-running kemo-agent background host entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import threading
from typing import Any

from message.transport import TransportPolicy
from run.config import load_dotenv, project_root
from run.runtime_host import build_host


VERSION = "0.1.0-dev"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kemo-agent-host",
        description="运行 kemo-agent 后台宿主（Cron + 外部消息路由）",
    )
    parser.add_argument("--root", help="项目根目录，默认自动识别")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="注册内存 MockTransport，仅用于本地诊断",
    )
    parser.add_argument(
        "--mock-tools",
        help="MockTransport 允许的工具名，逗号分隔；空值表示不额外限制",
    )
    parser.add_argument("--status-json", action="store_true", help="启动后输出 JSON 状态")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def _policy(value: str | None) -> TransportPolicy | None:
    if value is None:
        return None
    names = frozenset(item.strip() for item in value.split(",") if item.strip())
    return TransportPolicy(allowed_tools=names)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else project_root().resolve()
    load_dotenv(root / ".env")
    host = build_host(root, include_mock=args.mock, mock_policy=_policy(args.mock_tools))
    stop_requested = threading.Event()

    def request_stop(signum: int, frame: Any) -> None:
        stop_requested.set()
        host.stop()

    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[signum] = signal.signal(signum, request_stop)
        except (ValueError, OSError):
            pass

    try:
        host.start()
        if args.status_json:
            print(json.dumps(host.status(), ensure_ascii=False))
        else:
            names = ", ".join(host.registry.names()) or "无"
            print(
                f"kemo-agent RuntimeHost 已启动 | transports={names} | "
                f"cron={'on' if host.cron_enabled else 'off'}"
            )
        while host.running and not stop_requested.wait(0.5):
            pass
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"RuntimeHost 启动失败：{exc}", file=sys.stderr)
        return 1
    finally:
        host.stop()
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
