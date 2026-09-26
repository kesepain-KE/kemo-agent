from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MAX_IMPLEMENTATION_LINES = 800
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx"}
IGNORED_PARTS = {
    ".git", "node_modules", "tests", "tmp", "开发临时目录", "dist", "build",
    "coverage", "__pycache__", ".test-work",
}
# These are cohesive declaration/protocol or page-composition roots rather than
# multi-domain implementation modules.  Every exception must carry a rationale.
COHESIVE_LONG_MODULES = {
    "web/service.py": "single Web service compatibility facade; domain work lives in web/services",
    "web/services/files.py": "single file-management service covering one storage domain",
    "web/services/settings.py": "single settings service covering configuration reads and writes",
    "global_expand/kemo_app/start_expand.py": "single Kemo App control dispatcher and panel entry",
    "global_expand/kemo_gateway_status/gateway_status.py": "single gateway status collection and rendering domain",
    "provider/adapters/compat.py": "single provider compatibility conversion layer",
    "provider/protocol/models.py": "provider protocol data declarations without orchestration",
    "web/frontend/src/components/AppShell.tsx": "application-shell composition root; run registry is extracted to a dedicated hook",
    "web/frontend/src/components/Chat/inlineWidgetProtocol.ts": "single inline-widget protocol and validators",
    "web/frontend/src/types/api.ts": "API declaration surface; executable implementations live elsewhere",
    "web/frontend/src/api/client.ts": "single HTTP transport facade grouped by backend resource",
    "web/frontend/src/pages/ChatPage.tsx": "chat lifecycle composition root; rendering lives in ChatPageView and workflows in chatWorkflows",
    "web/frontend/src/pages/SettingsPage.tsx": "settings composition root; reusable controls and normalization live in settingsSupport",
}


def _production_sources():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(ROOT)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if ".bak" in path.name or ".test." in path.name:
            continue
        yield path, relative.as_posix()


def test_multi_responsibility_implementation_modules_stay_below_800_lines() -> None:
    oversized: list[str] = []
    for path, relative in _production_sources():
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > MAX_IMPLEMENTATION_LINES and relative not in COHESIVE_LONG_MODULES:
            oversized.append(f"{relative}: {lines}")
    assert not oversized, "Unexpected production modules above 800 lines:\n" + "\n".join(oversized)


def test_deployment_test_work_is_not_production_source() -> None:
    assert ".test-work" in IGNORED_PARTS


def test_long_module_allowlist_is_exact_and_documented() -> None:
    for relative, rationale in COHESIVE_LONG_MODULES.items():
        path = ROOT / relative
        assert path.is_file(), relative
        assert len(rationale.strip()) >= 20, relative
        assert len(path.read_text(encoding="utf-8").splitlines()) > MAX_IMPLEMENTATION_LINES, relative


def test_split_modules_keep_stable_unified_entries() -> None:
    from run.agents.runner import AgentRunner
    from run.config.prompt_sources import PromptSourceRegistry
    from run.history.window_store import load_window, save_window
    from run.memory.sqlite import SqliteMemoryStore
    from run.tasks.store import PlanStore

    assert all((AgentRunner, PromptSourceRegistry, load_window, save_window, SqliteMemoryStore, PlanStore))
