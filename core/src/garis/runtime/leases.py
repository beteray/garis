"""Resource leases.

Tasks run concurrently and are equally important by default, so when two of them
want the same file, program or device, GARIS orders them itself instead of asking
the user to sequence their own work.

Keys are opaque strings with a convention: ``file:C:\\x\\y``, ``app:notepad``,
``device:microphone``, ``host:server-1``, ``registry:HKLM\\...``. Shared leases
let many readers proceed together; an exclusive lease waits for all of them.

Deadlock is prevented structurally: a lease request sorts its keys, so two tasks
grabbing {A, B} and {B, A} acquire in the same order and cannot hold-and-wait.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from ..errors import LeaseTimeout


class LeaseMode(StrEnum):
    SHARED = "shared"        # concurrent reads
    EXCLUSIVE = "exclusive"  # sole writer


@dataclass
class _KeyState:
    readers: int = 0
    writer: str | None = None
    waiters: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self.waiters.set()  # "something changed" signal; set == recheck now

    @property
    def free(self) -> bool:
        return self.readers == 0 and self.writer is None


@dataclass
class Lease:
    keys: tuple[str, ...]
    mode: LeaseMode
    holder: str
    acquired_at: float
    _manager: LeaseManager | None = None
    _released: bool = False

    def release(self) -> None:
        if self._released or self._manager is None:
            return
        self._released = True
        self._manager._release(self.keys, self.mode, self.holder)

    async def __aenter__(self) -> Lease:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()


class LeaseManager:
    """In-process lease table.

    Deliberately not persisted: leases describe live contention between running
    tasks. After a restart nothing is running, so a stale lease would only be a
    lie that blocks recovery.
    """

    def __init__(self) -> None:
        self._keys: dict[str, _KeyState] = {}
        self._lock = asyncio.Lock()

    def _state(self, key: str) -> _KeyState:
        state = self._keys.get(key)
        if state is None:
            state = _KeyState()
            self._keys[key] = state
        return state

    def _can_take(self, key: str, mode: LeaseMode, holder: str) -> bool:
        state = self._keys.get(key)
        if state is None:
            return True
        if state.writer is not None and state.writer != holder:
            return False
        if mode is LeaseMode.EXCLUSIVE and state.readers > 0:
            return False
        return True

    def _take(self, key: str, mode: LeaseMode, holder: str) -> None:
        state = self._state(key)
        if mode is LeaseMode.EXCLUSIVE:
            state.writer = holder
        else:
            state.readers += 1

    def _give_back(self, key: str, mode: LeaseMode, holder: str) -> None:
        state = self._keys.get(key)
        if state is None:
            return
        if mode is LeaseMode.EXCLUSIVE:
            if state.writer == holder:
                state.writer = None
        elif state.readers > 0:
            state.readers -= 1
        state.waiters.set()
        if state.free:
            self._keys.pop(key, None)

    async def acquire(
        self,
        keys: Iterable[str],
        *,
        holder: str,
        mode: LeaseMode = LeaseMode.EXCLUSIVE,
        timeout: float | None = 300.0,
    ) -> Lease:
        ordered = tuple(sorted(set(k for k in keys if k)))
        if not ordered:
            return Lease((), mode, holder, time.time(), self)

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            async with self._lock:
                if all(self._can_take(k, mode, holder) for k in ordered):
                    for key in ordered:
                        self._take(key, mode, holder)
                    return Lease(ordered, mode, holder, time.time(), self)
                # Snapshot the change-signals of everything we are blocked on.
                blockers = [
                    self._state(k) for k in ordered if not self._can_take(k, mode, holder)
                ]
                for state in blockers:
                    state.waiters.clear()
                events = [state.waiters.wait() for state in blockers]

            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise LeaseTimeout(
                    f"Zasoby zajęte zbyt długo: {', '.join(ordered)}",
                )
            try:
                await asyncio.wait_for(
                    asyncio.wait(
                        [asyncio.ensure_future(e) for e in events],
                        return_when=asyncio.FIRST_COMPLETED,
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                raise LeaseTimeout(
                    f"Zasoby zajęte zbyt długo: {', '.join(ordered)}"
                ) from None

    def _release(self, keys: Sequence[str], mode: LeaseMode, holder: str) -> None:
        for key in keys:
            self._give_back(key, mode, holder)

    # --- introspection, surfaced in diagnostics ---

    def held(self) -> dict[str, dict[str, object]]:
        return {
            key: {"readers": state.readers, "writer": state.writer}
            for key, state in self._keys.items()
        }

    def is_busy(self, key: str) -> bool:
        state = self._keys.get(key)
        return state is not None and not state.free


def file_key(path: str) -> str:
    """Normalise a path into a lease key.

    Windows paths are case-insensitive, so ``C:\\Temp\\a`` and ``c:\\temp\\a`` must
    map to the same key or two tasks would happily corrupt one file.
    """
    import os

    normalised = os.path.normcase(os.path.abspath(os.path.expanduser(path)))
    return f"file:{normalised}"


def app_key(name: str) -> str:
    return f"app:{name.strip().lower()}"


def device_key(name: str) -> str:
    return f"device:{name.strip().lower()}"


def host_key(name: str) -> str:
    return f"host:{name.strip().lower()}"


__all__ = [
    "Lease",
    "LeaseManager",
    "LeaseMode",
    "app_key",
    "device_key",
    "file_key",
    "host_key",
]
