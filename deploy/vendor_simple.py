"""Vendor pinned upstream simple binaries for offline runtime use. No build tools needed."""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path, PurePosixPath
import platform
import struct
import urllib.request
import zipfile


VERSION = "0.7.1"
DESTINATION = Path(__file__).resolve().parents[1] / "run/infra/sqlite_simple"
# Digests published by GitHub for wangfenjin/simple v0.7.1 release assets.
ASSETS = {
    "windows-x64": ("windows-x64", "7f03cc28cf307721f5621b5a52ef3bcb26c5215de012b09900492eb34d5bed0b"),
    "windows-x86": ("windows-x86", "627847a5f7efbd392d7a52cf20fc3c47c205c0559f19b5c0ed6f9b3b39039889"),
    "windows-arm64": ("windows-arm64", "520c33aae3fab35cba963927d04f041f971eee71f01fa577fb1d51e171780687"),
    "linux-x64": ("linux-ubuntu-22.04", "0c9a7a578fc50ef5480e69e1e1880535ae68d75e1c1580f6bf106073087642a5"),
    "linux-arm64": ("linux-ubuntu-24.04-arm", "d2d6589f0fc144099d48105cb3d97c9939ef24c87b63b3f2b749a10a6922fa48"),
    "darwin-x64": ("osx-x64", "d6f7e9fc9dac3c2bcfb5389618d41f2f0db6ea5a83dd8b9a363cf9b02fa20f95"),
    "darwin-arm64": ("osx-arm64", "b699f0fca1e7d1f8776d067708ecf4d0bcc2d765e4b643862e129058583b885f"),
}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def vendor(target: str) -> None:
    asset, expected = ASSETS[target]
    url = f"https://github.com/wangfenjin/simple/releases/download/v{VERSION}/libsimple-{asset}.zip"
    data = fetch(url)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"simple release checksum mismatch: {target}")
    suffix = {"windows": "dll", "linux": "so", "darwin": "dylib"}[target.split("-")[0]]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        library_name = "simple.dll" if suffix == "dll" else f"libsimple.{suffix}"
        library = next(name for name in archive.namelist()
                       if PurePosixPath(name).name == library_name)
        output = DESTINATION / target / f"libsimple.{suffix}"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(archive.read(library))
        for name in archive.namelist():
            path = PurePosixPath(name)
            if path.parent.name == "dict" and path.suffix == ".utf8":
                dictionary = DESTINATION / "dict" / path.name
                dictionary.parent.mkdir(exist_ok=True)
                content = archive.read(name).replace(b"\r\n", b"\n")
                if dictionary.exists() and dictionary.read_bytes().replace(b"\r\n", b"\n") != content:
                    raise ValueError(f"simple dictionaries differ between release platforms: {path.name}")
                dictionary.write_bytes(content)
    print(f"Vendored simple {VERSION}: {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Include all supported desktop/server platforms")
    args = parser.parse_args()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "i386": "x86", "i686": "x86"}.get(
        platform.machine().lower(), platform.machine().lower())
    if platform.system() == "Windows" and struct.calcsize("P") == 4:
        arch = "x86"
    for target in ASSETS if args.all else [f"{platform.system().lower()}-{arch}"]:
        vendor(target)
    licenses = {
        "LICENSE.simple": f"https://raw.githubusercontent.com/wangfenjin/simple/v{VERSION}/LICENSE",
        "LICENSE.cppjieba": "https://raw.githubusercontent.com/yanyiwu/cppjieba/194c144d8b5ed1baf3190d07c5226e804454ab47/LICENSE",
    }
    for name, url in licenses.items():
        (DESTINATION / name).write_bytes(fetch(url))


if __name__ == "__main__":
    main()
