"""Compatibility alias for :mod:`run.tools.execution_watchdog`."""

from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("run.tools.execution_watchdog")
