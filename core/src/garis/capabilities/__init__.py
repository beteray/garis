"""Capabilities: what GARIS can do, with the proof each claim requires.

Importing this package registers everything in it. `REGISTRY.catalogue()` is what
a planner may see — exposed and supported only — while anything in this codebase
may call a capability by id whether or not it is on the menu.
"""

from __future__ import annotations

from . import processes  # noqa: F401 - imported for its registrations
from .base import (
    Capability,
    Field,
    Invocation,
    Outcome,
    Permission,
    Risk,
    Verdict,
    always_unchecked,
    evidence_verifier,
)
from .registry import REGISTRY, CapabilityRegistry, Performed, performed_from

__all__ = [
    "REGISTRY",
    "Capability",
    "CapabilityRegistry",
    "Field",
    "Invocation",
    "Outcome",
    "Performed",
    "Permission",
    "Risk",
    "Verdict",
    "always_unchecked",
    "evidence_verifier",
    "performed_from",
]
