"""Discovery and isolated probes must use the same local-import contract."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from message.plugin_contracts import MessagePluginError, _load_module


def test_transport_helpers_are_directory_local_without_sys_path_mutation(tmp_path: Path):
    before_path = sys.path[:]
    before_util = sys.modules.get("util")
    for name in ("first", "second"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "util.py").write_text(f"VALUE = {name!r}\n", encoding="utf-8")
        entry = directory / "output.py"
        entry.write_text("from .util import VALUE\ndef send(payload): return VALUE\n", encoding="utf-8")
        loaded = _load_module(entry, "same-machine", "output")
        assert loaded.send({}) == name
    assert sys.path == before_path
    assert sys.modules.get("util") is before_util


def test_import_failure_does_not_leave_partial_entry_module(tmp_path: Path):
    entry = tmp_path / "output.py"
    entry.write_text("raise RuntimeError('injected')\n", encoding="utf-8")
    with pytest.raises(MessagePluginError, match="injected"):
        _load_module(entry, "test-machine", "output")
    assert not any(getattr(module, "__file__", None) == str(entry) for module in list(sys.modules.values()))
