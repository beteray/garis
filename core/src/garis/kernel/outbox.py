"""Events that exist because something was committed, and for no other reason.

"Persist the change, then emit the event" has a window in it. A crash inside
that window leaves a fact nobody was told about; reverse the order and it leaves
an announcement about something that never happened. The second is worse — it is
the same family of untruth as reporting a task verified because a stub said so —
so the write and its event go into one transaction, and publication happens
afterwards from what committed.

Delivery is **at-least-once**, and this module says so rather than implying
better. A dispatcher that crashes between publishing and marking published will
publish again on the next drain. Consumers deduplicate on `event_id`; ordering
within a task is `sequence`, which is allocated inside the same transaction as
the fact.

Nothing here knows what a topic means. It moves committed rows to the existing
`EventBus`, so the WebSocket and the window keep seeing the strings they always
saw.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from ..events import EventBus
from ..store import Database
from .contracts import RuntimeEvent


class EventOutbox:
    """Durable event log with a publish cursor. Owns one table."""

    def __init__(self, db: Database, bus: EventBus | None = None) -> None:
        self.db = db
        self.bus = bus

    # ------------------------------------------------------------------ write

    def stage(
        self,
        conn: Any,
        topic: str,
        *,
        task_id: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> RuntimeEvent:
        """Write an event **inside a caller's transaction**.

        Takes the connection rather than opening its own: the whole point is
        that the event and the fact it reports either both land or neither does.
        The sequence number is allocated here, under the same transaction, so two
        events for one task cannot come out with the same position.
        """
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS last FROM event_outbox WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        event = RuntimeEvent.new(topic, task_id, int(row["last"]) + 1, payload or {})
        conn.execute(
            "INSERT INTO event_outbox(event_id, topic, task_id, sequence, occurred_at,"
            " payload) VALUES (?,?,?,?,?,?)",
            (
                event.event_id,
                event.topic,
                event.task_id,
                event.sequence,
                event.occurred_at,
                json.dumps(dict(event.payload), ensure_ascii=False, default=repr),
            ),
        )
        return event

    def record(
        self,
        topic: str,
        *,
        task_id: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> RuntimeEvent:
        """Stage an event in a transaction of its own, for callers with no other
        write to make. Still committed before anything is published."""
        with self.db.transaction() as conn:
            return self.stage(conn, topic, task_id=task_id, payload=payload)

    # ---------------------------------------------------------------- publish

    def pending(self, limit: int = 200) -> list[RuntimeEvent]:
        rows = self.db.query(
            "SELECT * FROM event_outbox WHERE published_at IS NULL"
            " ORDER BY occurred_at, sequence LIMIT ?",
            (limit,),
        )
        return [_event(row) for row in rows]

    def drain(self, limit: int = 200) -> list[RuntimeEvent]:
        """Publish what has committed, then mark it published.

        In that order, deliberately. Marking first would turn a crash into a lost
        event — silence about something that really happened — and this system
        would rather say a thing twice than not at all.
        """
        events = self.pending(limit)
        if not events:
            return []
        for event in events:
            if self.bus is not None:
                # The topic travels as the bus's own argument; everything else,
                # including the event id a consumer deduplicates on, rides in
                # the payload.
                body = {k: v for k, v in event.to_dict().items() if k != "topic"}
                self.bus.emit(event.topic, **body)
        self.db.executemany(
            "UPDATE event_outbox SET published_at = ? WHERE event_id = ?",
            [(time.time(), event.event_id) for event in events],
        )
        return events

    # ------------------------------------------------------------------ reads

    def history(self, task_id: str) -> list[RuntimeEvent]:
        """Everything ever recorded for one task, in order. The audit answer to
        "what actually happened", independent of whatever the UI managed to
        receive."""
        return [
            _event(row)
            for row in self.db.query(
                "SELECT * FROM event_outbox WHERE task_id = ? ORDER BY sequence", (task_id,)
            )
        ]

    def prune(self, *, keep_after: float) -> int:
        """Drop published events older than a cutoff. Unpublished rows stay."""
        cursor = self.db.execute(
            "DELETE FROM event_outbox WHERE published_at IS NOT NULL AND occurred_at < ?",
            (keep_after,),
        )
        return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def _event(row: Any) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=row["event_id"],
        topic=row["topic"],
        task_id=row["task_id"] or "",
        sequence=int(row["sequence"]),
        occurred_at=row["occurred_at"],
        payload=json.loads(row["payload"] or "{}"),
    )


def deduplicate(events: Sequence[RuntimeEvent]) -> list[RuntimeEvent]:
    """What a consumer does with at-least-once delivery. Here so consumers do not
    each invent their own version of it."""
    seen: set[str] = set()
    out: list[RuntimeEvent] = []
    for event in events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        out.append(event)
    return out


__all__ = ["EventOutbox", "deduplicate"]
