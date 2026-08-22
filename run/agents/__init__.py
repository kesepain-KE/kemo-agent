"""Compatibility import for the trusted sub-agent package runtime."""

from agents._runtime.schema import *  # noqa: F401,F403

_DOMAIN_MODULES = ("runner", "queue", "service", "subagent_invocation")


def __getattr__(name: str):
    from importlib import import_module

    for module_name in _DOMAIN_MODULES:
        module = import_module(f"run.agents.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
