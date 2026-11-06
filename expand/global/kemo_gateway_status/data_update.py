"""Cron collection entry for the Kemo gateway status extension."""

from __future__ import annotations

import json
import sys

from gateway_status import update_snapshot


def update():
    result = update_snapshot()
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    return result


def main():
    return update()


if __name__ == "__main__":
    outcome = update()
    raise SystemExit(0 if outcome.get("ok") else 1)

