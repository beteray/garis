"""In-process event bus.

Everything observable flows through here: task progress, runtime decisions,
voice state, proactive findings. The UI, the voice layer and the audit log are
all just subscribers, which is what keeps "closing the window doesn't stop
GARIS" true — the work never depends on anyone listening.

Subscribers are slow-consumer safe: a full queue drops its oldest event rather
than blocking the producer.
"""

from __future__ import annotations

import asyncio
import fnmatch
import time
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_QUEUE_SIZE = 512
DEFAULT_HISTORY = 500


@dataclass(frozen=True, slots=True)
class Event:
    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def matches(self, pattern: str) -> bool:
        return self.topic == pattern or fnmatch.fnmatchcase(self.topic, pattern)

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "at": self.at, **self.payload}


class Subscription:
    """Async iterator over matching events. Use as an async context manager."""

    def __init__(self, bus: EventBus, patterns: tuple[str, ...], queue_size: int) -> None:
        self._bus = bus
        self._patterns = patterns or ("*",)
        self._queue: deque[Event] = deque(maxlen=queue_size)
        self._wakeup = asyncio.Event()
        self._closed = False
        self.dropped = 0

    # --- producer side ---
    def _offer(self, event: Event) -> None:
        if self._closed:
            return
        if not any(event.matches(p) for p in self._patterns):
            return
        if self._queue.maxlen is not None and len(self._queue) == self._queue.maxlen:
            self.dropped += 1
        self._queue.append(event)
        self._wakeup.set()

    # --- consumer side ---
    async def get(self) -> Event:
        while True:
            if self._queue:
                return self._queue.popleft()
            if self._closed:
                raise StopAsyncIteration
            self._wakeup.clear()
            await self._wakeup.wait()

    def __aiter__(self) -> AsyncIterator[Event]:
        return self

    async def __anext__(self) -> Event:
        try:
            return await self.get()
        except StopAsyncIteration:
            raise StopAsyncIteration from None

    def close(self) -> None:
        self._closed = True
        self._wakeup.set()
        self._bus._remove(self)

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


class EventBus:
    def __init__(self, history: int = DEFAULT_HISTORY) -> None:
        self._subs: list[Subscription] = []
        self._history: deque[Event] = deque(maxlen=history)

    def emit(self, topic: str, **payload: Any) -> Event:
        """Publish an event. Safe from sync code — never blocks, never awaits."""
        event = Event(topic=topic, payload=payload)
        self._history.append(event)
        for sub in list(self._subs):
            sub._offer(event)
        return event

    def subscribe(
        self, *patterns: str, queue_size: int = DEFAULT_QUEUE_SIZE
    ) -> Subscription:
        """Subscribe to topics. Patterns are glob-style: ``task.*``, ``*``."""
        sub = Subscription(self, tuple(patterns), queue_size)
        self._subs.append(sub)
        return sub

    def recent(self, *patterns: str, limit: int = 50) -> list[Event]:
        pats: Iterable[str] = patterns or ("*",)
        found = [e for e in self._history if any(e.matches(p) for p in pats)]
        return found[-limit:]

    def _remove(self, sub: Subscription) -> None:
        try:
            self._subs.remove(sub)
        except ValueError:
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


# Canonical topics. Kept as constants so the UI protocol and the audit log
# cannot drift apart from the producers.
class Topic:
    TASK_CREATED = "task.created"
    TASK_STARTED = "task.started"
    TASK_STEP = "task.step"
    TASK_PROGRESS = "task.progress"
    TASK_FINISHED = "task.finished"
    TASK_FAILED = "task.failed"
    TASK_BLOCKED = "task.blocked"
    TASK_STOPPED = "task.stopped"

    ACTION_STARTED = "action.started"
    ACTION_FINISHED = "action.finished"
    ACTION_FAILED = "action.failed"
    ACTION_DENIED = "action.denied"

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"

    NOTICE = "notice"                 # something the user may want to hear
    SPEAK = "speak"                   # text handed to the voice layer

    VOICE_STATE = "voice.state"
    AGENT_STATE = "agent.state"       # drives the animated orb

    MEMORY_CHANGED = "memory.changed"
    SUBSCRIPTION_ITEM = "subscription.item"

    CONFIG_CHANGED = "config.changed"       # settings changed, with the paths
    VAULT_CHANGED = "vault.changed"         # a credential appeared, changed or went
    PROVIDER_HEALTH = "provider.health"     # a provider's state actually moved


__all__ = ["DEFAULT_HISTORY", "DEFAULT_QUEUE_SIZE", "Event", "EventBus", "Subscription", "Topic"]
