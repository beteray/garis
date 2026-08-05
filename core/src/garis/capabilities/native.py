"""Running a native capability inside the one envelope.

The mirror of :mod:`garis.runtime.legacy`, and just as narrow. It resolves a
capability id, validates arguments against the declared inputs, calls the
executor and hands the declared verifier the structured evidence it asked for.
It evaluates no policy, requests no approval, reserves no effect, writes no
audit row and publishes nothing — the runner does all of that once, around this,
exactly as it does for a legacy tool.

This file lives in the capability package rather than in ``runtime`` for a
layering reason: ``capabilities`` may import ``runtime``, but not the other way
round. The runner learns about capabilities when something registers this
executor into it, which is also what keeps a process that has no capability
layer from pretending it has one.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any

from ..kernel.contracts import (
    CapabilityError,
    ExecutionContext,
    Failure,
    PolicySubject,
    StepTarget,
    Verification,
)
from ..runtime.action import Action
from ..runtime.runner import ExecutionOutput, Resolved, TargetExecutor
from .base import Capability, Invocation, Outcome
from .registry import CapabilityRegistry


def subject_from_capability(capability: Capability) -> PolicySubject:
    """A capability, said in the words policy speaks.

    Effects come from the capability's own declaration and nowhere else.
    Deriving them from `permission` would be guessing: `FILES` covers reading a
    name and permanently deleting a directory, `PROCESS` covers listing programs
    and killing one, and gating the second like the first is precisely the
    failure this normalisation exists to prevent.
    """
    return PolicySubject(
        id=capability.id,
        name=capability.id,
        category="capability",
        risk=capability.risk,
        permissions=(capability.permission,),
        effects=capability.effects,
        reversible=capability.reversible,
        requires_approval=capability.needs_approval,
        source="capability",
    )


class NativeCapabilityExecutor(TargetExecutor):
    """The adapter between a ``Capability`` and the one envelope."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def resolve(self, target: StepTarget, action: Action) -> Resolved:
        capability = self.registry.get(target.name)
        if not capability.supported_here():
            raise CapabilityError(
                Failure.UNSUPPORTED_PLATFORM,
                f"{capability.id} nie działa na tym systemie ({sys.platform}); "
                f"obsługiwane: {', '.join(capability.platforms)}",
                user_message="Tego nie da się tu zrobić.",
            )
        return Resolved(
            subject=subject_from_capability(capability),
            version=capability.version,
            # A capability that changes something takes its own id as the lease
            # key, so two tasks cannot drive the same one at once.
            resource_keys=(
                (f"capability:{capability.id}",) if capability.risk.mutating else ()
            ),
            handle=capability,
        )

    def validate(self, resolved: Resolved, params: Mapping[str, Any]) -> dict[str, Any]:
        capability: Capability = resolved.handle
        try:
            return capability.validate(dict(params))
        except ValueError as exc:
            raise CapabilityError(Failure.INVALID_INPUT, str(exc)) from exc

    async def execute(
        self,
        resolved: Resolved,
        params: Mapping[str, Any],
        context: ExecutionContext,
        action: Action,
    ) -> ExecutionOutput:
        capability: Capability = resolved.handle
        invocation = Invocation(capability.id, dict(params), effect_id=context.effect_id)
        outcome: Outcome = await capability.executor(invocation)
        return ExecutionOutput(
            ok=outcome.ok,
            value=outcome.value,
            evidence=outcome.evidence,
            error=outcome.error,
            error_kind="error" if not outcome.ok else "",
            uncertain=outcome.uncertain,
            failure=None if outcome.ok else Failure.EXECUTOR_FAILED,
        )

    async def verify(
        self, resolved: Resolved, params: Mapping[str, Any], output: ExecutionOutput
    ) -> Verification:
        """The capability's own declared postcondition, run against real evidence.

        `checked` and `goal_met` stay separate all the way out: a verifier that
        did not look cannot make anything `verified`, however cheerful its note.
        """
        capability: Capability = resolved.handle
        invocation = Invocation(capability.id, dict(params))
        verdict = await capability.verifier(
            invocation,
            Outcome(
                ok=output.ok, value=output.value, evidence=output.evidence,
                error=output.error, uncertain=output.uncertain,
            ),
        )
        return Verification(
            goal_met=verdict.ok,
            checked=verdict.checked,
            checked_by="rules" if verdict.checked else "none",
            reason=verdict.note,
            unmet=verdict.missing,
        )


__all__ = ["NativeCapabilityExecutor", "subject_from_capability"]
