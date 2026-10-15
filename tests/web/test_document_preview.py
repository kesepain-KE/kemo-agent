"""Read-only multi-format preview and local Office converter regressions."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from web.errors import InvalidRequestError
from web.services import document_preview as preview
from tests.web import test_web_backend as backend_tests
from web.app import create_app
from web.service import WebRunService


class DocumentPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "source.docx"
        with zipfile.ZipFile(self.source, "w") as archive:
            archive.writestr("word/document.xml", "<document>preview</document>")

    def convert(self, command, **kwargs):
        work = Path(kwargs["cwd"])
        profile = (work / "profile" / "registrymodifications.xcu").read_text("utf-8")
        self.assertIn('DisableMacrosExecution', profile)
        self.assertIn('<value>true</value>', profile)
        self.assertIn('--headless', command)
        self.assertEqual(command[-1], str(work / "document.docx"))
        (work / "document.pdf").write_bytes(b"%PDF-1.7\npreview")
        return subprocess.CompletedProcess(command, 0)

    def test_read_only_snapshot_cache_and_content_invalidation(self):
        before = self.source.read_bytes()
        with patch.object(preview, "_libreoffice", return_value="soffice"), \
                patch.object(preview.subprocess, "run", side_effect=self.convert) as convert:
            first = preview.office_pdf(self.source)
            self.assertEqual(first.read_bytes(), b"%PDF-1.7\npreview")
            self.assertEqual(preview.office_pdf(self.source), first)
            self.assertEqual(convert.call_count, 1)
            self.assertEqual(self.source.read_bytes(), before)
            with zipfile.ZipFile(self.source, "a") as archive:
                archive.writestr("word/new.xml", "changed")
            second = preview.office_pdf(self.source)
            self.assertNotEqual(first, second)
            self.assertEqual(convert.call_count, 2)

    def test_rejects_invalid_oversized_and_timeout_without_modifying_source(self):
        with patch.object(preview, "_libreoffice", return_value="soffice"):
            with patch.object(preview.subprocess, "run",
                              side_effect=subprocess.TimeoutExpired("soffice", 45)):
                before = self.source.read_bytes()
                with self.assertRaisesRegex(InvalidRequestError, "超时"):
                    preview.office_pdf(self.source)
                self.assertEqual(self.source.read_bytes(), before)
            with patch.object(preview.subprocess, "run", return_value=None):
                with self.assertRaisesRegex(InvalidRequestError, "无法转换"):
                    preview.office_pdf(self.source)
        self.source.write_bytes(b"invalid office")
        with self.assertRaisesRegex(InvalidRequestError, "损坏"):
            preview.office_pdf(self.source)
        with self.source.open("wb") as handle:
            handle.truncate(preview.DOCUMENT_MAX_BYTES + 1)
        with self.assertRaisesRegex(InvalidRequestError, "32 MB"):
            preview.office_pdf(self.source)

    def test_missing_converter_is_an_actionable_error(self):
        with patch.object(preview.shutil, "which", return_value=None), \
                patch.object(preview.Path, "is_file", return_value=False):
            with self.assertRaisesRegex(InvalidRequestError, "安装 LibreOffice"):
                preview._libreoffice()

    def test_conversion_rejects_non_pdf_output(self):
        def invalid(command, **kwargs):
            (Path(kwargs["cwd"]) / "document.pdf").write_bytes(b"not PDF")
        with patch.object(preview, "_libreoffice", return_value="soffice"), \
                patch.object(preview.subprocess, "run", side_effect=invalid):
            with self.assertRaisesRegex(RuntimeError, "有效 PDF"):
                preview.office_pdf(self.source)


class DocumentPreviewRoutesTests(unittest.TestCase):
    def test_tmp_download_returns_original_office_and_unsupported_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tmp").mkdir()
            app = create_app(service=WebRunService(root))
            for name in ("original.docx", "unknown.bin"):
                data = b"original document bytes"
                (root / "tmp" / name).write_bytes(data)
                response = backend_tests.WebBackendTests.request(
                    self, app, "GET", "/api/tmp/download", params={"path": name})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.content, data)
                self.assertIn("attachment", response.headers["content-disposition"])
            response = backend_tests.WebBackendTests.request(
                self, app, "GET", "/api/tmp/download", params={"path": "../outside.bin"})
            self.assertEqual(response.status_code, 400, response.text)

    def test_text_pdf_office_ranges_and_limits_in_both_file_areas(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "users" / "alice" / "history").mkdir(parents=True)
            app = create_app(service=WebRunService(root))
            pdf = root / "converted.pdf"
            pdf.write_bytes(b"%PDF-1.7\npreview")
            for directory, endpoint in (
                (root / "users" / "alice" / "file_upload", "/api/users/alice/files/file_upload/preview"),
                (root / "tmp", "/api/tmp/preview"),
            ):
                directory.mkdir(parents=True, exist_ok=True)
                for ext in ("txt", "md", "json", "csv", "xml", "tsx"):
                    source = directory / f"sample.{ext}"
                    source.write_bytes("完整正文\nsecond line".encode("utf-8"))
                    response = backend_tests.WebBackendTests.request(self, app, "GET", endpoint, params={"path": source.name})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.text, "完整正文\nsecond line")
                    self.assertIn("text/plain", response.headers["content-type"])
                    self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                (directory / "sample.pdf").write_bytes(pdf.read_bytes())
                response = backend_tests.WebBackendTests.request(self, app, "GET", endpoint, params={"path": "sample.pdf"},
                                                  headers={"Range": "bytes=0-4"})
                self.assertEqual(response.status_code, 206, response.text)
                self.assertEqual(response.content, b"%PDF-")
                self.assertIn("application/pdf", response.headers["content-type"])
                for ext in preview.OFFICE_SUFFIXES:
                    (directory / f"sample{ext}").write_bytes(b"office")
                    with patch("web.services.files.office_pdf", return_value=pdf) as convert:
                        response = backend_tests.WebBackendTests.request(self, app, "GET", endpoint, params={"path": f"sample{ext}"})
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.content, pdf.read_bytes())
                        convert.assert_called_once_with(directory / f"sample{ext}")
                for name, limit in (("large.txt", 4 * 1024 * 1024), ("large.pdf", preview.PDF_MAX_BYTES)):
                    with (directory / name).open("wb") as stream:
                        stream.truncate(limit + 1)
                    response = backend_tests.WebBackendTests.request(self, app, "GET", endpoint, params={"path": name})
                    self.assertEqual(response.status_code, 400, response.text)
                response = backend_tests.WebBackendTests.request(self, app, "GET", endpoint, params={"path": "../converted.pdf"})
                self.assertEqual(response.status_code, 400, response.text)
