"""Refresh only the local Kemo Graph registry catalog."""

from __future__ import annotations

import json

from render import refresh_catalog
from start_expand import write_panel_status


def update():
    result = refresh_catalog()
    try:
        write_panel_status()
    except Exception:
        pass
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    return result


def main():
    return update()


if __name__ == "__main__":
    outcome = update()
    raise SystemExit(0 if outcome.get("ok") else 1)
