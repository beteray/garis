"""The gate between "GARIS noticed something" and "the user is interrupted".

Tools and subscriptions produce candidates; this decides which ones a person
actually hears, and when. Without it ``ctx.note()`` would be a direct line to the
user's attention, and an agent that watches everything would become the noisiest
thing on the machine.

Four rules, in order of how often they fire:

1. **Importance floor.** Below the configured threshold, silence.
2. **Quiet hours.** Held, not dropped — the user sees it in the morning.
3. **Gaming.** Same, but only critical gets through: an interruption mid-match is
   worse than a late message.
4. **Rate limit and duplicates.** Ten notices about one failing service is one
   notice.

Nothing here decides *what* is important — the producer states that. This layer
only decides whether now is the moment.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .config import NotificationsConfig
from .events import EventBus, Topic

# Importance, as producers declare it:
#   1 trivia          2 might matter      3 worth knowing
#   4 needs attention 5 critical (a failing disk, a blocked payment)
CRITICAL = 5
DEDUPE_WINDOW_SECONDS = 900.0


class Verdict(StrEnum):
    DELIVER = "deliver"
    HOLD = "hold"        # queued for later; the user will see it
    DROP = "drop"        # never shown


@dataclass(slots=True)
class Notice:
    message: str
    importance: int = 3
    source: str = ""
    task_id: str | None = None
    at: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Same message from the same source is the same notice."""
        raw = f"{self.source}|{self.message.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "importance": self.importance,
            "source": self.source,
            "task_id": self.task_id,
            "at": self.at,
            **self.data,
        }


@dataclass(slots=True)
class Decision:
    verdict: Verdict
    reason: str

    @property
    def delivered(self) -> bool:
        return self.verdict is Verdict.DELIVER


class NotificationGate:
    def __init__(
        self,
        config: NotificationsConfig,
        *,
        bus: EventBus | None = None,
        clock: Any = None,
    ) -> None:
        self.config = config
        self.bus = bus
        # Injectable so quiet-hours behaviour is testable without waiting for 23:00.
        self._now = clock or datetime.now
        self._recent: deque[tuple[float, str]] = deque(maxlen=64)
        self._delivered_at: deque[float] = deque(maxlen=256)
        self._held: list[Notice] = []
        self._gaming = False

    # ------------------------------------------------------------------- state

    def set_gaming(self, playing: bool) -> None:
        """Called by the presence watcher when a full-screen game takes over."""
        self._gaming = playing

    @property
    def gaming(self) -> bool:
        return self._gaming

    @property
    def held(self) -> list[Notice]:
        return list(self._held)

    # -------------------------------------------------------------------- gate

    def consider(self, notice: Notice) -> Decision:
        """Decide, record, and emit if the answer is yes."""
        decision = self._decide(notice)

        if decision.verdict is Verdict.DELIVER:
            self._recent.append((notice.at, notice.fingerprint()))
            self._delivered_at.append(notice.at)
            if self.bus is not None:
                self.bus.emit(Topic.SPEAK, **notice.to_dict(), reason=decision.reason)
        elif decision.verdict is Verdict.HOLD:
            self._held.append(notice)
            # Duplicates are suppressed while held too, or a service failing every
            # minute during quiet hours becomes 480 morning notifications.
            self._recent.append((notice.at, notice.fingerprint()))

        return decision

    def _decide(self, notice: Notice) -> Decision:
        if self._is_duplicate(notice):
            return Decision(Verdict.DROP, "duplikat")

        if notice.importance < self.config.min_importance:
            return Decision(Verdict.DROP, "poniżej progu ważności")

        # Critical outranks the clock: a failing disk at 03:00 is worth waking for.
        if notice.importance >= CRITICAL:
            return Decision(Verdict.DELIVER, "krytyczne")

        if self._in_quiet_hours(notice.at):
            return Decision(Verdict.HOLD, "godziny ciszy")

        if self._gaming and self.config.suppress_while_gaming:
            return Decision(Verdict.HOLD, "gra")

        if self._over_rate_limit(notice.at):
            return Decision(Verdict.HOLD, "limit powiadomień na godzinę")

        return Decision(Verdict.DELIVER, "przepuszczone")

    # ---------------------------------------------------------------- releasing

    def release_held(self, *, limit: int = 20) -> list[Notice]:
        """Hand back what was held, most important first.

        Called when quiet hours end, when the game closes, or when the user asks
        what they missed. Held notices are delivered as a batch, not replayed one
        by one — the point was to not interrupt.
        """
        ordered = sorted(self._held, key=lambda n: (-n.importance, n.at))[:limit]
        self._held = [n for n in self._held if n not in ordered]
        now = time.time()
        for notice in ordered:
            self._delivered_at.append(now)
            if self.bus is not None:
                self.bus.emit(Topic.SPEAK, **notice.to_dict(), reason="zaległe")
        return ordered

    def drop_held(self) -> int:
        count = len(self._held)
        self._held.clear()
        return count

    # ------------------------------------------------------------------ checks

    def _is_duplicate(self, notice: Notice) -> bool:
        fingerprint = notice.fingerprint()
        cutoff = notice.at - DEDUPE_WINDOW_SECONDS
        return any(at >= cutoff and seen == fingerprint for at, seen in self._recent)

    def _in_quiet_hours(self, moment: float) -> bool:
        when = self._now()
        if not isinstance(when, datetime):  # pragma: no cover - defensive
            when = datetime.fromtimestamp(moment)
        return self.config.quiet_hours.contains(when.time())

    def _over_rate_limit(self, moment: float) -> bool:
        cap = self.config.max_per_hour
        if cap <= 0:
            return False
        hour_ago = moment - 3600
        return sum(1 for at in self._delivered_at if at >= hour_ago) >= cap

    # ------------------------------------------------------------------ wiring

    def listen(self, bus: EventBus) -> Any:
        """Subscribe to ``notice`` events and gate them into ``speak``.

        This is what makes ``ctx.note()`` honest: tools emit a candidate, and what
        reaches the user is whatever survives this.
        """
        subscription = bus.subscribe(Topic.NOTICE)

        async def pump() -> None:
            async for event in subscription:
                payload = dict(event.payload)
                if payload.pop("silent", False):
                    continue
                self.consider(
                    Notice(
                        message=str(payload.pop("message", "")),
                        importance=int(payload.pop("importance", 3)),
                        source=str(payload.pop("source", "")),
                        task_id=payload.pop("task_id", None),
                        at=event.at,
                        data=payload,
                    )
                )

        return pump, subscription

    def describe(self) -> dict[str, Any]:
        return {
            "min_importance": self.config.min_importance,
            "quiet_hours": {
                "enabled": self.config.quiet_hours.enabled,
                "start": self.config.quiet_hours.start,
                "end": self.config.quiet_hours.end,
                "active_now": self._in_quiet_hours(time.time()),
            },
            "gaming": self._gaming,
            "suppress_while_gaming": self.config.suppress_while_gaming,
            "max_per_hour": self.config.max_per_hour,
            "held": len(self._held),
        }


__all__ = ["CRITICAL", "Decision", "Notice", "NotificationGate", "Verdict"]
