"""Compatibility alias for :mod:`run.agents.subagent_invocation`."""

from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("run.agents.subagent_invocation")
