"""Build a deterministic, source-only Kemo protocol artifact for peer repos."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from provider.protocol.models import PROTOCOL_VERSION
from provider.protocol.spec.generate import generate


PROTOCOL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PROTOCOL_ROOT.parents[1]
ARTIFACT_NAME = f"kemo_protocol-{PROTOCOL_VERSION}.zip"
FILES = (
    "diagnostics.py",
    "enums.py",
    "errors.py",
    "models.py",
    "serialization.py",
    "streaming.py",
    "validation.py",
)


def build(destination: Path) -> dict[str, str]:
    generate()
    destination.mkdir(parents=True, exist_ok=True)
    artifact = destination / ARTIFACT_NAME
    timestamp = (2026, 9, 30, 0, 0, 0)
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        root_info = zipfile.ZipInfo("provider/__init__.py", timestamp)
        root_info.external_attr = 0o644 << 16
        archive.writestr(root_info, b'"""Namespace for the Kemo protocol artifact."""\n')
        protocol_info = zipfile.ZipInfo("provider/protocol/__init__.py", timestamp)
        protocol_info.external_attr = 0o644 << 16
        archive.writestr(
            protocol_info,
            b'"""Pinned Kemo protocol artifact package."""\n',
        )
        for name in FILES:
            info = zipfile.ZipInfo(f"provider/protocol/{name}", timestamp)
            info.external_attr = 0o644 << 16
            archive.writestr(info, (PROTOCOL_ROOT / name).read_bytes())
        tool_arguments_info = zipfile.ZipInfo("provider/tool_arguments.py", timestamp)
        tool_arguments_info.external_attr = 0o644 << 16
        archive.writestr(
            tool_arguments_info,
            (PROTOCOL_ROOT.parent / "tool_arguments.py").read_bytes(),
        )
        spec_root = PROTOCOL_ROOT / "spec"
        for path in sorted(
            [
                spec_root / "schema.json",
                spec_root / "model-index.json",
                spec_root / "invariants.json",
                spec_root / "freeze.json",
                spec_root / "fixture-manifest.json",
                *sorted((spec_root / "fixtures").glob("*.json")),
            ],
            key=lambda item: item.relative_to(spec_root).as_posix(),
        ):
            info = zipfile.ZipInfo(
                "provider/protocol/spec_artifacts/"
                + path.relative_to(spec_root).as_posix(),
                timestamp,
            )
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    freeze = json.loads((PROTOCOL_ROOT / "spec" / "freeze.json").read_text(encoding="utf-8"))
    lock = {
        "protocol_version": PROTOCOL_VERSION,
        "artifact": ARTIFACT_NAME,
        "sha256": digest,
        "schema_sha256": freeze["schema_sha256"],
        "fixture_sha256": freeze["fixture_sha256"],
    }
    (destination / "kemo-protocol.lock.json").write_text(
        json.dumps(lock, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return lock


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.destination), ensure_ascii=False, indent=2))
