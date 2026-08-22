"""Compatibility alias for :mod:`run.history.index`."""

from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("run.history.index")
