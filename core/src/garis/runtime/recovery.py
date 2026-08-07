"""Settling an effect nobody could vouch for — by looking, never by repeating.

A crash leaves an effect `UNCERTAIN`: reserved, never settled, world unknown.
This service picks the read-only inspection the capability declared, runs it
**through the one envelope**, and writes down what came back.

Three rules it does not get to bend:

**It never re-runs the original.** Not once, not "just to be sure". The whole
value of an uncertain effect is that it stops exactly this, and a recovery pass
that repeated the act would be the bug it exists to prevent.

**It does not decide policy.** There is a static refusal for inspectors that are
obviously wrong for an automatic pass — anything that declares an effect, a
fixture in production, a capability that says up front nobody checks it. Beyond
that the envelope rules: the inspection goes through `CapabilityRunner`, and if
the runner comes back asking for a person, recovery ends `MANUAL_REQUIRED` and
the inspector does not run. Reproducing `PolicyEngine`'s conditions here would
be a second policy that agrees with the first until the day it does not.

**It does not conclude from a matching state.** See `kernel/recovery.py`: the
volume reading 30 proves the volume is 30, not that GARIS set it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..errors import GarisError
from ..events import Topic
from ..kernel.contracts import (
    CapabilityTarget,
    EffectDisposition,
    ExecutionContext,
    RuntimeProfile,
    Verification,
)
from ..kernel.effects import EffectRecord, EffectState, EffectStore
from ..kernel.outbox import EventOutbox
from ..kernel.recovery import (
    Observation,
    RecoveryAssessment,
    RecoveryRecord,
    RecoveryStatus,
    RecoveryStore,
)
from .action import Action
from .audit import AuditLog
from .resolve import TargetResolver
from .runner import CapabilityRunner, RunnerResult, child_step_key

#: Reason codes for events. Prose belongs in the history row, which a person
#: opens deliberately; an event goes to every consumer of the bus.
NO_RECONCILER = "no_reconciler"
INSPECTOR_EFFECTFUL = "inspector_effectful"
INSPECTOR_UNKNOWN = "inspector_unknown"
INSPECTOR_UNSUPPORTED = "inspector_unsupported"
INSPECTOR_UNVERIFIABLE = "inspector_unverifiable"
INSPECTOR_FIXTURE = "inspector_fixture"
NEEDS_APPROVAL = "needs_approval"
POLICY_DENIED = "policy_denied"
INSPECTION_FAILED = "inspection_failed"
INSPECTION_UNCHECKED = "inspection_unchecked"
NOT_UNCERTAIN = "not_uncertain"


class ReconcilerCatalogue(Protocol):
    """What this service needs to look a capability up. Metadata only."""

    def has(self, capability_id: str) -> bool: ...

    def get(self, capability_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    """What one reconciliation pass amounts to."""

    effect_id: str
    status: RecoveryStatus
    disposition: EffectDisposition
    verification: Verification
    reason: str = ""
    record: RecoveryRecord | None = None

    @property
    def resolved(self) -> bool:
        return self.status.resolved

    @property
    def goal_met(self) -> bool:
        """Whether the requested outcome holds now — regardless of who caused it."""
        return self.verification.goal_met and self.verification.verified


class EffectRecoveryService:
    """Inspects uncertain effects. Owns no execution of its own."""

    def __init__(
        self,
        *,
        effects: EffectStore,
        recoveries: RecoveryStore,
        runner: CapabilityRunner,
        resolver: TargetResolver,
        capabilities: ReconcilerCatalogue,
        audit: AuditLog,
        outbox: EventOutbox,
    ) -> None:
        self.effects = effects
        self.recoveries = recoveries
        self.runner = runner
        self.resolver = resolver
        self.capabilities = capabilities
        self.audit = audit
        self.outbox = outbox

    # ------------------------------------------------------------------ public

    def uncertain(self, task_id: str = "") -> list[EffectRecord]:
        """Everything still waiting to be settled, for a task or for all of them."""
        records = (
            self.effects.for_task(task_id) if task_id
            else self._all_uncertain()
        )
        return [r for r in records if r.state is EffectState.UNCERTAIN]

    def can_recover(self, effect: EffectRecord) -> bool:
        """Whether an automatic pass is even worth starting."""
        return self._refusal(effect)[0] is None

    async def reconcile(self, effect_id: str) -> RecoveryOutcome:
        """One pass. Inspect, assess, write it down."""
        effect = self.effects.load(effect_id)
        if effect is None:
            raise GarisError(f"Nie znam efektu {effect_id!r}")
        if effect.state is not EffectState.UNCERTAIN:
            # Nothing to settle. Saying so is not a failure.
            return RecoveryOutcome(
                effect_id, RecoveryStatus.NOT_RECOVERABLE, effect.disposition,
                Verification(goal_met=False),
                reason="Ten efekt nie jest niepewny — nie ma czego ustalać.",
            )

        refusal, reconciler = self._refusal(effect)
        if refusal is not None:
            return self._settle_refusal(effect, refusal)

        assert reconciler is not None
        inspection = reconciler.inspection_for(effect)
        # Canonical identity, from the resolver, before anything runs. Two
        # spellings of one inspector must not read back as two recoveries.
        resolved = self.resolver.resolve(inspection.target)
        canonical = resolved.name

        self._announce(
            Topic.EFFECT_RECOVERY_STARTED, effect,
            status=RecoveryStatus.STILL_UNKNOWN, disposition=effect.disposition,
            verification=Verification(goal_met=False, uncertain=True),
            reason="", inspector=canonical,
        )

        result = await self._inspect(effect, inspection, canonical)

        # The envelope, not this service, decides whether a person is needed.
        if result.approval_request_id:
            return self._settle_refusal(effect, NEEDS_APPROVAL, inspector=canonical)
        if result.denial is not None:
            return self._settle_refusal(effect, POLICY_DENIED, inspector=canonical)
        if not (result.verification.checked and not result.verification.uncertain):
            # The inspection ran and nobody can vouch for what it saw. That is
            # not evidence, so it may not settle anything.
            return self._settle_unknown(
                effect, INSPECTION_UNCHECKED, inspector=canonical,
                inspector_effect_id=result.effect_id,
            )

        assessment = reconciler.assess(effect, _observed(result))
        return self._commit(effect, assessment, canonical, result.effect_id)

    # ---------------------------------------------------------------- refusals

    def _refusal(self, effect: EffectRecord) -> tuple[str | None, Any]:
        """Static checks only — the ones no policy engine would ever disagree with.

        Anything conditional (autonomy level, resource limits, whether this user
        must confirm) belongs to the envelope and is asked there.
        """
        if not self.capabilities.has(effect.capability_id):
            return INSPECTOR_UNKNOWN, None
        capability = self.capabilities.get(effect.capability_id)
        reconciler = getattr(capability, "reconciler", None)
        if reconciler is None:
            return NO_RECONCILER, None

        target = reconciler.inspection_for(effect).target
        resolved = self.resolver.resolve(target)
        if not resolved.known:
            return INSPECTOR_UNKNOWN, None
        if not resolved.runs_here:
            return INSPECTOR_UNSUPPORTED, None
        if resolved.effects:
            # An inspection that changes something is not an inspection.
            return INSPECTOR_EFFECTFUL, None

        inspector = self.capabilities.get(resolved.name)
        if getattr(inspector, "fixture", False) and (
            self.runner.profile is RuntimeProfile.PRODUCTION
        ):
            return INSPECTOR_FIXTURE, None
        if _cannot_verify(inspector):
            # It says up front that nobody checks its result. Believe it.
            return INSPECTOR_UNVERIFIABLE, None
        return None, reconciler

    # -------------------------------------------------------------- inspection

    async def _inspect(
        self, effect: EffectRecord, inspection: Any, canonical: str
    ) -> RunnerResult:
        """The look itself — an ordinary invocation, with a parent.

        Its effect id is fresh rather than derived, and that falls out of the
        existing rule instead of being special-cased here: an inspector declares
        no effects, so `derive_effect_id` gives it a new id. Replaying a stored
        measurement would hand back the reading from before the crash — the very
        number recovery exists to refresh.
        """
        action = Action(
            tool=canonical,
            params=dict(inspection.arguments),
            intent=inspection.purpose or f"Sprawdzam skutek operacji {effect.capability_id}",
            task_id=effect.task_id or None,
            step_key=child_step_key(effect.step_key or None, canonical, 1),
        )
        context = ExecutionContext(
            task_id=effect.task_id or "",
            step_key=action.step_key or "",
            runtime_profile=self.runner.profile,
            # The tree stays readable: this reading exists because of that effect.
            parent_effect_id=effect.effect_id,
            depth=1,
        )
        return await self.runner.run(CapabilityTarget(canonical), action, context)

    # ----------------------------------------------------------------- writing

    def _commit(
        self,
        effect: EffectRecord,
        assessment: RecoveryAssessment,
        inspector: str,
        inspector_effect_id: str,
    ) -> RecoveryOutcome:
        """Rewrite the original effect, append the attempt, announce it — once.

        All three in one transaction. A history that outlives the settlement it
        describes lies after the next crash, and an event about a settlement that
        rolled back is the same untruth pointed outwards.
        """
        state, confirm = _settlement(assessment)

        with self.effects.db.transaction() as conn:
            self.effects.settle_in(
                conn, effect.effect_id,
                ok=state is EffectState.DONE,
                outcome=effect.outcome,
                reason=assessment.reason,
                uncertain=state is EffectState.UNCERTAIN,
                disposition=assessment.disposition,
                verification=assessment.verification.to_dict(),
                retry_requires_confirmation=confirm,
            )
            record = self.recoveries.stage(
                conn, effect.effect_id, assessment,
                inspector=inspector, inspector_effect_id=inspector_effect_id,
            )
            self.audit.stage(
                conn,
                task_id=effect.task_id or None,
                tool=inspector,
                intent=f"Ustalenie skutku operacji {effect.capability_id}",
                effects=[],
                decision="allow",
                rule="recovery",
                params={"effect_id": effect.effect_id},
                outcome=assessment.status.value,
                detail=assessment.reason,
            )
            self.outbox.stage(
                conn,
                Topic.EFFECT_RECOVERY_RESOLVED if assessment.status.resolved
                else Topic.EFFECT_RECOVERY_STILL_UNKNOWN,
                task_id=effect.task_id or "",
                payload=_payload(record),
            )
        self.outbox.drain()
        return RecoveryOutcome(
            effect.effect_id, assessment.status, assessment.disposition,
            assessment.verification, assessment.reason, record,
        )

    def _settle_refusal(
        self, effect: EffectRecord, code: str, *, inspector: str = ""
    ) -> RecoveryOutcome:
        """Nobody may decide this automatically. Say which nobody, and why."""
        return self._record_only(
            effect,
            RecoveryAssessment(
                status=RecoveryStatus.MANUAL_REQUIRED,
                disposition=EffectDisposition.UNKNOWN,
                verification=Verification(goal_met=False, uncertain=True),
                reason=_REASONS[code],
            ),
            Topic.EFFECT_RECOVERY_MANUAL_REQUIRED, inspector, "",
        )

    def _settle_unknown(
        self, effect: EffectRecord, code: str, *, inspector: str,
        inspector_effect_id: str,
    ) -> RecoveryOutcome:
        return self._record_only(
            effect,
            RecoveryAssessment(
                status=RecoveryStatus.STILL_UNKNOWN,
                disposition=EffectDisposition.UNKNOWN,
                verification=Verification(goal_met=False, uncertain=True),
                reason=_REASONS[code],
            ),
            Topic.EFFECT_RECOVERY_STILL_UNKNOWN, inspector, inspector_effect_id,
        )

    def _record_only(
        self, effect: EffectRecord, assessment: RecoveryAssessment, topic: str,
        inspector: str, inspector_effect_id: str,
    ) -> RecoveryOutcome:
        """History and event, and **not one word written to the effect**.

        An attempt that settled nothing must leave the original exactly as it
        was: still uncertain, still un-repeatable, still waiting.
        """
        with self.effects.db.transaction() as conn:
            record = self.recoveries.stage(
                conn, effect.effect_id, assessment,
                inspector=inspector, inspector_effect_id=inspector_effect_id,
            )
            self.outbox.stage(
                conn, topic, task_id=effect.task_id or "", payload=_payload(record),
            )
        self.outbox.drain()
        return RecoveryOutcome(
            effect.effect_id, assessment.status, assessment.disposition,
            assessment.verification, assessment.reason, record,
        )

    def _announce(
        self, topic: str, effect: EffectRecord, **fields: Any
    ) -> None:
        with self.effects.db.transaction() as conn:
            self.outbox.stage(
                conn, topic, task_id=effect.task_id or "",
                payload={
                    "effect_id": effect.effect_id,
                    "capability_id": effect.capability_id,
                    "inspector": fields.get("inspector", ""),
                },
            )
        self.outbox.drain()

    def _all_uncertain(self) -> list[EffectRecord]:
        from ..kernel.effects import _record  # local: one query, one shape

        return [
            _record(row)
            for row in self.effects.db.query(
                "SELECT * FROM effects WHERE state = ? ORDER BY reserved_at",
                (EffectState.UNCERTAIN.value,),
            )
        ]


def _settlement(assessment: RecoveryAssessment) -> tuple[EffectState, bool | None]:
    """How the original effect is rewritten, per status.

    `RESOLVED_GOAL_ONLY` deliberately leaves the row `UNCERTAIN`: the goal is
    measured, the cause is not, and the effect ledger is a record of what GARIS
    did — not of what happens to be true. The verdict rides along, so a task can
    still finish on a goal that was checked.
    """
    if assessment.status is RecoveryStatus.RESOLVED_APPLIED:
        return EffectState.DONE, None
    if assessment.status is RecoveryStatus.RESOLVED_NOT_APPLIED:
        # Honest, and second-hand. `repeatable` reads this, so the row is not
        # re-claimed by the next resume — a person decides.
        return EffectState.FAILED, True
    return EffectState.UNCERTAIN, None


def _observed(result: RunnerResult) -> Observation:
    """The runner's answer, narrowed to what a reconciler may see."""
    return Observation(
        ok=result.ok,
        value=result.value,
        evidence=result.evidence,
        verification=result.verification,
        failure=result.failure,
        effect_id=result.effect_id,
    )


def _payload(record: RecoveryRecord) -> dict[str, Any]:
    """Safe fields only: codes, flags and ids. Never what was read."""
    verification: Mapping[str, Any] = record.verification or {}
    return {
        "recovery_id": record.recovery_id,
        "effect_id": record.effect_id,
        "attempt_number": record.attempt_number,
        "status": record.status.value,
        "disposition": record.disposition.value,
        "inspector": record.inspector,
        "goal_met": bool(verification.get("goal_met")),
        "checked": bool(verification.get("checked")),
        "uncertain": bool(verification.get("uncertain")),
        "evidence_ids": list(record.evidence_ids),
    }


def _cannot_verify(capability: Any) -> bool:
    """Whether the capability says up front that nobody checks its result.

    Only the explicit non-verifier disqualifies an inspector. An empty evidence
    schema does not: a capability may return a structured, checked reading
    without minting a separate evidence id for it, and refusing that would rule
    out most honest readings on a technicality.
    """
    from ..capabilities.base import always_unchecked

    return getattr(capability, "verifier", None) is always_unchecked


_REASONS = {
    NO_RECONCILER: "Ta zdolność nie umie sprawdzić się po fakcie — musisz to ocenić sam.",
    INSPECTOR_EFFECTFUL: "Sprawdzenie musiałoby coś zmienić, więc go nie uruchamiam.",
    INSPECTOR_UNKNOWN: "Nie znam narzędzia, którym miałbym to sprawdzić.",
    INSPECTOR_UNSUPPORTED: "Sprawdzenie nie działa na tym systemie.",
    INSPECTOR_UNVERIFIABLE: "Sprawdzenie samo nie potwierdza swojego wyniku.",
    INSPECTOR_FIXTURE: "To sprawdzenie jest atrapą i nie działa poza testami.",
    NEEDS_APPROVAL: "Sprawdzenie wymaga Twojej zgody, więc go nie uruchomiłem.",
    POLICY_DENIED: "Zasady nie pozwalają mi tego sprawdzić automatycznie.",
    INSPECTION_FAILED: "Sprawdzenie się nie udało — nadal nie wiem, co się stało.",
    INSPECTION_UNCHECKED: "Sprawdzenie nic nie potwierdziło — nadal nie wiem, co się stało.",
    NOT_UNCERTAIN: "Ten efekt nie jest niepewny.",
}


__all__ = ["EffectRecoveryService", "ReconcilerCatalogue", "RecoveryOutcome"]
