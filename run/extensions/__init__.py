"""Expansion, sensing, attachment, capability and multimodal runtime domain."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from run.extensions.expand_runtime import resolve_expand as resolve_expand

_DOMAIN_MODULES = (
    "attachments",
    "media_input_request",
    "media_asset_cache",
    "media_input_queue",
    "media_input_host",
    "media_input_metadata",
    "model_capabilities",
    "module_runtime",
    "multimodal",
    "media_outputs",
    "expand_runtime",
)


def __getattr__(name: str):
    from importlib import import_module

    for module_name in _DOMAIN_MODULES:
        module = import_module(f"run.extensions.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
