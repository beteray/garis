"""The catalogue of capabilities. A list, not an engine.

Three rules, all learned the hard way.

**Registration requires a verifier.** There is no default. A capability whose
postcondition nobody has written must say so explicitly with
`always_unchecked`, and everything it produces is then reported as finished but
unverified. Making the verifier optional is how "nothing checked this" becomes
"verified" by omission.

**Registration requires declared effects for anything that mutates.** A
capability that changes the world and says nothing about how gets gated like a
read. `Permission` does not answer this: `FILES` covers reading a name and
deleting a directory, `PROCESS` covers listing programs and killing one.

**Running does not happen here.** It used to: this class had its own executor
call, its own crash handling and its own in-memory idempotence dict, none of
which had policy, an audit row or an effect that survived a restart. That was
the second execution path. Now there is one, in
:class:`~garis.runtime.runner.CapabilityRunner`, and this catalogue holds
capabilities and answers questions about them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..errors import Unsupported
from ..kernel.contracts import Failure
from .base import Capability, Outcome, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from ..runtime.runner import CapabilityRunner, RunnerResult


@dataclass(frozen=True, slots=True)
class Performed:
    """What a capability run amounts to: the result, and what is provable."""

    capability: str
    version: int
    outcome: Outcome
    verdict: Verdict
    effect_id: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome.ok

    @property
    def verified(self) -> bool:
        """The only place this word is computed. Both halves, always."""
        return self.verdict.ok and self.verdict.checked

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "version": self.version,
            "ok": self.outcome.ok,
            "uncertain": self.outcome.uncertain,
            "value": self.outcome.value,
            "evidence": dict(self.outcome.evidence),
            "error": self.outcome.error,
            "verified": self.verified,
            "checked": self.verdict.checked,
            "note": self.verdict.note,
            "missing": list(self.verdict.missing),
            "effect_id": self.effect_id,
        }


class CapabilityRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, Capability] = {}
        #: Set by whoever assembles the process. The catalogue never builds one:
        #: a registry that can construct its own runner, effect store and policy
        #: engine is a second execution path wearing a different hat.
        self.runner: CapabilityRunner | None = None

    # ------------------------------------------------------------- registering

    def add(self, capability: Capability) -> Capability:
        existing = self._by_id.get(capability.id)
        if existing is not None and existing.version != capability.version:
            raise ValueError(
                f"{capability.id}: wersja {existing.version} jest już zarejestrowana"
            )
        if capability.risk.mutating and not capability.effects:
            # No bridge, no fallback, no guess. A capability that changes the
            # world and says nothing about how would be gated like a read, and
            # the only person who can say what it changes is its author.
            #
            # `permission` cannot stand in for this: FILES covers reading a name
            # and permanently deleting a directory, PROCESS covers listing and
            # killing. Declaring `effects=frozenset()` is a legitimate answer —
            # for something that genuinely only reads — but it has to be said.
            raise ValueError(
                f"{capability.id}: zdolność zmienia stan ({capability.risk.value}), "
                f"więc musi zadeklarować effects=. Dla samego odczytu podaj "
                f"effects=frozenset()."
            )
        self._by_id[capability.id] = capability
        return capability

    def bind(self, runner: CapabilityRunner) -> None:
        """Attach the one envelope. Everything this catalogue can run, runs there."""
        self.runner = runner

    def has(self, capability_id: str) -> bool:
        return capability_id in self._by_id

    def get(self, capability_id: str) -> Capability:
        try:
            return self._by_id[capability_id]
        except KeyError:
            raise Unsupported(f"Nie znam zdolności {capability_id!r}") from None

    def all(self) -> tuple[Capability, ...]:
        return tuple(self._by_id.values())

    def usable_here(self) -> tuple[Capability, ...]:
        return tuple(c for c in self._by_id.values() if c.supported_here())

    def catalogue(self) -> list[dict[str, Any]]:
        """What the planner may see: exposed, supported, and nothing else.

        Unexposed capabilities still run when something in this codebase calls
        them by id — a workflow composing four of them does not need the planner
        to know they exist.
        """
        return [
            {
                "id": c.id,
                "version": c.version,
                "summary": c.summary,
                "risk": c.risk.value,
                "permission": c.permission.value,
                "needs_approval": c.needs_approval,
                "inputs": [
                    {"name": f.name, "type": f.type, "required": f.required,
                     "description": f.description}
                    for f in c.inputs
                ],
            }
            for c in self._by_id.values()
            if c.exposed and c.supported_here()
        ]

def performed_from(capability: Capability, result: RunnerResult) -> Performed:
    """The runner's structured answer, in capability terms.

    Translation, not execution: it takes what the one envelope already returned.
    The catalogue used to own a `perform()` that built an action and called the
    runner itself — convenient, and a second door into execution that nothing in
    production used. Whoever runs a capability builds the target and calls the
    runner; this turns the answer back into `Performed`.
    """
    verification = result.verification
    note = verification.reason or result.error
    if result.failure is Failure.UNSUPPORTED_PLATFORM:
        return Performed(
            capability.id, capability.version,
            Outcome(ok=False, error=result.error),
            Verdict(ok=False, checked=True, note="Zdolność nieobsługiwana tutaj."),
            result.effect_id,
        )
    if result.failure is Failure.INVALID_INPUT and not result.replayed:
        return Performed(
            capability.id, capability.version,
            Outcome(ok=False, error=result.error),
            Verdict(ok=False, checked=True, note="Złe parametry — nic nie zrobiłem."),
            result.effect_id,
        )
    outcome = Outcome(
        ok=result.ok,
        value=result.value,
        evidence=result.evidence[0] if result.evidence else {},
        error=result.error,
        uncertain=result.uncertain,
    )
    return Performed(
        capability.id, capability.version, outcome,
        Verdict(
            ok=verification.goal_met, checked=verification.checked, note=note,
            missing=verification.unmet,
        ),
        result.effect_id,
    )


#: One registry, filled at import time by the modules under this package.
REGISTRY = CapabilityRegistry()


__all__ = ["REGISTRY", "CapabilityRegistry", "Performed"]
