"""Unified public facade for Kemo Graph sidecar operations.

Implementation is grouped by library/document workflows and graph/admin workflows.
Callers should keep importing this module so the internal layout can evolve safely.
"""

from client import api_request, api_upload_file, verify_service
from registry import QUERY_ARTIFACT_DIR, STATUS_PATH
import library_operations as _library
import graph_operations as _graph
from library_operations import (
    _default_document_health,
    _portable_document_health,
    MAX_INGEST_PATH_CHARS,
    MAX_INGEST_PATH_TOTAL_CHARS,
    MAX_INGEST_PATHS,
    SUPPORTED_IMPORT_SUFFIXES,
)
def _sync_dependencies() -> None:
    # Preserve the historical facade as the dependency-injection boundary.
    for module in (_library, _graph):
        module.api_request = api_request
    _library.api_upload_file = api_upload_file
    _library.verify_service = verify_service
    _library._default_document_health = _default_document_health
    _library._portable_document_health = _portable_document_health
    _library.STATUS_PATH = STATUS_PATH
    _library.QUERY_ARTIFACT_DIR = QUERY_ARTIFACT_DIR

def _call(module, name, *args, **kwargs):
    _sync_dependencies()
    return getattr(module, name)(*args, **kwargs)

def status_libraries(*args, **kwargs): return _call(_library, "status_libraries", *args, **kwargs)
def initialize_libraries(*args, **kwargs): return _call(_library, "initialize_libraries", *args, **kwargs)
def query_libraries(*args, **kwargs): return _call(_library, "query_libraries", *args, **kwargs)
def ingest_library(*args, **kwargs): return _call(_library, "ingest_library", *args, **kwargs)
def upload_markdown(*args, **kwargs): return _call(_library, "upload_markdown", *args, **kwargs)
def import_file(*args, **kwargs): return _call(_library, "import_file", *args, **kwargs)
def document_operation(*args, **kwargs): return _call(_library, "document_operation", *args, **kwargs)
def jobs_operation(*args, **kwargs): return _call(_library, "jobs_operation", *args, **kwargs)
def project_operation(*args, **kwargs): return _call(_library, "project_operation", *args, **kwargs)
def graph_operation(*args, **kwargs): return _call(_graph, "graph_operation", *args, **kwargs)
def entity_operation(*args, **kwargs): return _call(_graph, "entity_operation", *args, **kwargs)
def cache_operation(*args, **kwargs): return _call(_graph, "cache_operation", *args, **kwargs)
def maintenance_operation(*args, **kwargs): return _call(_graph, "maintenance_operation", *args, **kwargs)
def logs_operation(*args, **kwargs): return _call(_graph, "logs_operation", *args, **kwargs)
def config_read(*args, **kwargs): return _call(_graph, "config_read", *args, **kwargs)
def update_status(*args, **kwargs): return _call(_graph, "update_status", *args, **kwargs)

__all__ = [
    "MAX_INGEST_PATH_CHARS", "MAX_INGEST_PATH_TOTAL_CHARS", "MAX_INGEST_PATHS",
    "QUERY_ARTIFACT_DIR", "STATUS_PATH", "SUPPORTED_IMPORT_SUFFIXES",
    "cache_operation", "config_read", "document_operation", "entity_operation",
    "graph_operation", "import_file", "ingest_library", "initialize_libraries",
    "jobs_operation", "logs_operation", "maintenance_operation", "project_operation",
    "query_libraries", "status_libraries", "update_status", "upload_markdown",
]
