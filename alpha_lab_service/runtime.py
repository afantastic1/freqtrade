"""Operator-selected, trusted service dependency loading (never Agent supplied)."""

from __future__ import annotations

import importlib


def load_service_factory(specification: str):
    if not specification or ":" not in specification:
        raise ValueError("factory must be configured as trusted_module:callable")
    module_name, attribute = specification.split(":", 1)
    if not module_name.replace("_", "").replace(".", "").isalnum() or not attribute.isidentifier():
        raise ValueError("invalid trusted factory reference")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise ValueError("configured service factory is not callable")
    return factory
