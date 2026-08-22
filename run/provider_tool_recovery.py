"""Compatibility alias for :mod:`run.tools.provider_tool_recovery`."""

from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("run.tools.provider_tool_recovery")
