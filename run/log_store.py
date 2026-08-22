"""Compatibility alias for :mod:`run.infra.log_store`."""

from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("run.infra.log_store")
