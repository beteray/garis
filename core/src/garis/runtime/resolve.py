"""What a step *is*, asked before anything runs.

The planner drops steps it cannot run and `garis do --dry-run` lists what a plan
would touch. Both are questions about a target, not about an execution, and both
used to answer them by reaching into :class:`~garis.runtime.registry.ToolRegistry`
themselves — which was fine while a step could only ever be a tool.

It stops being fine the moment there are two kinds. Three modules each deciding
"can this run" is three definitions that drift, and the one that drifts quietly
is the planner's: it would simply stop scheduling capabilities and never say so.

Deliberately **not** the runner's first step made public. `CapabilityRunner` is
where things execute, in a fixed order that is free to change; if the planner
called into it, that order would become a public contract and a change to step 4
could break plan validation. This layer answers four questions and holds no
policy, no registry of its own and no way to run anything:

  * is this target known here?
  * does it run on this machine?
  * what does it declare it will do?
  * are these parameters acceptable to it?
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..kernel.contracts import CapabilityTarget, Effect, StepTarget
from .registry import ToolRegistry


class CapabilityCatalogue(Protocol):
    """The two methods this layer needs from a capability catalogue.

    Structural, so `runtime` keeps its hands off `capabilities` — which imports
    this package and may not be imported back.
    """

    def has(self, capability_id: str) -> bool: ...

    def get(self, capability_id: str) -> Any: ...


def _unresolvable(params: dict[str, Any]) -> dict[str, Any]:
    raise ValueError("krok nie wskazuje niczego, co da się uruchomić")


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """What is known about a step before anything happens.

    `reason` is written for a person and is empty when there is nothing wrong.
    """

    target: StepTarget
    known: bool
    runs_here: bool
    reason: str = ""
    effects: frozenset[Effect] = frozenset()
    #: Coerces parameters or raises. Belongs to the tool or capability itself —
    #: this layer never invents a schema.
    validate: Callable[[dict[str, Any]], dict[str, Any]] = field(
        default=_unresolvable, repr=False
    )

    @property
    def name(self) -> str:
        return self.target.name

    @property
    def executable(self) -> bool:
        """Whether this may be handed to the runner at all."""
        return self.known and self.runs_here


class TargetResolver:
    """Translates a target into what is known about it. Runs nothing."""

    def __init__(
        self, tools: ToolRegistry, capabilities: CapabilityCatalogue | None = None
    ) -> None:
        self.tools = tools
        self.capabilities = capabilities

    def resolve(self, target: StepTarget) -> ResolvedTarget:
        if isinstance(target, CapabilityTarget):
            return self._capability(target)
        return self._tool(target)

    def executable(self, target: StepTarget) -> bool:
        """Shorthand for the one question most callers have."""
        return self.resolve(target).executable

    # ------------------------------------------------------------------ kinds

    def _tool(self, target: StepTarget) -> ResolvedTarget:
        name = target.name
        if not self.tools.has(name):
            return ResolvedTarget(target, known=False, runs_here=False,
                                  reason=f"nieznane narzędzie {name!r}")
        spec = self.tools.get(name)
        if not spec.supported_here():
            return ResolvedTarget(target, known=True, runs_here=False,
                                  reason=f"{name} nie działa na tym systemie",
                                  effects=frozenset(spec.effects), validate=spec.validate)
        return ResolvedTarget(target, known=True, runs_here=True,
                              effects=frozenset(spec.effects), validate=spec.validate)

    def _capability(self, target: CapabilityTarget) -> ResolvedTarget:
        name = target.capability
        if self.capabilities is None or not self.capabilities.has(name):
            return ResolvedTarget(target, known=False, runs_here=False,
                                  reason=f"nieznana zdolność {name!r}")
        capability = self.capabilities.get(name)
        if not capability.supported_here():
            return ResolvedTarget(target, known=True, runs_here=False,
                                  reason=f"{name} nie działa na tym systemie",
                                  effects=frozenset(capability.effects),
                                  validate=capability.validate)
        return ResolvedTarget(target, known=True, runs_here=True,
                              effects=frozenset(capability.effects),
                              validate=capability.validate)


__all__ = ["CapabilityCatalogue", "ResolvedTarget", "TargetResolver"]
