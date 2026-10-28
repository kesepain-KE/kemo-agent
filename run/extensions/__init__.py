"""Expansion, sensing, attachment, capability and multimodal runtime domain."""

_DOMAIN_MODULES = (
    "attachments",
    "media_input_request",
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
