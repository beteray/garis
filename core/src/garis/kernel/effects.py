"""What GARIS did to the world, written down before it does it.

Idempotence used to live in a dict on the capability registry. That survives a
retry and nothing else: a crash between launching a program and journalling the
launch loses the record, and the resumed task launches it again. For a disk
reading nobody notices. For "install this" or "send that", it is the difference
between doing a thing once and doing it twice.

So an effect is **reserved before the action**, in the database, under a unique
key. What that buys, in the order it matters:

  * two workers cannot perform the same effect — the second insert loses;
  * a successful effect is never repeated, only replayed from its record;
  * an effect reserved but never settled — the shape a crash leaves — comes back
    as *uncertain*, not as free to retry, because nobody knows whether the world
    moved;
  * the same effect id with different arguments is refused, so a reused id
    cannot silently stand in for a different act.

Uncertain is a first-class answer here. The honest thing to tell someone after a
crash mid-install is "I do not know whether that happened", and the machinery
has to be able to hold that rather than rounding it to yes or no.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..errors import StoreError
from ..store import Database
from .contracts import CapabilityError, EffectDisposition, EvidenceRecord, Failure


class EffectState(StrEnum):
    RESERVED = "reserved"      # claimed, not yet settled: in flight, or a crash
    DONE = "done"
    FAILED = "failed"
    UNCERTAIN = "uncertain"    # it may have happened; nothing can tell

    @property
    def settled(self) -> bool:
        return self is not EffectState.RESERVED


@dataclass(frozen=True, slots=True)
class EffectRecord:
    effect_id: str
    capability_id: str
    state: EffectState
    task_id: str = ""
    step_key: str = ""
    arguments_hash: str = ""
    outcome: Mapping[str, Any] | None = None
    evidence: tuple[Mapping[str, Any], ...] = ()
    reason: str = ""
    reserved_at: float = 0.0
    settled_at: float | None = None
    #: What happened outside GARIS. The only field that may license a retry.
    disposition: EffectDisposition = EffectDisposition.UNKNOWN
    attempt_number: int = 1
    #: Every settled attempt, oldest first, append-only. A retry that overwrote
    #: the previous attempt would erase the reason it was retried.
    attempts: tuple[Mapping[str, Any], ...] = ()
    #: The verifier's whole verdict, stored so a replay hands back what was
    #: measured rather than what the state implies.
    verification: Mapping[str, Any] | None = None

    @property
    def repeatable(self) -> bool:
        """May the external effect be attempted again?

        Asked of the disposition, not of the state. "It failed" is not "nothing
        happened": an executor that changed something and then failed on the
        readback has failed *and* applied, and running it again is how one
        message becomes two. Only a failure that never reached the executor, or
        one that proved it changed nothing, may be repeated.
        """
        return self.state is EffectState.FAILED and self.disposition.permits_retry


@dataclass(frozen=True, slots=True)
class EffectReservation:
    """The answer to "may I proceed?" — and if not, what is already known."""

    granted: bool
    record: EffectRecord

    @property
    def existing(self) -> EffectRecord | None:
        return None if self.granted else self.record


def arguments_hash(args: Mapping[str, Any]) -> str:
    """A stable fingerprint of what was asked for.

    Sorted keys and a canonical dump, so the same call hashes the same across
    processes; anything unserialisable falls back to its repr rather than
    exploding, because a hash that raises is worse than a hash that is coarse.
    """
    try:
        canonical = json.dumps(args, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):  # pragma: no cover - default=repr covers most
        canonical = repr(sorted(args.items()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class EffectStore:
    """Durable effect identity. Owns one table and no policy."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------------------------------------------------------------- reserve

    def reserve(
        self,
        effect_id: str,
        *,
        capability_id: str,
        task_id: str = "",
        step_key: str = "",
        args: Mapping[str, Any] | None = None,
    ) -> EffectReservation:
        """Claim the right to perform this effect, or find out who already did.

        The insert is the lock: `effect_id` is the primary key, so two callers
        racing for the same effect resolve in SQLite rather than in Python, and
        the loser reads the winner's row.
        """
        with self.db.transaction() as conn:
            return self.reserve_in(
                conn, effect_id, capability_id=capability_id, task_id=task_id,
                step_key=step_key, args=args,
            )

    def reserve_in(
        self,
        conn: Any,
        effect_id: str,
        *,
        capability_id: str,
        task_id: str = "",
        step_key: str = "",
        args: Mapping[str, Any] | None = None,
    ) -> EffectReservation:
        """Claim inside a caller's transaction, so the reservation and the event
        announcing it either both land or neither does."""
        fingerprint = arguments_hash(args or {})
        now = time.time()

        row = conn.execute(
            "SELECT * FROM effects WHERE effect_id = ?", (effect_id,)
        ).fetchone()
        if row is not None:
            existing = _record(row)
            if existing.arguments_hash and existing.arguments_hash != fingerprint:
                # The same id for a different act. Refusing is the only safe
                # answer: replaying would return someone else's result, and
                # proceeding would perform an effect under a used identity.
                raise CapabilityError(
                    Failure.INVALID_INPUT,
                    f"efekt {effect_id!r} został już zarezerwowany dla innych "
                    f"argumentów tej samej zdolności",
                )
            if not existing.repeatable:
                return EffectReservation(granted=False, record=existing)
            # A failure that provably changed nothing may be tried again under
            # the same logical identity — that is what the deterministic effect
            # id is for. The previous attempt is appended to the history first:
            # overwriting it would erase the evidence of why the retry happened.
            attempt = existing.attempt_number + 1
            conn.execute(
                "UPDATE effects SET state = ?, reserved_at = ?, settled_at = NULL,"
                " outcome = NULL, reason = '', attempt_number = ?, attempts = ?,"
                " verification = NULL WHERE effect_id = ?",
                (
                    EffectState.RESERVED.value, now, attempt,
                    json.dumps(
                        [*existing.attempts, _attempt_entry(existing)],
                        ensure_ascii=False, default=repr,
                    ),
                    effect_id,
                ),
            )
            return EffectReservation(
                granted=True,
                record=EffectRecord(
                    effect_id=effect_id,
                    capability_id=capability_id,
                    state=EffectState.RESERVED,
                    task_id=task_id,
                    step_key=step_key,
                    arguments_hash=fingerprint,
                    reserved_at=now,
                    attempt_number=attempt,
                    attempts=(*existing.attempts, _attempt_entry(existing)),
                ),
            )
        else:
            try:
                conn.execute(
                    "INSERT INTO effects(effect_id, task_id, step_key, capability_id,"
                    " arguments_hash, state, reserved_at) VALUES (?,?,?,?,?,?,?)",
                    (effect_id, task_id, step_key, capability_id, fingerprint,
                     EffectState.RESERVED.value, now),
                )
            except Exception as exc:  # pragma: no cover - the race, rarely hit
                raise StoreError(f"Nie udało się zarezerwować efektu: {exc}") from exc

        return EffectReservation(
            granted=True,
            record=EffectRecord(
                effect_id=effect_id,
                capability_id=capability_id,
                state=EffectState.RESERVED,
                task_id=task_id,
                step_key=step_key,
                arguments_hash=fingerprint,
                reserved_at=now,
            ),
        )

    # ----------------------------------------------------------------- settle

    def complete(
        self,
        effect_id: str,
        *,
        ok: bool,
        outcome: Mapping[str, Any] | None = None,
        evidence: Sequence[EvidenceRecord] = (),
        reason: str = "",
        disposition: EffectDisposition | None = None,
    ) -> None:
        state = EffectState.DONE if ok else EffectState.FAILED
        if disposition is None:
            # A success applied; a failure with nothing said about it could have
            # applied. Guessing `NOT_APPLIED` here is the one guess that would
            # let a partial effect be run again.
            disposition = (
                EffectDisposition.APPLIED if ok else EffectDisposition.UNKNOWN
            )
        self._settle(effect_id, state, outcome=outcome, evidence=evidence,
                     reason=reason, disposition=disposition)

    def mark_uncertain(self, effect_id: str, reason: str) -> None:
        """It may have happened. Say so, and stop anything from retrying it."""
        self._settle(effect_id, EffectState.UNCERTAIN, reason=reason,
                     disposition=EffectDisposition.UNKNOWN)

    def settle_in(
        self,
        conn: Any,
        effect_id: str,
        *,
        ok: bool,
        outcome: Mapping[str, Any] | None = None,
        evidence: Sequence[EvidenceRecord] = (),
        reason: str = "",
        uncertain: bool = False,
        disposition: EffectDisposition = EffectDisposition.UNKNOWN,
        verification: Mapping[str, Any] | None = None,
    ) -> EffectState:
        """Settle inside a caller's transaction.

        This is what makes "an effect is never successful before its evidence
        committed" true rather than intended: the state, the evidence, the
        verdict and the event that announces them go in together, and a failure
        anywhere in the transaction leaves the effect unsettled — which the
        startup sweep then reads as uncertain, correctly.

        ``state`` records how the row was settled; ``disposition`` records what
        happened outside GARIS. `DONE` means "settled truthfully", not "the user
        got what they asked for" — a checked postcondition failure is `DONE`,
        `APPLIED`, and `goal_met=False`, all at once and without contradiction.
        """
        state = (
            EffectState.UNCERTAIN if uncertain
            else EffectState.DONE if ok
            else EffectState.FAILED
        )
        self._write(conn, effect_id, state, outcome=outcome, evidence=evidence,
                    reason=reason, disposition=disposition, verification=verification)
        return state

    def _settle(
        self,
        effect_id: str,
        state: EffectState,
        *,
        outcome: Mapping[str, Any] | None = None,
        evidence: Sequence[EvidenceRecord] = (),
        reason: str = "",
        disposition: EffectDisposition = EffectDisposition.UNKNOWN,
    ) -> None:
        with self.db.transaction() as conn:
            self._write(conn, effect_id, state, outcome=outcome, evidence=evidence,
                        reason=reason, disposition=disposition)

    def _write(
        self,
        conn: Any,
        effect_id: str,
        state: EffectState,
        *,
        outcome: Mapping[str, Any] | None = None,
        evidence: Sequence[EvidenceRecord] = (),
        reason: str = "",
        disposition: EffectDisposition = EffectDisposition.UNKNOWN,
        verification: Mapping[str, Any] | None = None,
    ) -> None:
        conn.execute(
            "UPDATE effects SET state = ?, outcome = ?, evidence = ?, reason = ?,"
            " settled_at = ?, disposition = ?, verification = ? WHERE effect_id = ?",
            (
                state.value,
                json.dumps(outcome, ensure_ascii=False, default=repr)
                if outcome is not None else None,
                json.dumps([e.to_dict() for e in evidence], ensure_ascii=False,
                           default=repr),
                reason,
                time.time(),
                disposition.value,
                json.dumps(verification, ensure_ascii=False, default=repr)
                if verification is not None else None,
                effect_id,
            ),
        )

    # ------------------------------------------------------------------ reads

    def load(self, effect_id: str) -> EffectRecord | None:
        row = self.db.one("SELECT * FROM effects WHERE effect_id = ?", (effect_id,))
        return _record(row) if row is not None else None

    def for_task(self, task_id: str) -> list[EffectRecord]:
        return [
            _record(row)
            for row in self.db.query(
                "SELECT * FROM effects WHERE task_id = ? ORDER BY reserved_at", (task_id,)
            )
        ]

    def sweep_unsettled(self, *, before: float | None = None) -> list[EffectRecord]:
        """After a restart: everything reserved and never settled is uncertain.

        Called once at startup. A reservation with no settlement is precisely the
        footprint of a process that died mid-action, and the world it left is
        unknown — which is a state this system can now name instead of guessing.
        """
        cutoff = before if before is not None else time.time()
        rows = self.db.query(
            "SELECT * FROM effects WHERE state = ? AND reserved_at <= ?",
            (EffectState.RESERVED.value, cutoff),
        )
        stranded = [_record(row) for row in rows]
        for record in stranded:
            self.mark_uncertain(
                record.effect_id,
                "Proces zakończył się w trakcie tej operacji — nie wiem, czy doszła do skutku.",
            )
        return stranded


def _attempt_entry(record: EffectRecord) -> dict[str, Any]:
    """One line of history, written when an attempt is superseded by another."""
    return {
        "attempt_number": record.attempt_number,
        "state": record.state.value,
        "disposition": record.disposition.value,
        "reason": record.reason,
        "outcome": dict(record.outcome) if record.outcome else None,
        "evidence": [dict(e) for e in record.evidence],
        "verification": dict(record.verification) if record.verification else None,
        "settled_at": record.settled_at,
    }


def _record(row: Any) -> EffectRecord:
    keys = row.keys()
    return EffectRecord(
        effect_id=row["effect_id"],
        capability_id=row["capability_id"],
        state=EffectState(row["state"]),
        task_id=row["task_id"] or "",
        step_key=row["step_key"] or "",
        arguments_hash=row["arguments_hash"] or "",
        outcome=json.loads(row["outcome"]) if row["outcome"] else None,
        evidence=tuple(json.loads(row["evidence"] or "[]")),
        reason=row["reason"] or "",
        reserved_at=row["reserved_at"],
        settled_at=row["settled_at"],
        # Rows written before dispositions existed carry 'unknown', which is the
        # truth about them: nobody recorded whether the world moved.
        disposition=EffectDisposition(
            row["disposition"] if "disposition" in keys and row["disposition"]
            else EffectDisposition.UNKNOWN.value
        ),
        attempt_number=(
            int(row["attempt_number"]) if "attempt_number" in keys else 1
        ),
        attempts=tuple(
            json.loads(row["attempts"] or "[]") if "attempts" in keys else []
        ),
        verification=(
            json.loads(row["verification"])
            if "verification" in keys and row["verification"] else None
        ),
    )


__all__ = [
    "EffectDisposition",
    "EffectRecord",
    "EffectReservation",
    "EffectState",
    "EffectStore",
    "arguments_hash",
]
