"""What to do about an effect nobody can vouch for.

After a crash an effect is `UNCERTAIN`: it was reserved, never settled, and the
world it left behind is unknown. GARIS already refuses to repeat it. That is
safe and it is not enough — "I do not know" is where recovery *starts*, not
where it ends.

So recovery **inspects**. It picks a read-only capability, runs it through the
one envelope, and reads fresh evidence. It never re-runs the original.

## Two truths, two fields

The distinction this module exists to protect, and the one that is easiest to
lose:

    original request:   audio.set(volume=30)
    after the crash:    audio.get() == 30

That proves the goal state **now**. It does not prove GARIS caused it — a person
could have reached for the volume key while the engine was down. Collapsing the
two would let a coincidence be recorded as a completed action, and every later
recovery (files, messages, installs) would inherit the confusion.

So an assessment carries both, separately:

    disposition    — did *this* effect change the world?   (causality)
    verification   — is the goal satisfied, and who says?  (state)

and this is a legitimate, fully-resolved answer:

    disposition            = UNKNOWN
    verification.goal_met  = True
    verification.checked   = True
    verification.uncertain = False

Read as: *the volume is 30 and I measured it; whether my earlier attempt is what
set it, I cannot say.* A task may finish on that. An effect may not be marked
`APPLIED` on it. `APPLIED` needs evidence tied to the original act — a message
id, a receipt, a file that only that operation could have written.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from ..store import Database
from .contracts import CapabilityTarget, EffectDisposition, Failure, Verification
from .effects import EffectRecord


class RecoveryStatus(StrEnum):
    """How a recovery attempt ended. Never what the world looks like — that is
    `disposition` and `verification`, which are separate on purpose."""

    #: Evidence tied to the original act. The effect happened.
    RESOLVED_APPLIED = "resolved_applied"
    #: Evidence that the original act left no trace. It did not happen.
    RESOLVED_NOT_APPLIED = "resolved_not_applied"
    #: The goal is satisfied and measured; who satisfied it is unknown. The
    #: honest answer for anything whose state a person can also change.
    RESOLVED_GOAL_ONLY = "resolved_goal_only"
    #: The inspection ran and settled nothing.
    STILL_UNKNOWN = "still_unknown"
    #: Nobody may decide this automatically — no reconciler, an unsafe
    #: inspector, or the envelope asked for a human.
    MANUAL_REQUIRED = "manual_required"
    #: Recovery does not apply here at all.
    NOT_RECOVERABLE = "not_recoverable"

    @property
    def resolved(self) -> bool:
        return self in (
            RecoveryStatus.RESOLVED_APPLIED,
            RecoveryStatus.RESOLVED_NOT_APPLIED,
            RecoveryStatus.RESOLVED_GOAL_ONLY,
        )

    @property
    def needs_person(self) -> bool:
        return self in (RecoveryStatus.MANUAL_REQUIRED, RecoveryStatus.NOT_RECOVERABLE)


@dataclass(frozen=True, slots=True)
class RecoveryInspection:
    """The read-only look a reconciler wants taken. A request, not a call."""

    target: CapabilityTarget
    arguments: Mapping[str, Any] = field(default_factory=dict)
    #: Why this look answers the question, in one Polish sentence, for the log.
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class Observation:
    """What the inspection came back with, and nothing else.

    Deliberately not `RunnerResult`. A reconciler is authored alongside a
    capability and must not be handed the runner's own result object: this
    module lives in the kernel, which may not know that `runtime` exists, and a
    reconciler with a runner-shaped value in its hands is one refactor away from
    calling something on it.
    """

    ok: bool
    value: Any = None
    evidence: tuple[Mapping[str, Any], ...] = ()
    verification: Verification = field(
        default_factory=lambda: Verification(goal_met=False)
    )
    failure: Failure | None = None
    #: The inspection's own effect id — a fresh read, never the original's.
    effect_id: str = ""


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    """A reconciler's verdict: what happened, and what is true now.

    The constructor refuses the combinations that would erase the distinction
    this module exists for. They are mistakes an author makes in good faith —
    "the volume reads 30, so my set worked" — and a comment would not stop them.
    """

    status: RecoveryStatus
    disposition: EffectDisposition
    verification: Verification
    reason: str = ""
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is RecoveryStatus.RESOLVED_APPLIED:
            if self.disposition is not EffectDisposition.APPLIED:
                raise ValueError(
                    "RESOLVED_APPLIED wymaga disposition=APPLIED — inaczej status "
                    "twierdzi coś, czego dyspozycja nie potwierdza"
                )
        if self.status is RecoveryStatus.RESOLVED_NOT_APPLIED:
            if self.disposition is not EffectDisposition.NOT_APPLIED:
                raise ValueError("RESOLVED_NOT_APPLIED wymaga disposition=NOT_APPLIED")
        if self.status is RecoveryStatus.RESOLVED_GOAL_ONLY:
            # The whole point of this status: the goal is measured, the cause is
            # not. Anything else is one of the other two statuses.
            if self.disposition is not EffectDisposition.UNKNOWN:
                raise ValueError(
                    "RESOLVED_GOAL_ONLY znaczy „cel zmierzony, sprawca nieznany” — "
                    "dyspozycja musi zostać UNKNOWN"
                )
            if not (self.verification.checked and not self.verification.uncertain):
                raise ValueError(
                    "RESOLVED_GOAL_ONLY wymaga sprawdzonego, niepewnego werdyktu"
                )
        if self.status.resolved and self.verification.uncertain:
            raise ValueError("rozstrzygnięcie nie może nieść niepewnego werdyktu")

    @property
    def proves_causation(self) -> bool:
        """Whether this says anything about *who* changed the world."""
        return self.disposition is not EffectDisposition.UNKNOWN


class EffectReconciler(Protocol):
    """Declared by a capability that knows how to check up on itself.

    Two methods, both pure. It chooses a look and it reads what came back. It
    never executes anything, touches persistence, or decides policy — all three
    belong to the envelope, and a reconciler that could reach them would be a
    second execution path wearing a capability's clothes.
    """

    def inspection_for(self, effect: EffectRecord) -> RecoveryInspection: ...

    def assess(
        self, effect: EffectRecord, observation: Observation
    ) -> RecoveryAssessment: ...


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    """One attempt at settling one uncertain effect. Never overwritten."""

    recovery_id: str
    effect_id: str
    attempt_number: int
    status: RecoveryStatus
    disposition: EffectDisposition
    reason: str = ""
    #: Canonical, as the resolver returned it — never the alias a caller typed.
    inspector: str = ""
    inspector_effect_id: str = ""
    verification: Mapping[str, Any] | None = None
    evidence_ids: tuple[str, ...] = ()
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovery_id": self.recovery_id,
            "effect_id": self.effect_id,
            "attempt_number": self.attempt_number,
            "status": self.status.value,
            "disposition": self.disposition.value,
            "reason": self.reason,
            "inspector": self.inspector,
            "inspector_effect_id": self.inspector_effect_id,
            "verification": dict(self.verification) if self.verification else None,
            "evidence_ids": list(self.evidence_ids),
            "created_at": self.created_at,
        }


class RecoveryStore:
    """Append-only history of recovery attempts. Owns one table and no policy."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def stage(
        self,
        conn: Any,
        effect_id: str,
        assessment: RecoveryAssessment,
        *,
        inspector: str = "",
        inspector_effect_id: str = "",
        attempt_number: int | None = None,
    ) -> RecoveryRecord:
        """Write one attempt inside the caller's transaction.

        Inside, so the attempt, the effect it settles, its audit row and its
        event land together or not at all. A history that can outlive the
        settlement it describes is a history that lies after a crash.
        """
        number = (
            attempt_number if attempt_number is not None
            else self._next_attempt(conn, effect_id)
        )
        record = RecoveryRecord(
            recovery_id="rec-" + uuid.uuid4().hex[:26],
            effect_id=effect_id,
            attempt_number=number,
            status=assessment.status,
            disposition=assessment.disposition,
            reason=assessment.reason,
            inspector=inspector,
            inspector_effect_id=inspector_effect_id,
            verification=assessment.verification.to_dict(),
            evidence_ids=assessment.evidence_ids,
            created_at=time.time(),
        )
        conn.execute(
            "INSERT INTO effect_recoveries(recovery_id, effect_id, attempt_number,"
            " inspector, inspector_effect_id, status, disposition, verification,"
            " evidence_ids, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.recovery_id, record.effect_id, record.attempt_number,
                record.inspector, record.inspector_effect_id, record.status.value,
                record.disposition.value,
                json.dumps(record.verification, ensure_ascii=False, default=repr),
                json.dumps(list(record.evidence_ids), ensure_ascii=False),
                record.reason, record.created_at,
            ),
        )
        return record

    def history(self, effect_id: str) -> list[RecoveryRecord]:
        """Every attempt, oldest first."""
        return [
            _recovery(row)
            for row in self.db.query(
                "SELECT * FROM effect_recoveries WHERE effect_id = ?"
                " ORDER BY attempt_number, created_at",
                (effect_id,),
            )
        ]

    def latest(self, effect_id: str) -> RecoveryRecord | None:
        attempts = self.history(effect_id)
        return attempts[-1] if attempts else None

    def _next_attempt(self, conn: Any, effect_id: str) -> int:
        row = conn.execute(
            "SELECT MAX(attempt_number) AS n FROM effect_recoveries WHERE effect_id = ?",
            (effect_id,),
        ).fetchone()
        return int(row["n"] or 0) + 1


def _recovery(row: Any) -> RecoveryRecord:
    return RecoveryRecord(
        recovery_id=row["recovery_id"],
        effect_id=row["effect_id"],
        attempt_number=int(row["attempt_number"]),
        status=RecoveryStatus(row["status"]),
        disposition=EffectDisposition(row["disposition"]),
        reason=row["reason"] or "",
        inspector=row["inspector"] or "",
        inspector_effect_id=row["inspector_effect_id"] or "",
        verification=json.loads(row["verification"]) if row["verification"] else None,
        evidence_ids=tuple(json.loads(row["evidence_ids"] or "[]")),
        created_at=row["created_at"],
    )


def evidence_ids_of(evidence: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Identifiers only. Evidence bodies stay in the effect record.

    An event carrying a file's contents or a token would leak it to every
    consumer of the bus; an id says the same thing to anyone entitled to look
    the record up.
    """
    return tuple(
        str(item["id"]) for item in evidence if isinstance(item, Mapping) and item.get("id")
    )


__all__ = [
    "EffectReconciler",
    "Observation",
    "RecoveryAssessment",
    "RecoveryInspection",
    "RecoveryRecord",
    "RecoveryStatus",
    "RecoveryStore",
    "evidence_ids_of",
]
