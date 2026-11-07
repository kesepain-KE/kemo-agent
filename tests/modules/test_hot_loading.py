import os
import py_compile

from plugins.expand_creater.tool import run as create_expand
from run.config import PromptSourceRegistry
from run.extensions import invoke_expand, run_module_updater
from run.modules import Piece, collect, write_manifest


def test_collection_keeps_warnings_offsets_order_and_attribution():
    pieces = [Piece("global:a", "global", "a", data="A" * 200),
              Piece("global:b", "global", "b", data="B" * 5000,
                    health="异常", recent_update="2026-11-07 12:00:00")]
    full = collect(pieces, 10000)
    assert not full.truncated
    assert full.original_chars == len(full.text)
    clipped = collect(pieces, 550)
    assert clipped == collect(pieces, 550)
    assert len(clipped.text) <= 550
    assert clipped.clipped and not clipped.dropped
    assert "异常" in clipped.fragment("global:b")
    assert "2026-11-07 12:00:00" in clipped.fragment("global:b")
    for placement in clipped.placed:
        assert clipped.text[placement.start:placement.end] == clipped.fragment(placement.key)
        assert placement.end - placement.start >= 120
    dropped = collect(pieces, 200)
    assert dropped.dropped == (("global:b", "budget_exceeded"),)
    assert [placement.key for placement in dropped.placed] == ["global:a"]
    empty = collect(pieces, 0)
    assert not empty.text and empty.original_items == 2
    assert [key for key, _ in empty.dropped] == ["global:a", "global:b"]


def test_data_fragment_excludes_control_and_respects_budget():
    piece = Piece("user:a", "user", "a", data="DATA" * 100, capability="CONTROL" * 50)
    for budget in (0, 130, 450, 900):
        result = collect([piece], budget)
        data = result.data_fragment("user:a")
        assert "CONTROL" not in data
        assert data in result.text
        assert len(result.text) <= budget


def test_registry_discovers_edits_new_modules_and_removals_without_recreation(tmp_path):
    base = tmp_path / "sense"
    base.mkdir()
    registry = PromptSourceRegistry(tmp_path, "alice")
    registry.add_perception(base)
    assert not registry.select_perception(max_chars=1000).text
    module = base / "sensor"
    module.mkdir()
    (module / "update.py").write_text("def update(): return True", "utf-8")
    data = module / "data.md"
    data.write_text("FIRST", "utf-8")
    payload = {"kind": "sense", "name": "Sensor", "explain": "live data",
               "health": "正常", "input_open": True, "control_open": False,
               "input_file": "data.md", "update_script": "update.py"}
    write_manifest(module / "module.yaml", payload)
    assert "FIRST" in registry.select_perception(max_chars=1000).text
    data.write_text("SECOND", "utf-8")
    payload["health"] = "异常"
    write_manifest(module / "module.yaml", payload)
    assert "SECOND" in registry.select_perception(max_chars=1000).text
    assert not registry.select_perception(max_chars=1000, allow_modules=()).text
    payload["input_open"] = False
    write_manifest(module / "module.yaml", payload)
    assert not registry.select_perception(max_chars=1000).text
    module.rename(base / ".removed")
    assert not registry.perception_inventory()


def test_next_control_and_update_load_equal_size_same_timestamp_edits(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.yaml").write_text("{}", "utf-8")
    user = tmp_path / "users/alice"
    user.mkdir(parents=True)
    (user / "user_config.yaml").write_text("{}", "utf-8")
    created = create_expand("create", "user", name="live", explain="live module",
        injection="Live action", operations="Call read", context={"root": str(tmp_path), "user": "alice"})
    module = tmp_path / created["path"]
    # Seed stale bytecode for both an entry and its imported implementation.
    dependency = module / "implementation.py"
    dependency.write_text("VALUE = 'OLD'\n", "utf-8")
    control = module / "start_expand.py"
    control.write_text("from implementation import VALUE\ndef execute(command, params): return VALUE\n", "utf-8")
    for path in (dependency, control):
        py_compile.compile(str(path), doraise=True)
    before = dependency.stat()
    dependency.write_text("VALUE = 'NEW'\n", "utf-8")
    os.utime(dependency, ns=(before.st_atime_ns, before.st_mtime_ns))
    result = invoke_expand(root=tmp_path, user="alice", scope="user", module="live",
                           command="read", params={}, timeout=5)
    assert result["result"] == "NEW"
    updater = module / "data_update.py"
    updater.write_text("from implementation import VALUE\ndef update(): return VALUE\n", "utf-8")
    assert run_module_updater(updater, module, timeout=5)["result"] == "NEW"


def test_unreadable_expand_root_is_diagnosed_without_stopping_other_scopes(tmp_path, monkeypatch):
    from pathlib import Path

    global_root = tmp_path / "expand/global"
    user_root = tmp_path / "users/alice/expand"
    global_root.mkdir(parents=True)
    user_root.mkdir(parents=True)
    created = create_expand(
        "create", "user", name="live", explain="user capability",
        injection="Available", operations="Read status",
        context={"root": str(tmp_path), "user": "alice"},
    )
    registry = PromptSourceRegistry(tmp_path, "alice")
    registry.add_expand_root("global", global_root)
    registry.add_user_expands(user_root)
    original = Path.iterdir

    def scan(path):
        if path == global_root:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", scan)
    selection = registry.select_expand(max_chars=5000)
    assert "user:live" in selection.text
    assert (tmp_path / created["path"]).is_dir()
    diagnostics = registry.selection_diagnostics()["expand"]["global"]
    assert diagnostics["scan_errors"] == ["global 目录不可读"]
    assert not diagnostics["discovered"]
