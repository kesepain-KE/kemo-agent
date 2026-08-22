"""Compatibility alias for :mod:`run.conversation.round_finalizer`."""

from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("run.conversation.round_finalizer")
