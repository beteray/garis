"""Approvals — durable, transferable "yes".

An approval is a row in the database, not a promise in memory. That is what makes
the spec's timeline possible: a task may run for days, hit a payment step at
03:00, wait, survive a reboot, and be approved from a phone in the morning.

Matching is by action fingerprint, so the retry that follows an approval is
recognised as the same operation and the user is not asked twice.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..errors import ApprovalDenied
from ..events import EventBus, Topic
from ..store import Database, dumps, loads
from .action import Action, Effect
from .policy import Verdict
from .registry import ToolSpec


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass(slots=True)
class ApprovalRequest:
    id: str
    tool: str
    prompt: str
    fingerprint: str
    state: ApprovalState
    requested_at: float
    task_id: str | None = None
    effects: tuple[str, ...] = ()
    params: dict[str, Any] | None = None
    consumed: bool = False
    resolved_at: float | None = None
    resolved_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "tool": self.tool,
            "prompt": self.prompt,
            "effects": list(self.effects),
            "params": self.params or {},
            "state": self.state.value,
            "requested_at": self.requested_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
        }


class ApprovalBroker:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self.db = db
        self.bus = bus
        self._waiters: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------ create

    def request(
        self,
        action: Action,
        spec: ToolSpec,
        verdict: Verdict,
        *,
        redacted_params: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        """Compatibility adapter over :meth:`request_for`."""
        return self.request_for(
            action, verdict,
            effects=tuple(sorted(e.value for e in spec.effects)),
            redacted_params=redacted_params,
        )

    def request_for(
        self,
        action: Action,
        verdict: Verdict,
        *,
        effects: tuple[str, ...] = (),
        redacted_params: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        """Open a pending approval, or return the one already waiting for it.

        Takes the effects as values rather than a ``ToolSpec`` so a native
        capability asks the same question, through the same broker, and its
        stored yes matches by the same fingerprint.
        """
        fingerprint = action.fingerprint()
        existing = self.db.one(
            "SELECT * FROM approvals WHERE fingerprint = ? AND state = ? "
            "ORDER BY requested_at DESC LIMIT 1",
            (fingerprint, ApprovalState.PENDING.value),
        )
        if existing is not None:
            return _row_to_request(existing)

        request = ApprovalRequest(
            id=uuid.uuid4().hex,
            tool=action.tool,
            prompt=verdict.prompt or f"Potwierdzasz operację {action.tool}?",
            fingerprint=fingerprint,
            state=ApprovalState.PENDING,
            requested_at=time.time(),
            task_id=action.task_id,
            effects=effects,
            params=redacted_params if redacted_params is not None else dict(action.params),
        )
        self.db.execute(
            "INSERT INTO approvals(id, task_id, tool, prompt, effects, params, fingerprint,"
            " state, consumed, requested_at) VALUES (?,?,?,?,?,?,?,?,0,?)",
            (
                request.id,
                request.task_id,
                request.tool,
                request.prompt,
                dumps(list(request.effects)),
                dumps(request.params),
                request.fingerprint,
                request.state.value,
                request.requested_at,
            ),
        )
        self.bus.emit(
            Topic.APPROVAL_REQUESTED,
            approval_id=request.id,
            task_id=request.task_id,
            tool=request.tool,
            prompt=request.prompt,
            effects=list(request.effects),
        )
        return request

    # ------------------------------------------------------------------ resolve

    def resolve(self, approval_id: str, approved: bool, *, by: str = "user") -> ApprovalRequest:
        row = self.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if row is None:
            raise ApprovalDenied(f"Nie ma zgody o id {approval_id}")
        state = ApprovalState.APPROVED if approved else ApprovalState.DENIED
        now = time.time()
        self.db.execute(
            "UPDATE approvals SET state = ?, resolved_at = ?, resolved_by = ? WHERE id = ?",
            (state.value, now, by, approval_id),
        )
        self.bus.emit(
            Topic.APPROVAL_RESOLVED,
            approval_id=approval_id,
            task_id=row["task_id"],
            approved=approved,
            by=by,
        )
        event = self._waiters.get(approval_id)
        if event is not None:
            event.set()
        request = _row_to_request(row)
        request.state = state
        request.resolved_at = now
        request.resolved_by = by
        return request

    async def wait(self, approval_id: str, *, timeout: float | None = None) -> bool:
        """Await a decision. Returns True if approved.

        Used by interactive paths (voice, CLI). Background tasks do not wait here —
        they park in the BLOCKED state and are resumed by the supervisor, which is
        what lets them outlive the process.
        """
        event = self._waiters.setdefault(approval_id, asyncio.Event())
        try:
            current = self.get(approval_id)
            if current is not None and current.state is not ApprovalState.PENDING:
                return current.state is ApprovalState.APPROVED
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        finally:
            self._waiters.pop(approval_id, None)
        refreshed = self.get(approval_id)
        return refreshed is not None and refreshed.state is ApprovalState.APPROVED

    # ------------------------------------------------------------------- query

    def get(self, approval_id: str) -> ApprovalRequest | None:
        row = self.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return _row_to_request(row) if row else None

    def granted_for(self, fingerprint: str) -> ApprovalRequest | None:
        """An approved, not-yet-used yes for this exact operation."""
        row = self.db.one(
            "SELECT * FROM approvals WHERE fingerprint = ? AND state = ? AND consumed = 0 "
            "ORDER BY resolved_at DESC LIMIT 1",
            (fingerprint, ApprovalState.APPROVED.value),
        )
        return _row_to_request(row) if row else None

    def denied_for(self, fingerprint: str) -> ApprovalRequest | None:
        row = self.db.one(
            "SELECT * FROM approvals WHERE fingerprint = ? AND state = ? AND consumed = 0 "
            "ORDER BY resolved_at DESC LIMIT 1",
            (fingerprint, ApprovalState.DENIED.value),
        )
        return _row_to_request(row) if row else None

    def consume(self, approval_id: str) -> None:
        """Mark a yes as spent, so one approval authorises exactly one action."""
        self.db.execute("UPDATE approvals SET consumed = 1 WHERE id = ?", (approval_id,))

    def pending(self, *, task_id: str | None = None) -> list[ApprovalRequest]:
        if task_id is None:
            rows = self.db.query(
                "SELECT * FROM approvals WHERE state = ? ORDER BY requested_at",
                (ApprovalState.PENDING.value,),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM approvals WHERE state = ? AND task_id = ? ORDER BY requested_at",
                (ApprovalState.PENDING.value, task_id),
            )
        return [_row_to_request(r) for r in rows]

    def expire_older_than(self, seconds: float) -> int:
        cutoff = time.time() - seconds
        cur = self.db.execute(
            "UPDATE approvals SET state = ? WHERE state = ? AND requested_at < ?",
            (ApprovalState.EXPIRED.value, ApprovalState.PENDING.value, cutoff),
        )
        return cur.rowcount or 0


def _row_to_request(row: Any) -> ApprovalRequest:
    return ApprovalRequest(
        id=row["id"],
        tool=row["tool"],
        prompt=row["prompt"],
        fingerprint=row["fingerprint"],
        state=ApprovalState(row["state"]),
        requested_at=row["requested_at"],
        task_id=row["task_id"],
        effects=tuple(loads(row["effects"], [])),
        params=loads(row["params"], {}),
        consumed=bool(row["consumed"]),
        resolved_at=row["resolved_at"],
        resolved_by=row["resolved_by"],
    )


def effects_of(spec: ToolSpec) -> tuple[str, ...]:
    return tuple(sorted(e.value for e in spec.effects))


def label_effects(effects: tuple[str, ...]) -> str:
    labels = []
    for name in effects:
        try:
            labels.append(Effect(name).label_pl)
        except ValueError:
            labels.append(name)
    return ", ".join(labels)


__all__ = ["ApprovalBroker", "ApprovalRequest", "ApprovalState", "label_effects"]
