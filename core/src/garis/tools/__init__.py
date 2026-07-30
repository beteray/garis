"""Built-in capabilities.

Registration is explicit and grouped so the two agents can differ: GARIS Desktop
gets the whole set, GARIS Server drops screen and mouse control it has no use for
and gains nothing by declaring.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..runtime import ToolRegistry
from . import assistant, desktop, files, packages, remote, shell, system, web

# Module per capability area. Order is irrelevant; names must stay unique.
ALL_MODULES = (files, shell, system, web, packages, desktop, assistant, remote)

DESKTOP_MODULES = ALL_MODULES
SERVER_MODULES = (files, shell, system, web, packages, assistant, remote)


def build_registry(modules: Iterable[object] = DESKTOP_MODULES) -> ToolRegistry:
    """Create a registry with the given capability modules registered."""
    registry = ToolRegistry()
    for module in modules:
        module.register(registry)  # type: ignore[attr-defined]
    return registry


def register_all(registry: ToolRegistry, modules: Iterable[object] = DESKTOP_MODULES) -> None:
    for module in modules:
        module.register(registry)  # type: ignore[attr-defined]


__all__ = [
    "ALL_MODULES",
    "DESKTOP_MODULES",
    "SERVER_MODULES",
    "build_registry",
    "register_all",
]
