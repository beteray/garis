"""Reference capabilities for recovery, and nothing that ships.

No real mutating Windows capability exists yet, so the recovery machinery is
exercised against deliberately narrow stand-ins — one per outcome it has to be
able to reach, plus two that must be refused. Every one carries `fixture=True`,
which `profiles.validate` already refuses to start PRODUCTION with, by identity
rather than by counting.

The shape they model is the one that matters first: **set a value, crash, read
the value back**. `audio.set` will be the first real instance of it, and the
scenario that must not happen is GARIS setting the volume twice because it
could not remember whether the first attempt landed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..kernel.contracts import CapabilityTarget, Effect, EffectDisposition, Verification
from ..kernel.effects import EffectRecord
from ..kernel.recovery import (
    Observation,
    RecoveryAssessment,
    RecoveryInspection,
    RecoveryStatus,
    evidence_ids_of,
)
from .base import (
    Capability,
    Field,
    Invocation,
    Outcome,
    Permission,
    Risk,
    always_unchecked,
    evidence_verifier,
)

#: What the "world" currently reads. Tests set it to stage a crash aftermath.
WORLD: dict[str, Any] = {"level": None, "receipt": None}


def reset(level: Any = None, receipt: Any = None) -> None:
    WORLD["level"] = level
    WORLD["receipt"] = receipt


# ------------------------------------------------------------------ inspectors


async def _read_level(invocation: Invocation) -> Outcome:
    """A plain reading. Changes nothing, and says what it saw."""
    level = WORLD["level"]
    if level is None:
        return Outcome(ok=False, error="nie umiem teraz odczytać poziomu",
                       uncertain=True)
    return Outcome(
        ok=True,
        value={"level": level, "receipt": WORLD["receipt"]},
        evidence={"level": level, "id": "ev-level"},
    )


READ_LEVEL = Capability(
    id="fixture.level.read",
    version=1,
    summary="Odczytuje bieżący poziom.",
    risk=Risk.READ,
    permission=Permission.NONE,
    effects=frozenset(),
    executor=_read_level,
    verifier=evidence_verifier("Odczytałem poziom."),
    outputs=(Field("level", "int", "Zmierzony poziom"),),
    evidence=(Field("level", "int", "Zmierzony poziom"),),
    fixture=True,
)

#: Declares an effect, so it is not an inspection at all.
WRITING_INSPECTOR = Capability(
    id="fixture.level.touch",
    version=1,
    summary="Odczytuje i przy okazji zapisuje.",
    risk=Risk.REVERSIBLE,
    permission=Permission.NONE,
    effects=frozenset({Effect.WRITE}),
    executor=_read_level,
    verifier=evidence_verifier(),
    evidence=(Field("level", "int", "Poziom"),),
    fixture=True,
)

#: Says up front that nobody checks its result.
UNVERIFIABLE_INSPECTOR = Capability(
    id="fixture.level.guess",
    version=1,
    summary="Zgaduje poziom.",
    risk=Risk.READ,
    permission=Permission.NONE,
    effects=frozenset(),
    executor=_read_level,
    verifier=always_unchecked,
    fixture=True,
)

#: Needs a person before it will run.
APPROVING_INSPECTOR = Capability(
    id="fixture.level.ask",
    version=1,
    summary="Odczytuje poziom, ale pyta o zgodę.",
    risk=Risk.READ,
    permission=Permission.NONE,
    effects=frozenset(),
    executor=_read_level,
    verifier=evidence_verifier(),
    evidence=(Field("level", "int", "Poziom"),),
    needs_approval=True,
    fixture=True,
)

#: Exists, but not on this machine.
ELSEWHERE_INSPECTOR = Capability(
    id="fixture.level.elsewhere",
    version=1,
    summary="Odczytuje poziom na innym systemie.",
    risk=Risk.READ,
    permission=Permission.NONE,
    effects=frozenset(),
    executor=_read_level,
    verifier=evidence_verifier(),
    evidence=(Field("level", "int", "Poziom"),),
    platforms=("nonesuch",),
    fixture=True,
)


# ---------------------------------------------------------------- reconcilers


@dataclass(frozen=True, slots=True)
class LevelReconciler:
    """The honest one, and the reason `RESOLVED_GOAL_ONLY` exists.

    A level reading tells us what the level *is*. It cannot tell us who set it —
    a person can turn a dial while the engine is down. So this reconciler
    reports the goal as measured and leaves causality `UNKNOWN`, unless the
    reading carries a receipt that only the original operation could have left.
    """

    inspector: str = "fixture.level.read"

    def inspection_for(self, effect: EffectRecord) -> RecoveryInspection:
        return RecoveryInspection(
            target=CapabilityTarget(self.inspector),
            arguments={},
            purpose="Sprawdzam, jaki jest teraz poziom.",
        )

    def assess(
        self, effect: EffectRecord, observation: Observation
    ) -> RecoveryAssessment:
        wanted = _wanted(effect)
        seen = _seen(observation, "level")
        ids = evidence_ids_of(observation.evidence)

        if seen is None:
            return RecoveryAssessment(
                RecoveryStatus.STILL_UNKNOWN, EffectDisposition.UNKNOWN,
                Verification(goal_met=False, uncertain=True),
                reason="Odczyt nie zawierał poziomu.",
            )

        receipt = _seen(observation, "receipt")
        if receipt and receipt == effect.effect_id:
            # A trace only that operation could have left. This is what earns
            # APPLIED — not the value matching.
            return RecoveryAssessment(
                RecoveryStatus.RESOLVED_APPLIED, EffectDisposition.APPLIED,
                Verification(
                    goal_met=seen == wanted, checked=True, checked_by="rules",
                    reason=f"Znalazłem ślad tej operacji; poziom wynosi {seen}.",
                ),
                reason=f"Ślad {receipt} pochodzi z tej operacji.",
                evidence_ids=ids,
            )

        if seen == wanted:
            # The state is right. Who made it right is not knowable from here,
            # and saying otherwise would be the lie this whole module prevents.
            return RecoveryAssessment(
                RecoveryStatus.RESOLVED_GOAL_ONLY, EffectDisposition.UNKNOWN,
                Verification(
                    goal_met=True, checked=True, checked_by="rules",
                    reason=f"Poziom wynosi {seen}, czyli tyle, ile miał wynosić.",
                ),
                reason="Cel jest osiągnięty; nie wiem, czy to moja operacja go ustawiła.",
                evidence_ids=ids,
            )

        return RecoveryAssessment(
            RecoveryStatus.RESOLVED_NOT_APPLIED, EffectDisposition.NOT_APPLIED,
            Verification(
                goal_met=False, checked=True, checked_by="rules",
                reason=f"Poziom wynosi {seen}, a miał wynosić {wanted}.",
                unmet=(f"poziom {wanted}",),
            ),
            reason="Poziom jest nietknięty — ta operacja nie doszła do skutku.",
            evidence_ids=ids,
        )


@dataclass(frozen=True, slots=True)
class MissedGoalReconciler(LevelReconciler):
    """Proves the act happened and missed: asked for 30, measured 33."""

    def assess(
        self, effect: EffectRecord, observation: Observation
    ) -> RecoveryAssessment:
        seen = _seen(observation, "level")
        wanted = _wanted(effect)
        return RecoveryAssessment(
            RecoveryStatus.RESOLVED_APPLIED, EffectDisposition.APPLIED,
            Verification(
                goal_met=False, checked=True, checked_by="rules",
                reason=f"Ustawiłem poziom, ale wyszło {seen} zamiast {wanted}.",
                unmet=(f"poziom {wanted}",),
            ),
            reason="Operacja zadziałała, ale nie osiągnęła celu.",
            evidence_ids=evidence_ids_of(observation.evidence),
        )


@dataclass(frozen=True, slots=True)
class UnsafeInspectorReconciler(LevelReconciler):
    inspector: str = "fixture.level.touch"


@dataclass(frozen=True, slots=True)
class UnverifiableInspectorReconciler(LevelReconciler):
    inspector: str = "fixture.level.guess"


@dataclass(frozen=True, slots=True)
class ApprovingInspectorReconciler(LevelReconciler):
    inspector: str = "fixture.level.ask"


@dataclass(frozen=True, slots=True)
class ElsewhereInspectorReconciler(LevelReconciler):
    inspector: str = "fixture.level.elsewhere"


# ------------------------------------------------------------ the mutators


async def _set_level(invocation: Invocation) -> Outcome:
    """Never actually called during recovery. That is the point of the tests."""
    WORLD["level"] = invocation.get("level")
    return Outcome(ok=True, value={"level": WORLD["level"]},
                   evidence={"level": WORLD["level"]})


def _mutator(capability_id: str, reconciler: Any) -> Capability:
    return Capability(
        id=capability_id,
        version=1,
        summary="Ustawia poziom.",
        risk=Risk.REVERSIBLE,
        permission=Permission.NONE,
        effects=frozenset({Effect.WRITE}),
        executor=_set_level,
        verifier=evidence_verifier(),
        inputs=(Field("level", "int", "Docelowy poziom", required=False),),
        evidence=(Field("level", "int", "Poziom po zmianie"),),
        reconciler=reconciler,
        fixture=True,
    )


SET_LEVEL = _mutator("fixture.level.set", LevelReconciler())
SET_LEVEL_MISSED = _mutator("fixture.level.set_missed", MissedGoalReconciler())
SET_LEVEL_UNSAFE = _mutator("fixture.level.set_unsafe", UnsafeInspectorReconciler())
SET_LEVEL_UNVERIFIABLE = _mutator(
    "fixture.level.set_unverifiable", UnverifiableInspectorReconciler()
)
SET_LEVEL_APPROVING = _mutator(
    "fixture.level.set_approving", ApprovingInspectorReconciler()
)
SET_LEVEL_ELSEWHERE = _mutator(
    "fixture.level.set_elsewhere", ElsewhereInspectorReconciler()
)
#: No reconciler at all — recovery must say so rather than invent one.
SET_LEVEL_BLIND = Capability(
    id="fixture.level.set_blind",
    version=1,
    summary="Ustawia poziom i nie umie się sprawdzić.",
    risk=Risk.REVERSIBLE,
    permission=Permission.NONE,
    effects=frozenset({Effect.WRITE}),
    executor=_set_level,
    verifier=evidence_verifier(),
    evidence=(Field("level", "int", "Poziom"),),
    fixture=True,
)

ALL: tuple[Capability, ...] = (
    READ_LEVEL, WRITING_INSPECTOR, UNVERIFIABLE_INSPECTOR, APPROVING_INSPECTOR,
    ELSEWHERE_INSPECTOR, SET_LEVEL, SET_LEVEL_MISSED, SET_LEVEL_UNSAFE,
    SET_LEVEL_UNVERIFIABLE, SET_LEVEL_APPROVING, SET_LEVEL_ELSEWHERE,
    SET_LEVEL_BLIND,
)


def _wanted(effect: EffectRecord) -> Any:
    outcome: Mapping[str, Any] = effect.outcome or {}
    requested = outcome.get("requested") if isinstance(outcome, Mapping) else None
    return requested if requested is not None else 30


def _seen(observation: Observation, key: str) -> Any:
    value = observation.value
    return value.get(key) if isinstance(value, Mapping) else None


__all__ = ["ALL", "WORLD", "LevelReconciler", "reset"]
