"""Independent startup entry for the kemo-agent Web API."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from run.config import project_root, read_json_object


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 kemo-agent Web 后端")
    parser.add_argument("--host", help="监听地址；默认读取 global_config.web.host")
    parser.add_argument("--port", type=int, help="监听端口；默认读取 global_config.web.port")
    parser.add_argument("--log-level", help="uvicorn 日志等级")
    return parser


def resolve_web_config(root: Path, args: argparse.Namespace) -> dict[str, object]:
    config = read_json_object(root / "config" / "global_config.json")
    web = config.get("web") or {}
    if not isinstance(web, dict):
        raise ValueError("global_config.web 必须是对象")
    host = str(args.host or web.get("host") or "127.0.0.1").strip()
    port = int(args.port or web.get("port") or 1478)
    log_level = str(args.log_level or web.get("log_level") or "info").strip().lower()
    if not host:
        raise ValueError("Web host 不能为空")
    if not 1 <= port <= 65535:
        raise ValueError("Web port 必须在 1–65535 之间")
    if log_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
        raise ValueError("Web log_level 无效")
    return {"host": host, "port": port, "log_level": log_level}


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    base = (root or project_root()).resolve()
    options = resolve_web_config(base, args)
    try:
        import uvicorn
    except ImportError:
        raise RuntimeError("缺少 uvicorn，无法启动 Web 后端") from None
    from web.app import create_app

    # create_app and health checks do not construct a Provider and do not start
    # RuntimeHost, CronScheduler or message transports.
    app = create_app(root=base)
    uvicorn.run(app, **options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
