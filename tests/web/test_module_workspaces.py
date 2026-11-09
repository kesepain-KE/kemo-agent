from __future__ import annotations

import asyncio
import io
import json
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import yaml

from run.modules import read_manifest
from web.app import create_app
from web.errors import ConflictError, InvalidRequestError
from web.service import WebRunService
from web.services.module_import import install_import, preview_import
from web.services.module_panels import module_panel_response, save_module_panel_values
from web.services.module_ui_instances import invoke_bridge, open_instance, static_asset
from web.services.module_workspace import ModuleWorkspaceService, WorkspaceForbidden
from web.services.module_workspace_schema import workspace_declaration


@pytest.fixture
def service(tmp_path: Path) -> ModuleWorkspaceService:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.yaml").write_text("{}", "utf-8")
    for user in ("alice", "bob"):
        (tmp_path / "users" / user).mkdir(parents=True)
        (tmp_path / "users" / user / "user_config.yaml").write_text("{}", "utf-8")
    for relative, body in (
        ("expand/global/register.py", 'registry.add_expand_root("global", Path(__file__).resolve().parent)'),
        ("expand/share/register.py", 'registry.add_expand_root("shared", Path(__file__).resolve().parent)'),
        ("sense/register.py", "registry.add_perception(Path(__file__).resolve().parent)"),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("from pathlib import Path\n\ndef register(registry):\n    " + body + "\n", "utf-8")
    return ModuleWorkspaceService(WebRunService(tmp_path))


def make_module(root: Path, *, kind: str = "expand", name: str = "demo", web: bool = True) -> Path:
    target = root / ("expand/global" if kind == "expand" else "sense") / name
    (target / "module").mkdir(parents=True)
    (target / "ui/dist/assets").mkdir(parents=True)
    manifest = {
        "kind": kind, "name": name, "explain": "真实测试模块", "health": "正常",
        "input_open": True, "input_file": "input.md", "control_open": kind == "expand",
        "update_script": "update.py",
    }
    if kind == "expand":
        manifest.update(control_script="control.py", control_entry="README.md")
    (target / "input.md").write_text("真实采集数据", "utf-8")
    (target / "README.md").write_text("# 模块说明", "utf-8")
    (target / "update.py").write_text(
        "from pathlib import Path\nimport json\n"
        "def update():\n"
        " p=Path(__file__).parent\n"
        " (p/'module/status.json').write_text(json.dumps({'health':'updated','private':'secret-status'}))\n"
        " return {'ok':True}\n", "utf-8",
    )
    (target / "control.py").write_text(
        "def execute(command, params, context=None):\n"
        " return {'value':params.get('value'), 'command':command, 'user':context['user']}\n", "utf-8",
    )
    panel = {
        "schema_version": 1, "title": "模块配置", "containers": [
            {"kind": "status", "title": "状态", "source": "module/status.json", "fields": [
                {"key": "health", "label": "健康", "type": "badge"},
                {"key": "private", "label": "密钥", "type": "text", "masked": True},
            ]},
            {"kind": "config", "title": "配置", "values": "module/panel.values.json", "fields": [
                {"key": "name", "label": "名称", "type": "string"},
                {"key": "secret", "label": "密钥", "type": "string", "masked": True},
            ]},
        ],
    }
    (target / "module/panel.json").write_text(json.dumps(panel), "utf-8")
    (target / "module/panel.values.json").write_text('{"name":"before","secret":"private-config"}', "utf-8")
    (target / "module/status.json").write_text('{"health":"ready","private":"private-status"}', "utf-8")
    if web:
        manifest["ui"] = {"workspace": "module/workspace.json"}
        if kind == "sense":
            manifest["ui"]["action_entry"] = "control.py"
        declaration = {
            "schema_version": 1, "default_page": "console",
            "pages": [{"id": "console", "label": "控制台", "type": "web", "entry": "ui/dist/index.html", "permissions": [
                "module.status.read", "module.config.read", "module.config.write", "module.action.invoke",
            ]}],
            "actions": [{"id": "echo", "label": "真实子进程", "handler": "echo", "input_schema": {
                "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"],
            }}],
        }
        (target / "module/workspace.json").write_text(json.dumps(declaration), "utf-8")
        (target / "ui/dist/index.html").write_text('<script type="module" src="./assets/app.js"></script>', "utf-8")
        (target / "ui/dist/assets/app.js").write_text("import '../__kemo__/bridge.js';", "utf-8")
    (target / "module.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True), "utf-8")
    return target


def grant(service: ModuleWorkspaceService, kind: str = "expand", module: str = "demo") -> dict:
    item, _ = service.describe("alice", kind, "global", module)
    service.set_permissions("alice", kind, "global", module, {
        "authorization_revision": item["authorization_revision"],
        "permissions": item["requested_permissions"],
    })
    return open_instance(service, "alice", kind, "global", module, "console")


@pytest.mark.parametrize("kind", ["expand", "sense"])
def test_panel_only_and_bad_workspace_keep_real_module_available(service: ModuleWorkspaceService, kind: str):
    target = make_module(service.root, kind=kind, web=False)
    runtime = {"schema_version": 1, "update": {"status": "failed", "error": {"message": "采集连接失败"}}}
    (target / "_runtime.json").write_text(json.dumps(runtime), "utf-8")
    item = service.inventory("alice", kind)["items"][0]
    assert {page["type"] for page in item["pages"]} == {"builtin", "panel"}
    assert any(page.get("view") == "logs" for page in item["pages"])
    assert item["runtime"] == runtime
    manifest = yaml.safe_load((target / "module.yaml").read_text("utf-8"))
    manifest["ui"] = {"workspace": "module/missing.json"}
    (target / "module.yaml").write_text(yaml.safe_dump(manifest), "utf-8")
    item = service.inventory("alice", kind)["items"][0]
    assert item["valid"] and item["ui_error"]
    assert any(page["type"] == "panel" for page in item["pages"])


@pytest.mark.parametrize("kind", ["expand", "sense"])
def test_bridge_authorizes_real_actions_and_masks_config(service: ModuleWorkspaceService, kind: str):
    make_module(service.root, kind=kind)
    with pytest.raises(WorkspaceForbidden):
        open_instance(service, "alice", kind, "global", "demo", "console")
    instance = grant(service, kind)
    token = instance["nonce"]
    status = invoke_bridge(service, token, {"method": "module.status.get", "params": {"module": "other", "user": "bob"}})
    assert status["result"]["module"]["module"] == "demo"
    assert "private-status" not in json.dumps(status)
    config = invoke_bridge(service, token, {"method": "module.config.get"})["result"]
    assert "private-config" not in json.dumps(config)
    result = invoke_bridge(service, token, {"method": "module.action.invoke", "params": {"action": "echo", "params": {"value": "真实执行"}}})["result"]
    if kind == "expand":
        result = result["result"]
    assert result == {"command": "echo", "value": "真实执行", "user": "alice"}
    with pytest.raises(InvalidRequestError):
        invoke_bridge(service, token, {"method": "module.action.invoke", "params": {"action": "echo", "params": {"value": "x", "shell": "bad"}}})
    with pytest.raises(InvalidRequestError):
        invoke_bridge(service, token, {"method": "module.action.invoke", "params": {"action": "undeclared"}})
    saved = invoke_bridge(service, token, {"method": "module.config.patch", "params": {
        "values": {"name": "after"}, "config_revision": config["config_revision"],
    }})["result"]
    assert saved["saved"]
    assert service.panel("alice", kind, "global", "demo")["panel"]["containers"][0]["data"]["health"] == "updated"
    with pytest.raises(ConflictError):
        invoke_bridge(service, token, {"method": "module.config.patch", "params": {
            "values": {"name": "overwrite"}, "config_revision": config["config_revision"],
        }})


def test_health_updates_keep_ui_but_asset_change_and_revoke_invalidate(service: ModuleWorkspaceService):
    target = make_module(service.root)
    token = grant(service)["nonce"]
    manifest = yaml.safe_load((target / "module.yaml").read_text("utf-8"))
    manifest.update(health="异常", recent_update="2026-11-09 12:00:00")
    (target / "module.yaml").write_text(yaml.safe_dump(manifest), "utf-8")
    assert static_asset(service, token, "index.html")[0]
    (target / "ui/dist/assets/app.js").write_text("console.log('updated')", "utf-8")
    with pytest.raises(ConflictError):
        static_asset(service, token, "assets/app.js")
    token = grant(service)["nonce"]
    item, _ = service.describe("alice", "expand", "global", "demo")
    service.set_permissions("alice", "expand", "global", "demo", {"permissions": [], "authorization_revision": item["authorization_revision"]})
    with pytest.raises(Exception, match="关闭|过期"):
        invoke_bridge(service, token, {"method": "module.status.get"})


def test_concurrent_panel_writes_compare_revision_under_lock(service: ModuleWorkspaceService):
    target = make_module(service.root)
    revision = module_panel_response(target, "expand")["config_revision"]
    def save(value: str):
        try:
            return save_module_panel_values(target, "expand", {"name": value}, expected_revision=revision)["config_revision"]
        except ConflictError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["one", "two"]))
    assert results.count("conflict") == 1


def archive_module(target: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in target.rglob("*"):
            if path.is_file() and not path.name.startswith("."):
                archive.writestr(f"{target.name}/{path.relative_to(target).as_posix()}", path.read_bytes())
    return buffer.getvalue()


def test_import_preview_install_and_update_preserve_runtime(service: ModuleWorkspaceService, tmp_path: Path):
    source = make_module(tmp_path / "package", name="installed")
    data = archive_module(source)
    preview = preview_import(service, "alice", "expand", "user", "installed.zip", data)
    assert preview["permissions"] and preview["entrypoints"] and not preview["updating"]
    result = install_import(service, "alice", preview["import_id"])
    target = service.root / "users/alice/expand/installed"
    assert result["installed"] and target.is_dir()
    (target / ".env").write_text("TOKEN=keep-local\n", "utf-8")
    (target / "module/panel.values.json").write_text('{"name":"keep-local","secret":"keep-secret"}', "utf-8")
    for root in (target, source):
        (root / "data").mkdir()
    (target / "data/record.json").write_text('{"value":"keep-collected"}', "utf-8")
    (source / "data/record.json").write_text('{"value":"package-default"}', "utf-8")
    (source / "ui/dist/assets/app.js").write_text("console.log('new version')", "utf-8")
    preview = preview_import(service, "alice", "expand", "user", "installed.zip", archive_module(source))
    install_import(service, "alice", preview["import_id"])
    assert (target / ".env").read_text("utf-8") == "TOKEN=keep-local\n"
    assert json.loads((target / "module/panel.values.json").read_text("utf-8"))["name"] == "keep-local"
    assert "new version" in (target / "ui/dist/assets/app.js").read_text("utf-8")
    assert json.loads((target / "data/record.json").read_text("utf-8"))["value"] == "keep-collected"


@pytest.mark.parametrize("filename", ["../outside.py", "demo/../../outside.py", "C:/outside.py", "demo/.env"])
def test_import_rejects_unsafe_paths(service: ModuleWorkspaceService, filename: str):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(filename, "bad")
    with pytest.raises(InvalidRequestError):
        preview_import(service, "alice", "expand", "user", "bad.zip", buffer.getvalue())
    assert not (service.root / "outside.py").exists()


def test_import_rejects_symlink_and_ui_rejects_external_schema_ref(service: ModuleWorkspaceService):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("demo/link.py")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(InvalidRequestError):
        preview_import(service, "alice", "expand", "user", "bad.zip", buffer.getvalue())
    target = make_module(service.root)
    path = target / "module/workspace.json"
    data = json.loads(path.read_text("utf-8"))
    data["actions"][0]["input_schema"]["properties"]["value"] = {"$ref": "https://external/schema"}
    path.write_text(json.dumps(data), "utf-8")
    with pytest.raises(InvalidRequestError, match="引用"):
        workspace_declaration(read_manifest(target, kind_hint="expand", scope="global"))


def test_http_assets_enforce_csp_and_rpc_requires_instance_auth(service: ModuleWorkspaceService):
    make_module(service.root)
    instance = grant(service)
    app = create_app(root=service.root, service=service.backend)
    app.state.module_workspaces = service
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(instance["asset_url"])
            assert response.status_code == 200
            assert "sandbox allow-scripts" in response.headers["content-security-policy"]
            assert "allow-same-origin" not in response.headers["content-security-policy"]
            assert response.headers["access-control-allow-origin"] == "*"
            assert response.headers["cache-control"] == "no-store"
            sdk = await client.get(f"/module-ui/{instance['nonce']}/__kemo__/bridge.js")
            assert sdk.status_code == 200 and "postMessage" in sdk.text
            response = await client.post(f"/api/module-ui/instances/{instance['nonce']}/rpc", json={"method": "module.status.get"})
            assert response.status_code == 200
            disabled = await client.patch("/api/users/alice/expand/global/demo/enabled", json={"enabled": False})
            assert disabled.status_code == 200
            enabled = await client.patch("/api/users/alice/expand/global/demo/enabled", json={"enabled": True})
            assert enabled.status_code == 200
            assert (await client.get(instance["asset_url"])).status_code == 404
            closed = await client.delete(f"/api/module-ui/instances/{instance['nonce']}")
            assert closed.status_code == 200
            assert (await client.get(instance["asset_url"])).status_code == 404
    asyncio.run(run())
