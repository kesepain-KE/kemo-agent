"""Cron, maintenance and runtime-host scheduling domain."""

_DOMAIN_MODULES = ("cron_store", "runtime_state", "log_aggregator", "maintenance", "runtime_host")


def __getattr__(name: str):
    from importlib import import_module

    for module_name in _DOMAIN_MODULES:
        module = import_module(f"run.scheduler.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
