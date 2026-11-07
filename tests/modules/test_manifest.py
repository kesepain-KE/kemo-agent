from pathlib import Path

import pytest
import yaml

from run.modules import ModuleSpecError, locate_modules, read_manifest, write_manifest


@pytest.fixture
def module(tmp_path):
    directory = tmp_path / "sense" / "sensor"
    directory.mkdir(parents=True)
    (directory / "update.py").write_text("def update(): return True\n", "utf-8")
    (directory / "data.md").write_text("observed data", "utf-8")
    write_manifest(directory / "module.yaml", {
        "kind": "sense", "name": "sensor", "explain": "An observable sensor",
        "health": "正常", "update_script": "update.py",
        "input_open": True, "input_file": "data.md", "control_open": False,
    })
    return directory


def test_optional_timestamp_and_disabled_files(module):
    spec = read_manifest(module, kind_hint="sense", scope="global")
    assert spec.recent_update is None and spec.input_file == module / "data.md"
    payload = yaml.safe_load((module / "module.yaml").read_text("utf-8"))
    payload.update(input_open=False, input_file=None)
    write_manifest(module / "module.yaml", payload)
    assert read_manifest(module, kind_hint="sense", scope="global").input_file is None


@pytest.mark.parametrize(("change", "reason", "field"), [
    ({"kind": "expand"}, "kind_mismatch", "kind"),
    ({"kind": []}, "bad_type", "kind"),
    ({"kind": "other"}, "bad_enum", "kind"),
    ({"explain": ""}, "bad_type", "explain"),
    ({"name": None}, "bad_type", "name"),
    ({"input_open": "true"}, "bad_type", "input_open"),
    ({"control_open": 1}, "bad_type", "control_open"),
    ({"health": []}, "bad_type", "health"),
    ({"health": "unknown"}, "bad_enum", "health"),
    ({"recent_update": 20261107}, "bad_type", "recent_update"),
    ({"recent_update": "yesterday"}, "bad_type", "recent_update"),
    ({"control_open": True}, "bad_enum", "control_open"),
    ({"input_file": "../data.md"}, "path_escape", "input_file"),
    ({"input_file": r"C:\data.md"}, "path_escape", "input_file"),
    ({"input_file": "data.txt"}, "bad_suffix", "input_file"),
    ({"input_file": "missing.md"}, "file_missing", "input_file"),
    ({"update_script": "missing.py"}, "file_missing", "update_script"),
    ({"input_file": None}, "missing_field", "input_file"),
    ({"depends_on": []}, "unknown_field", None),
])
def test_invalid_manifest_has_one_attributable_error(module, change, reason, field):
    payload = yaml.safe_load((module / "module.yaml").read_text("utf-8"))
    payload.update(change)
    write_manifest(module / "module.yaml", payload)
    with pytest.raises(ModuleSpecError) as caught:
        read_manifest(module, kind_hint="sense", scope="global")
    assert (caught.value.error.reason, caught.value.error.field) == (reason, field)
    assert str(module) not in str(caught.value)


@pytest.mark.parametrize(("text", "reason"), [
    ("[", "unreadable"), ("[]", "not_object"),
    ("{}", "missing_field"), ("kind: sense\nkind: sense", "unreadable"),
])
def test_malformed_yaml(module, text, reason):
    (module / "module.yaml").write_text(text, "utf-8")
    with pytest.raises(ModuleSpecError) as caught:
        read_manifest(module, kind_hint="sense", scope="global")
    assert caught.value.error.reason == reason


def test_json_filename_is_not_a_manifest(module):
    (module / "module.yaml").rename(module / "sense.json")
    located = locate_modules(module.parents[1], "alice", kind="sense")
    assert not located.specs
    assert located.errors[0][1].reason == "missing_manifest"


def test_expand_enabled_file_contract(module):
    payload = yaml.safe_load((module / "module.yaml").read_text("utf-8"))
    payload.update(kind="expand", input_open=False)
    write_manifest(module / "module.yaml", payload)
    with pytest.raises(ModuleSpecError, match="bad_enum"):
        read_manifest(module, kind_hint="expand", scope="user")
    payload.update(control_open=True, control_script="control.py", control_entry="control.md")
    (module / "control.py").write_text("def execute(command, params): return True", "utf-8")
    (module / "control.md").write_text("## 注入层\nAn action\n## 操作层\nUsage", "utf-8")
    write_manifest(module / "module.yaml", payload)
    spec = read_manifest(module, kind_hint="expand", scope="user")
    assert spec.control_script == module / "control.py"
    (module / "control.md").unlink()
    with pytest.raises(ModuleSpecError, match="file_missing:control_entry"):
        read_manifest(module, kind_hint="expand", scope="user")


def test_manifest_file_link_cannot_escape_module(module, tmp_path):
    target = tmp_path / "outside.md"
    target.write_text("outside", "utf-8")
    (module / "data.md").unlink()
    try:
        (module / "data.md").symlink_to(target)
    except OSError:
        pytest.skip("This OS account cannot create symbolic links")
    with pytest.raises(ModuleSpecError, match="path_escape"):
        read_manifest(module, kind_hint="sense", scope="global")
