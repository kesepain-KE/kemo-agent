"""Unified internal facade for Kemo Graph library workflows."""
from client import api_request, api_upload_file, verify_service
from registry import QUERY_ARTIFACT_DIR, STATUS_PATH
import library_status_operations as _status
import library_content_operations as _content
import library_document_operations as _documents
from library_content_operations import (MAX_INGEST_PATH_CHARS, MAX_INGEST_PATH_TOTAL_CHARS, MAX_INGEST_PATHS, SUPPORTED_IMPORT_SUFFIXES)
_default_document_health = _status._default_document_health
_portable_document_health = _status._portable_document_health

def _sync_dependencies():
    for module in (_status, _content, _documents):
        module.api_request = api_request
    _content.api_upload_file = api_upload_file
    _status.verify_service = verify_service
    _status.STATUS_PATH = STATUS_PATH
    _content.QUERY_ARTIFACT_DIR = QUERY_ARTIFACT_DIR
    _status._default_document_health = _default_document_health
    _status._portable_document_health = _portable_document_health

def _call(module, name, *args, **kwargs):
    _sync_dependencies()
    return getattr(module, name)(*args, **kwargs)

def status_libraries(*a, **k): return _call(_status, "status_libraries", *a, **k)
def initialize_libraries(*a, **k): return _call(_status, "initialize_libraries", *a, **k)
def query_libraries(*a, **k): return _call(_content, "query_libraries", *a, **k)
def ingest_library(*a, **k): return _call(_content, "ingest_library", *a, **k)
def upload_markdown(*a, **k): return _call(_content, "upload_markdown", *a, **k)
def import_file(*a, **k): return _call(_content, "import_file", *a, **k)
def document_operation(*a, **k): return _call(_documents, "document_operation", *a, **k)
def jobs_operation(*a, **k): return _call(_documents, "jobs_operation", *a, **k)
def project_operation(*a, **k): return _call(_documents, "project_operation", *a, **k)
