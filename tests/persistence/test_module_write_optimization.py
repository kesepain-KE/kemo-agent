from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from run.extensions import (
    ModuleRuntimeError,
    module_subprocess_environment,
    record_module_health,
    run_module_updater,
)


def test_healthy_module_manifest_is_not_rewritten_before_checkpoint(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "expand.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "demo",
                "input_health": "正常",
                "recent_update": "2026-08-16 12:00:00",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "utf-8",
    )
    before = (manifest.stat().st_mtime_ns, manifest.read_bytes())
    with patch("run.extensions.module_runtime.datetime") as clock:
        clock.now.return_value = __import__("datetime").datetime(
            2026,
            8,
            16,
            12,
            4,
            59,
            tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Shanghai"),
        )
        clock.strptime.side_effect = __import__("datetime").datetime.strptime
        record_module_health(manifest, "expand", healthy=True)
    assert (manifest.stat().st_mtime_ns, manifest.read_bytes()) == before


def test_module_health_transition_is_persisted_immediately(tmp_path: Path) -> None:
    manifest = tmp_path / "sense.json"
    manifest.write_text(
        json.dumps(
            {"name": "demo", "health": "正常", "recent_update": "2026-08-16 12:00:00"},
            ensure_ascii=False,
        ),
        "utf-8",
    )
    record_module_health(manifest, "sense", healthy=False)
    assert json.loads(manifest.read_text("utf-8"))["health"] == "异常"


def test_module_environments_are_independent_and_do_not_mutate_parent(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / ".env").write_text("MODULE_VALUE=first\n", "utf-8")
    (second / ".env").write_text("MODULE_VALUE=second\n", "utf-8")

    with patch.dict(
        os.environ,
        {"KEMO_API_KEY": "must-not-leak", "MODULE_VALUE": "parent"},
        clear=False,
    ):
        first_environment = module_subprocess_environment(first)
        second_environment = module_subprocess_environment(second)
        assert os.environ["MODULE_VALUE"] == "parent"

    assert first_environment["MODULE_VALUE"] == "first"
    assert second_environment["MODULE_VALUE"] == "second"
    assert "KEMO_API_KEY" not in first_environment
    assert "KEMO_API_KEY" not in second_environment


def test_invalid_module_dotenv_is_a_local_update_failure(tmp_path: Path) -> None:
    module = tmp_path / "module"
    module.mkdir()
    update_path = module / "update.py"
    update_path.write_text("def update():\n    return True\n", "utf-8")
    (module / ".env").write_text("not-an-assignment\n", "utf-8")

    result = run_module_updater(update_path, module, timeout=2)

    assert result["ok"] is False
    assert result["exception_type"] == "ModuleRuntimeError"
    assert "模块 .env 无效" in result["reason"]


def test_module_dotenv_rejects_unbounded_or_invalid_values(tmp_path: Path) -> None:
    module = tmp_path / "module"
    module.mkdir()
    dotenv = module / ".env"
    dotenv.write_bytes(b"VALUE=" + (b"x" * (256 * 1024)))

    with pytest.raises(ModuleRuntimeError, match="超过 .* 字节上限"):
        module_subprocess_environment(module)

    dotenv.write_bytes(b"VALUE=before\x00after\n")
    with pytest.raises(ModuleRuntimeError, match="包含空字符"):
        module_subprocess_environment(module)

    dotenv.write_text("VALUE=first\nVALUE=second\n", "utf-8")
    with pytest.raises(ModuleRuntimeError, match="重复定义变量"):
        module_subprocess_environment(module)
