"""Local, read-only Office → PDF preview. Never uses a cloud document viewer."""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile

from web.errors import InvalidRequestError, WebServiceError

OFFICE_SUFFIXES = frozenset({
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf",
})
DOCUMENT_MAX_BYTES = 32 * 1024 * 1024
PDF_MAX_BYTES = 64 * 1024 * 1024
_CACHE_MAX_BYTES = 256 * 1024 * 1024
_LOCK = threading.Lock()
_CONVERTERS = threading.BoundedSemaphore(2)
_CACHE: OrderedDict[str, tuple[Path, float, int]] = OrderedDict()
_CACHE_ROOT = tempfile.TemporaryDirectory(prefix="kemo-document-previews-")


def _libreoffice() -> str:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            candidate = Path(base) / "LibreOffice" / "program" / "soffice.exe"
            if candidate.is_file():
                return str(candidate)
    candidate = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if candidate.is_file():
        return str(candidate)
    raise InvalidRequestError("Office 预览需要在服务器安装 LibreOffice；可先下载原文件查看")


def _check_package(source: Path) -> None:
    if source.suffix.lower() not in {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}:
        return
    try:
        with zipfile.ZipFile(source) as package:
            entries = package.infolist()
            if len(entries) > 20_000 or sum(item.file_size for item in entries) > 256 * 1024 * 1024:
                raise InvalidRequestError("Office 文档展开后的数据过大，无法安全预览")
    except zipfile.BadZipFile as exc:
        raise InvalidRequestError("Office 文件损坏或已加密，请下载后查看") from exc


def office_pdf(source: Path) -> Path:
    """Convert a bounded snapshot with an isolated, macro-disabled LO profile."""
    if source.stat().st_size > DOCUMENT_MAX_BYTES:
        raise InvalidRequestError("Office 文档预览上限（32 MB），请下载后查看")
    _check_package(source)
    digest = hashlib.sha256(str(source.resolve()).encode("utf-8"))
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    key = digest.hexdigest()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0].is_file():
            _CACHE[key] = (cached[0], time.monotonic(), cached[2])
            _CACHE.move_to_end(key)
            return cached[0]
    if not _CONVERTERS.acquire(timeout=1):
        raise InvalidRequestError("文档预览正在处理其他文件，请稍后重试")
    try:
        executable = _libreoffice()
        with tempfile.TemporaryDirectory(prefix="kemo-document-convert-") as staging:
            work = Path(staging)
            snapshot = work / ("document" + source.suffix.lower())
            shutil.copyfile(source, snapshot)
            if snapshot.stat().st_size > DOCUMENT_MAX_BYTES:
                raise InvalidRequestError("Office 文档预览上限（32 MB），请下载后查看")
            _check_package(snapshot)
            # The cache key must describe the exact snapshot sent to the converter,
            # even when an editor saves the original during preview generation.
            digest = hashlib.sha256(str(source.resolve()).encode("utf-8"))
            with snapshot.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            key = digest.hexdigest()
            profile = work / "profile"
            profile.mkdir()
            (profile / "registrymodifications.xcu").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
                '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
                '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
                '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
                '<prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop>'
                '</item>'
                '<item oor:path="/org.openoffice.Office.Writer/Content/Update">'
                '<prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item>'
                '<item oor:path="/org.openoffice.Office.Calc/Content/Update">'
                '<prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item>'
                '</oor:items>',
                encoding="utf-8",
            )
            try:
                subprocess.run(
                    [executable, f"-env:UserInstallation={profile.as_uri()}", "--headless",
                     "--nologo", "--nodefault", "--nolockcheck", "--norestore",
                     "--convert-to", "pdf", "--outdir", str(work), str(snapshot)],
                    cwd=work, timeout=45, check=True, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except subprocess.TimeoutExpired as exc:
                raise InvalidRequestError("Office 文档转换超时，请下载原文件查看") from exc
            except (OSError, subprocess.CalledProcessError) as exc:
                raise InvalidRequestError("Office 文件转换失败，请确认文档没有损坏或加密") from exc
            output = work / "document.pdf"
            if not output.is_file() or output.stat().st_size == 0:
                raise InvalidRequestError("Office 文件无法转换为预览，可能损坏、加密或格式不匹配")
            if output.stat().st_size > PDF_MAX_BYTES:
                raise InvalidRequestError("转换后的 PDF 预览上限（64 MB），请下载原文件查看")
            with output.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise WebServiceError("文档转换没有生成有效 PDF")
            target = Path(_CACHE_ROOT.name) / f"{key}.pdf"
            with _LOCK:
                if key not in _CACHE:
                    shutil.copyfile(output, target)
                    _CACHE[key] = (target, time.monotonic(), target.stat().st_size)
                cached = _CACHE[key]
                _CACHE[key] = (cached[0], time.monotonic(), cached[2])
                _CACHE.move_to_end(key)
                # Only our own generated cache files; never touch user documents.
                # Keep recent responses alive for at least five minutes.
                total = sum(entry[2] for entry in _CACHE.values())
                for old_key, (old_path, created, size) in list(_CACHE.items()):
                    if total <= _CACHE_MAX_BYTES:
                        break
                    if old_key != key and time.monotonic() - created > 300:
                        old_path.unlink(missing_ok=True)
                        del _CACHE[old_key]
                        total -= size
                if total > _CACHE_MAX_BYTES:
                    target.unlink(missing_ok=True)
                    _CACHE.pop(key, None)
                    raise InvalidRequestError("文档预览缓存暂满，请稍后重试或下载原文件")
            return target
    finally:
        _CONVERTERS.release()
