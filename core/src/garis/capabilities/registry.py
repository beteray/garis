"""The catalogue of capabilities, and the one way to run one.

Two rules, both learned the hard way.

**Registration requires a verifier.** There is no default. A capability whose
postcondition nobody has written must say so explicitly with
`always_unchecked`, and everything it produces is then reported as finished but
unverified. Making the verifier optional is how "nothing checked this" becomes
"verified" by omission.

**Running goes through `perform`, never through the executor directly.** That is
where the platform check, the argument validation, the evidence check and the
verdict all happen. An executor called on its own can return `ok=True` and mean
nothing by it; `perform` is what turns a return value into a claim someone may
repeat to the user.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from ..errors import Unsupported
from .base import Capability, Invocation, Outcome, Verdict


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
        #: Effects already performed, by id. A resumed workflow asks here before
        #: acting: launching Discord twice because a crash landed between the
        #: launch and the journal write is a real effect duplicated, not a retry.
        self._performed: dict[str, Performed] = {}

    # ------------------------------------------------------------- registering

    def add(self, capability: Capability) -> Capability:
        existing = self._by_id.get(capability.id)
        if existing is not None and existing.version != capability.version:
            raise ValueError(
                f"{capability.id}: wersja {existing.version} jest już zarejestrowana"
            )
        self._by_id[capability.id] = capability
        return capability

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

    # ---------------------------------------------------------------- running

    async def perform(
        self,
        capability_id: str,
        args: dict[str, Any] | None = None,
        *,
        effect_id: str = "",
    ) -> Performed:
        capability = self.get(capability_id)

        if not capability.supported_here():
            return Performed(
                capability.id,
                capability.version,
                Outcome(ok=False, error=(
                    f"{capability.id} nie działa na tym systemie ({sys.platform}); "
                    f"obsługiwane: {', '.join(capability.platforms)}"
                )),
                Verdict(ok=False, checked=True, note="Zdolność nieobsługiwana tutaj."),
                effect_id,
            )

        # An effect already performed is not performed again. The recorded
        # result is returned as it stands, including its verdict: replaying it
        # would be a second real change dressed up as idempotence.
        if effect_id and effect_id in self._performed:
            return self._performed[effect_id]

        try:
            checked_args = capability.validate(args or {})
        except ValueError as exc:
            return Performed(
                capability.id,
                capability.version,
                Outcome(ok=False, error=str(exc)),
                Verdict(ok=False, checked=True, note="Złe parametry — nic nie zrobiłem."),
                effect_id,
            )

        invocation = Invocation(capability.id, checked_args, effect_id=effect_id)

        try:
            outcome = await capability.executor(invocation)
        except Exception as exc:  # a crash is an outcome, not a surprise
            # A capability that raised may still have changed something: it got
            # far enough to fail. Neither success nor a clean failure — say so.
            outcome = Outcome(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                uncertain=capability.risk is not None and capability.risk.value != "read",
            )

        verdict = await capability.verifier(invocation, outcome)
        performed = Performed(capability.id, capability.version, outcome, verdict, effect_id)
        if effect_id and outcome.ok:
            self._performed[effect_id] = performed
        return performed

    def forget_effects(self) -> None:
        """For tests and for a fresh process. Effects are per-run, not durable —
        durability belongs to the task journal, which already has it."""
        self._performed.clear()


#: One registry, filled at import time by the modules under this package.
REGISTRY = CapabilityRegistry()


__all__ = ["REGISTRY", "CapabilityRegistry", "Performed"]
