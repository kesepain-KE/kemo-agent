"""Task-plan storage, mutation, execution and scheduling domain."""

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from run.tasks.boundary import TaskPlanCreationBoundary as TaskPlanCreationBoundary

_DOMAIN_MODULES = ("boundary", "store", "mutations", "executor", "service", "scheduler")


def __getattr__(name: str):
    from importlib import import_module

    for module_name in _DOMAIN_MODULES:
        module = import_module(f"run.tasks.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
